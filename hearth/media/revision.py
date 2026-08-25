"""Cross-machine revision authority for clip promotion.

THE PROBLEM
-----------
Once rendering is asynchronous, a clip can be re-cut while a render for the
previous cut is still running. AM4's analysis stage extends a clip across a
segment boundary, bumps its revision, and submits a new render. The old render
is now stale -- but it is still encoding, and it will finish.

Passing ``clip_revision`` inside the render request does not solve this. That
field records what the job *started* with; it says nothing about what AM4 did
afterwards. A job cannot know it has been superseded by looking at its own
arguments.

THE RULE
--------
**AM4 owns the revision. HEARTH reads it and must fail closed.**

AM4 writes ``work/<session>/<clip>.revision.json`` atomically *before*
submitting any render request for that revision, so the authority always exists
before a job that references it. HEARTH re-reads that file at *commit time* --
immediately before ``os.replace`` -- and promotes only on an exact match.

Both the read and the replace happen inside a per-clip promotion lease
(``promote:<clip_id>``, limit 1) taken from the existing CapacityLeaseStore, so
two jobs for the same clip can never interleave their promotions.

WHAT THIS DELIBERATELY DOES NOT DEPEND ON
-----------------------------------------
Cancellation. ``cancel_execution`` is cooperative (ADR-0030) and cannot reliably
stop an ffmpeg that is already running. It remains a useful optimisation -- it
saves encode time -- but correctness here rests entirely on the commit-time
check, so a cancellation that is lost, ignored, or arrives too late changes
nothing about the outcome.

RESIDUAL RACE -- BOUNDED, OBSERVABLE, RECOVERABLE. NOT ELIMINATED.
------------------------------------------------------------------
A read-then-replace window remains; the lease bounds it rather than removing it.
The dangerous interleaving is prevented, because a newer job cannot hold the
lease concurrently and re-reads the authority when it acquires it, so it
promotes last. The case that does not self-correct is "stale job promoted **and**
the newer job then failed": it requires a revision bump inside one job's
promotion window plus a failure of its successor, it is visible in the ledger
(the receipt records a promotion against a revision the authority no longer
names), and it is repaired by a retry from immutable raw. No interleaving loses
footage; the worst outcome is a draft one revision behind, flagged as failed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from hearth.toolsurface._media_scope import MediaPathError, resolve_media

SCHEMA_VERSION = 1

# Scope prefix for the per-clip promotion lease. One clip, one promoter.
PROMOTION_SCOPE_PREFIX = "promote"
PROMOTION_LEASE_TTL_S = 120.0

# Outcome reasons recorded on the result artifact. These are the vocabulary the
# review UI and the ledger use to explain why a render did not become a draft.
REASON_PROMOTED = "promoted"
REASON_SUPERSEDED = "superseded_by_revision"
REASON_AUTHORITY_UNAVAILABLE = "revision_authority_unavailable"
REASON_PROMOTION_FAILED = "promotion_failed"

# Suffix for a displaced draft during a multi-variant promotion.
#
# `os.replace` is atomic PER FILE and not across files, so promoting a two
# variant set is NOT physically transactional however it is written. The
# invariant that actually matters is narrower and achievable: a clip must never
# end up with horizontal at revision N+1 and vertical still at revision N.
#
# So the previous set is moved aside under this suffix, the new set is installed,
# and only then are the displaced files removed. The presence of ANY rollback
# file is therefore proof that a promotion did not finish -- which is what makes
# process death inside the promotion window recoverable rather than silent.
ROLLBACK_SUFFIX = ".rollback-"


class AuthorityUnavailable(RuntimeError):
    """The authoritative revision could not be established. Never promote."""


@dataclass(frozen=True)
class PromotionOutcome:
    """What happened at commit time, and why."""

    promoted: bool
    reason: str
    job_revision: int
    authoritative_revision: Optional[int]
    destination: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "promoted": self.promoted,
            "reason": self.reason,
            "job_revision": self.job_revision,
            "authoritative_revision": self.authoritative_revision,
            "destination": self.destination,
            "detail": self.detail,
        }


def authority_relative_path(session_id: str, clip_id: str) -> str:
    """Media-relative location of the authority record."""
    return "work/%s/%s.revision.json" % (session_id, clip_id)


def write_authority(session_id: str, clip_id: str, revision: int,
                    updated_at: str, root: Optional[Path] = None) -> Path:
    """Write the authority record atomically.

    In production AM4 writes this; HEARTH only reads it. This helper exists so
    the dispatcher's test doubles and the BF6 worker share one spelling of the
    file, and so the atomic-write discipline is defined in exactly one place.
    """
    if not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be a non-negative int")
    target = resolve_media(
        authority_relative_path(session_id, clip_id), mode="write", root=root
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "clip_id": clip_id,
        "clip_revision": revision,
        "updated_at": updated_at,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_authority(session_id: str, clip_id: str, root: Optional[Path] = None) -> int:
    """Read the authoritative revision, or raise.

    Every failure mode raises rather than returning a default. A default here
    would be a silent decision to promote against unknown authority, which is
    precisely the outcome this module exists to prevent.
    """
    try:
        path = resolve_media(
            authority_relative_path(session_id, clip_id), mode="read", root=root
        )
    except MediaPathError as exc:
        raise AuthorityUnavailable("authority path refused: %s" % (exc,)) from exc

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        # Includes the share being unavailable. Unreadable is not "unchanged".
        raise AuthorityUnavailable(
            "authority record unreadable at %s: %s" % (path, exc)
        ) from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthorityUnavailable(
            "authority record at %s is not valid JSON: %s" % (path, exc)
        ) from exc

    if not isinstance(document, dict):
        raise AuthorityUnavailable("authority record at %s is not an object" % (path,))

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        # A schema we do not understand is not authority we can act on.
        raise AuthorityUnavailable(
            "authority record at %s has schema_version %r; expected %d"
            % (path, version, SCHEMA_VERSION)
        )

    if document.get("clip_id") != clip_id:
        raise AuthorityUnavailable(
            "authority record at %s names clip %r, not %r"
            % (path, document.get("clip_id"), clip_id)
        )

    revision = document.get("clip_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise AuthorityUnavailable(
            "authority record at %s has a bad clip_revision: %r" % (path, revision)
        )
    return revision


def promotion_scope(clip_id: str) -> str:
    """Lease scope serialising promotion of one clip."""
    return "%s:%s" % (PROMOTION_SCOPE_PREFIX, clip_id)


def promote_if_current(
    *,
    leases,
    session_id: str,
    clip_id: str,
    job_revision: int,
    job_id: str,
    invocation_id: str,
    staged: Path,
    destination: Path,
    root: Optional[Path] = None,
    ttl_seconds: float = PROMOTION_LEASE_TTL_S,
) -> PromotionOutcome:
    """Commit a validated render, but only if its revision is still current.

    ``staged`` is the validated ``.part`` file; ``destination`` is the draft it
    would become. The caller must have already run ffprobe validation -- this
    function decides *whether to publish*, not *whether the file is good*.

    A superseded or unverifiable job removes its own staged file and reports
    why. The existing draft, if any, is left exactly as it was.
    """
    scope = promotion_scope(clip_id)
    lease_id = leases.acquire(
        scope=scope,
        job_id=job_id,
        invocation_id=invocation_id,
        limit=1,
        ttl_seconds=ttl_seconds,
    )
    try:
        # Re-read INSIDE the lease. Reading before acquiring would reintroduce
        # exactly the window the lease exists to close.
        try:
            authoritative = read_authority(session_id, clip_id, root=root)
        except AuthorityUnavailable:
            _discard(staged)
            return PromotionOutcome(
                promoted=False,
                reason=REASON_AUTHORITY_UNAVAILABLE,
                job_revision=job_revision,
                authoritative_revision=None,
            )

        if authoritative != job_revision:
            _discard(staged)
            return PromotionOutcome(
                promoted=False,
                reason=REASON_SUPERSEDED,
                job_revision=job_revision,
                authoritative_revision=authoritative,
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, destination)
        return PromotionOutcome(
            promoted=True,
            reason=REASON_PROMOTED,
            job_revision=job_revision,
            authoritative_revision=authoritative,
            destination=str(destination),
        )
    finally:
        leases.release(lease_id)


def rollback_path(destination: Path, job_id: str) -> Path:
    """Where a displaced draft waits while its replacement is installed."""
    return destination.with_name(destination.name + ROLLBACK_SUFFIX + job_id)


def promote_set(
    *,
    leases,
    session_id: str,
    clip_id: str,
    job_revision: int,
    job_id: str,
    invocation_id: str,
    staged: Mapping,
    root: Optional[Path] = None,
    ttl_seconds: float = PROMOTION_LEASE_TTL_S,
    replace: Optional[Callable] = None,
) -> PromotionOutcome:
    """Promote a COMPLETE variant set, or none of it.

    ``staged`` maps variant name -> ``(staged_path, destination_path)``. Every
    entry must already have passed validation; this function decides whether to
    publish, not whether the files are good.

    THE DOMAIN INVARIANT
    --------------------
        A successful render job promotes the complete requested variant set.
        A failed render job promotes none of that attempt's variants.

    Without this, a clip whose horizontal failed validation while its vertical
    passed leaves the drafts tree holding two different revisions of the same
    clip -- a state nothing downstream is written to understand.

    HOW, GIVEN os.replace IS ONLY PER-FILE ATOMIC
    ---------------------------------------------
    1. take ``promote:<clip_id>`` so two jobs for one clip cannot interleave;
    2. re-read the authoritative revision INSIDE the lease and fail closed;
    3. move every existing destination aside to a job-owned rollback path;
    4. install the new set;
    5. on ANY failure, delete what was installed and restore what was moved;
    6. only once the whole set is installed, delete the rollback files.

    Step 6 last is what makes recovery possible: a surviving rollback file means
    the promotion did not complete. See ``recover_incomplete_promotions``.

    ``replace`` is injectable so tests can fail the SECOND filesystem operation
    and prove the previous set is restored.
    """
    mover = replace or os.replace
    entries = list(staged.items())
    scope = promotion_scope(clip_id)
    lease_id = leases.acquire(
        scope=scope, job_id=job_id, invocation_id=invocation_id,
        limit=1, ttl_seconds=ttl_seconds,
    )
    try:
        try:
            authoritative = read_authority(session_id, clip_id, root=root)
        except AuthorityUnavailable:
            _discard_all(entries)
            return PromotionOutcome(False, REASON_AUTHORITY_UNAVAILABLE,
                                    job_revision, None)

        if authoritative != job_revision:
            # Superseded: the whole staged set is stale, so the whole set goes.
            _discard_all(entries)
            return PromotionOutcome(False, REASON_SUPERSEDED, job_revision,
                                    authoritative)

        displaced: list = []
        installed: list = []
        try:
            for _variant, (_source, destination) in entries:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    saved = rollback_path(destination, job_id)
                    mover(destination, saved)
                    displaced.append((destination, saved))
            for _variant, (source, destination) in entries:
                mover(source, destination)
                installed.append(destination)
        except Exception as exc:
            for destination in installed:
                try:
                    destination.unlink()
                except OSError:
                    pass
            for destination, saved in displaced:
                try:
                    mover(saved, destination)
                except OSError:
                    pass
            _discard_all(entries)
            return PromotionOutcome(
                False, REASON_PROMOTION_FAILED, job_revision, authoritative,
                detail="promotion failed and the previous set was restored: %s" % (exc,),
            )

        for _destination, saved in displaced:
            try:
                saved.unlink()
            except OSError:
                pass
        return PromotionOutcome(
            True, REASON_PROMOTED, job_revision, authoritative,
            destination=", ".join(str(d) for _v, (_s, d) in entries),
        )
    finally:
        leases.release(lease_id)


def recover_incomplete_promotions(directory: Path) -> list:
    """Restore drafts displaced by a promotion that never finished.

    A rollback file exists only between "moved the old set aside" and "installed
    the whole new set". If the process died in that window, the previous draft
    is sitting under the rollback name and the destination may be missing or
    half-new. Restoring returns the clip to its last complete state.

    Reverting rather than completing is deliberate: the staged replacements
    belong to a job that is no longer running, and a clip at its previous
    revision is a valid state, whereas a half-installed pair is not.
    """
    restored = []
    try:
        candidates = list(Path(directory).glob("*" + ROLLBACK_SUFFIX + "*"))
    except OSError:
        return restored
    for saved in candidates:
        name = saved.name
        index = name.rfind(ROLLBACK_SUFFIX)
        if index <= 0:
            continue
        destination = saved.with_name(name[:index])
        try:
            os.replace(saved, destination)
            restored.append(str(destination))
        except OSError:
            continue
    return restored


def _discard_all(entries) -> None:
    for _variant, (source, _destination) in entries:
        _discard(source)


def _discard(staged: Path) -> None:
    """Remove a staged render that will not be published.

    Best-effort: a leftover .part is untidy, but failing to delete it must never
    turn a correct refusal-to-promote into an error.
    """
    try:
        staged.unlink()
    except OSError:
        pass
