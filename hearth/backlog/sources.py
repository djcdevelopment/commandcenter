"""The three backlog sources: authored files, promoted refined intents, priced candidates.

Each source is a generator over ``Brief``s plus an explicit account of what it
did NOT yield. Nothing here is silent: a brief that cannot be parsed, a refine
intent with no promote sidecar, a half-written sidecar — each lands in
``SourceScan.rejected`` with a reason, so ``python -m hearth.backlog list`` (and
the drain's tick detail) can show an operator why a backlog looks empty.

Every entry point takes its root/path EXPLICITLY. The module-level
``DEFAULT_*`` constants name the ``hearth/var/...`` locations, but no function
falls back to them and no function creates a directory it was merely asked to
read — so importing this module, scanning an absent backlog, or running the
whole test suite never brings ``hearth/var`` into existence.

Source-specific notes:

**authored** — ``hearth/var/backlog/queued/*.md``, oldest mtime first, ties by
filename. The file IS the brief (``briefs.render()`` output). ``mark_dispatched``
/ ``mark_done`` move it through ``dispatched/`` and ``done/`` with ``os.replace``
(atomic on Windows and POSIX) and are idempotent: moving an already-moved file
reports ``already_moved`` and touches nothing.

**refined** — a commander refine result (``<intent_id>.json``) is a backlog item
only once a human writes the sidecar ``<intent_id>.promote.json``. Refining is
thinking; promoting is the decision to spend the fleet on it, and it is a
separate, dated, attributed act.

**candidate** — the priced experiment candidates in
``knowledge/candidate_worth.json``, minus the ones already run. THE BUG THIS
FIXES: the drain compared ``candidate_id`` against ``candidate_id`` on
``knowledge/experiment_results.json`` rows, but those rows are
``experiment-result.v1`` and carry ``experiment_id`` — no ``candidate_id`` key
exists on them, so the "already run" set was always empty and a completed
candidate could be dispatched forever. ``already_run_ids`` now honours three
fields (see its docstring).
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hearth.backlog.briefs import Brief, safe_slug  # noqa: E402
from hearth.backlog.briefs import parse as parse_brief  # noqa: E402
from hearth.toolsurface.task_expectations import (  # noqa: E402
    validate_max_age_s,
    validate_requires,
)

# --- Default locations (named, never silently used) ---------------------------
DEFAULT_BACKLOG_ROOT = _REPO_ROOT / "hearth" / "var" / "backlog"
DEFAULT_QUEUED_DIR = DEFAULT_BACKLOG_ROOT / "queued"
DEFAULT_DISPATCHED_DIR = DEFAULT_BACKLOG_ROOT / "dispatched"
DEFAULT_DONE_DIR = DEFAULT_BACKLOG_ROOT / "done"
DEFAULT_REFINE_DIR = _REPO_ROOT / "hearth" / "var" / "commander" / "refine"
DEFAULT_CANDIDATE_WORTH_PATH = _REPO_ROOT / "knowledge" / "candidate_worth.json"
DEFAULT_EXPERIMENT_RESULTS_PATH = _REPO_ROOT / "knowledge" / "experiment_results.json"

# The promote sidecar contract. A new key is a new version, never a silent add.
PROMOTE_CONTRACT = "backlog-promote.v1"
PROMOTE_SUFFIX = ".promote.json"

# Candidate dispatches are PROOFING runs (retests/experiments on sunk idle
# compute), not production build work. Defined HERE and re-exported by
# fleet.bankedfire_drain so there is exactly one definition.
CANDIDATE_TASK_CLASS = "proofing"

_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SourceScan:
    """What a source yielded, and what it deliberately did not.

    ``rejected`` is "considered but not yielded, with a reason" — it holds both
    faults (an unparseable brief) and ordinary non-membership (a refine intent
    nobody promoted). The ``reason`` distinguishes them; the point is that a
    caller can always answer "why is the backlog empty?" without guessing.
    """

    briefs: tuple[Brief, ...] = ()
    rejected: tuple[dict, ...] = field(default=())

    def __iter__(self) -> Iterator[Brief]:
        return iter(self.briefs)

    def __len__(self) -> int:
        return len(self.briefs)


# --- shared helpers -----------------------------------------------------------

def _load_json(path: Path, default: dict) -> dict:
    """Read a JSON object, defaulting on absence/corruption. Never raises."""
    path = Path(path)
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default
    return data if isinstance(data, dict) else default


def _atomic_write_text(path: Path, text: str,
                       replace_fn: Optional[Callable[[str, str], None]] = None) -> Path:
    """Temp file in the same directory, then ``os.replace``.

    ``replace_fn`` is injectable (looked up at call time) so a test can crash
    between the two and prove the previous file is byte-intact — the same
    idiom ``task_expectations.save_expectations`` already uses.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    fn = replace_fn or os.replace
    try:
        fn(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def _confined(path: Path, root: Path) -> Path:
    """Return ``path`` resolved, or raise if it escapes ``root``.

    File moves are confined to the backlog root by construction: a brief whose
    ``source_ref`` carries ``..`` or an absolute path must not be able to move
    an arbitrary file on this machine into (or out of) the backlog.
    """
    root_resolved = Path(root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root_resolved / target
    resolved = target.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"path escapes the backlog root: {path!r} (root {root_resolved})")
    return resolved


# --- authored -----------------------------------------------------------------

def authored_source(queued_dir) -> SourceScan:
    """Briefs from ``<queued_dir>/*.md``, oldest mtime first, ties by filename.

    Oldest-first is FIFO fairness: an authored brief that has been waiting must
    not be starved by a newer one. mtime is the queue timestamp because the file
    is written once and never edited in place; the filename tie-break makes the
    order total (two files can share an mtime — the FS clock granularity is
    coarser than a loop that writes both).

    A file that vanishes between the listing and the read, or fails ``parse()``,
    is skipped and reported. Neither crashes the scan: an operator's typo in one
    brief must not stop the whole backlog.
    """
    directory = Path(queued_dir)
    briefs: list[Brief] = []
    rejected: list[dict] = []
    try:
        entries = sorted(directory.glob("*.md"))
    except OSError as exc:
        return SourceScan((), ({"source_ref": str(directory),
                                "reason": f"queued dir unreadable: {type(exc).__name__}: {exc}"},))

    stamped: list[tuple[float, str, Path]] = []
    for path in entries:
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            # Vanished between listing and stat -- absence, not a fault to raise.
            rejected.append({"source_ref": path.name,
                             "reason": f"unreadable: {type(exc).__name__}: {exc}"})
            continue
        stamped.append((mtime, path.name, path))
    stamped.sort(key=lambda row: (row[0], row[1]))

    for _mtime, name, path in stamped:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            rejected.append({"source_ref": name,
                             "reason": f"unreadable: {type(exc).__name__}: {exc}"})
            continue
        try:
            briefs.append(parse_brief(text, source="authored", source_ref=name,
                                      slug=safe_slug(Path(name).stem)))
        except ValueError as exc:
            rejected.append({"source_ref": name, "reason": f"unparseable: {exc}"})
    return SourceScan(tuple(briefs), tuple(rejected))


def _move(src: Path, dst: Path, replace_fn: Optional[Callable[[str, str], None]] = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    (replace_fn or os.replace)(str(src), str(dst))


def mark_dispatched(brief: Brief, plan_id: str, queued_dir, dispatched_dir,
                    replace_fn: Optional[Callable[[str, str], None]] = None) -> dict:
    """Move ``queued/<brief.source_ref>`` to ``dispatched/<plan_id>.md``, atomically.

    IDEMPOTENT: if the source file is already gone and the target already
    exists, this is a no-op reporting ``already_moved`` — so a crash between the
    move and the caller's bookkeeping replays safely, and exactly one logical
    move ever happens.

    Both ends are confined to their roots; ``plan_id`` is charset-checked
    because it becomes a filename.
    """
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.match(plan_id):
        raise ValueError(f"plan_id must match [A-Za-z0-9._-]+ ; got {plan_id!r}")
    src = _confined(brief.source_ref, queued_dir)
    dst = _confined(f"{plan_id}.md", dispatched_dir)
    if not src.exists():
        if dst.exists():
            return {"moved": False, "status": "already_moved",
                    "src": str(src), "dst": str(dst)}
        return {"moved": False, "status": "missing", "src": str(src), "dst": str(dst)}
    _move(src, dst, replace_fn)
    return {"moved": True, "status": "moved", "src": str(src), "dst": str(dst)}


def mark_done(plan_id: str, dispatched_dir, done_dir,
              replace_fn: Optional[Callable[[str, str], None]] = None) -> dict:
    """Move ``dispatched/<plan_id>.md`` to ``done/<plan_id>.md``. Idempotent."""
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.match(plan_id):
        raise ValueError(f"plan_id must match [A-Za-z0-9._-]+ ; got {plan_id!r}")
    src = _confined(f"{plan_id}.md", dispatched_dir)
    dst = _confined(f"{plan_id}.md", done_dir)
    if not src.exists():
        if dst.exists():
            return {"moved": False, "status": "already_moved",
                    "src": str(src), "dst": str(dst)}
        return {"moved": False, "status": "missing", "src": str(src), "dst": str(dst)}
    _move(src, dst, replace_fn)
    return {"moved": True, "status": "moved", "src": str(src), "dst": str(dst)}


# --- refined ------------------------------------------------------------------

def validate_promote_sidecar(doc: object) -> dict:
    """Return the sidecar normalised, or raise ValueError naming the violation."""
    if not isinstance(doc, dict):
        raise ValueError(f"promote sidecar must be a JSON object, got {type(doc).__name__}")
    if doc.get("contract") != PROMOTE_CONTRACT:
        raise ValueError(
            f"promote sidecar contract must be {PROMOTE_CONTRACT!r}; got {doc.get('contract')!r}")
    task_class = doc.get("task_class")
    if not isinstance(task_class, str) or not task_class.strip():
        raise ValueError("promote sidecar task_class must be a non-empty string")
    requires = validate_requires(doc.get("requires"), where="promote sidecar requires")
    if requires is None:
        raise ValueError("promote sidecar requires is mandatory (name the deliverables)")
    max_age_s = validate_max_age_s(doc.get("max_age_s"), where="promote sidecar max_age_s")
    builders = doc.get("builders")
    if builders is not None:
        if (not isinstance(builders, list) or not builders
                or not all(isinstance(b, str) and b.strip() for b in builders)):
            raise ValueError(
                "promote sidecar builders must be a non-empty list of non-empty strings")
    for key in ("promoted_by", "promoted_at"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise ValueError(f"promote sidecar {key} must be a non-empty string")
    return {
        "contract": PROMOTE_CONTRACT,
        "task_class": task_class,
        "requires": requires,
        "max_age_s": max_age_s,
        "builders": list(builders) if builders is not None else None,
        "promoted_by": doc["promoted_by"],
        "promoted_at": doc["promoted_at"],
    }


def _sidecar_bytes(sidecar: dict) -> str:
    """The one serialization of a sidecar. Deterministic, so byte-comparison of
    an unchanged re-promote is meaningful."""
    return json.dumps(sidecar, indent=2, sort_keys=True) + "\n"


def refined_source(refine_dir) -> SourceScan:
    """Briefs for each refine intent that carries a valid promote sidecar.

    Ordered by ``promoted_at`` (oldest decision first), ties by intent_id. The
    brief body is the refine result's ``final`` text — the thing the loop
    actually converged on, not the original idea.
    """
    directory = Path(refine_dir)
    briefs: list[Brief] = []
    rejected: list[dict] = []
    try:
        entries = sorted(p for p in directory.glob("*.json")
                         if not p.name.endswith(PROMOTE_SUFFIX))
    except OSError as exc:
        return SourceScan((), ({"source_ref": str(directory),
                                "reason": f"refine dir unreadable: {type(exc).__name__}: {exc}"},))

    rows: list[tuple[str, str, Brief]] = []
    for path in entries:
        intent_id = path.stem
        sidecar_path = path.with_name(f"{intent_id}{PROMOTE_SUFFIX}")
        if not sidecar_path.is_file():
            # Not a fault: refining is thinking, promoting is the decision.
            rejected.append({"source_ref": intent_id, "reason": "not-promoted"})
            continue
        try:
            sidecar = validate_promote_sidecar(
                json.loads(sidecar_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            rejected.append({"source_ref": intent_id,
                             "reason": f"invalid promote sidecar: {exc}"})
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            rejected.append({"source_ref": intent_id,
                             "reason": f"refine result unreadable: {exc}"})
            continue
        final = document.get("final") if isinstance(document, dict) else None
        if not isinstance(final, str) or not final.strip():
            rejected.append({"source_ref": intent_id,
                             "reason": "refine result has no 'final' text"})
            continue
        try:
            brief = Brief(
                slug=safe_slug(intent_id),
                title=(document.get("idea") or final).strip().splitlines()[0][:120],
                body=final,
                builders=tuple(sidecar["builders"]) if sidecar["builders"] else None,
                task_class=sidecar["task_class"],
                est_tokens=None,
                requires=tuple(sidecar["requires"]),
                max_age_s=sidecar["max_age_s"],
                source="refined",
                source_ref=intent_id,
            )
        except ValueError as exc:
            rejected.append({"source_ref": intent_id, "reason": f"invalid brief: {exc}"})
            continue
        rows.append((sidecar["promoted_at"], intent_id, brief))

    rows.sort(key=lambda row: (row[0], row[1]))
    briefs = [row[2] for row in rows]
    return SourceScan(tuple(briefs), tuple(rejected))


def promote_refine(intent_id: str, *, refine_dir, task_class: str,
                   requires, max_age_s=None, builders=None,
                   promoted_by: str = "derek", replace: bool = False,
                   now: Optional[str] = None,
                   replace_fn: Optional[Callable[[str, str], None]] = None) -> dict:
    """Write ``<intent_id>.promote.json`` — the act that makes a refine result a brief.

    IDEMPOTENT: re-running with identical arguments leaves the file
    BYTE-IDENTICAL (the original ``promoted_at`` is preserved, so the timestamp
    cannot drift a no-op into a rewrite) and reports ``unchanged``. Different
    arguments raise ValueError unless ``replace=True`` — a promotion is an
    authored decision, and silently overwriting one loses who decided what.
    """
    if not isinstance(intent_id, str) or not intent_id.strip():
        raise ValueError("intent_id must be a non-empty string")
    if "/" in intent_id or "\\" in intent_id or intent_id in (".", ".."):
        raise ValueError(f"invalid intent_id: {intent_id!r}")
    directory = Path(refine_dir)
    intent_path = directory / f"{intent_id}.json"
    if not intent_path.is_file():
        raise ValueError(f"no refine result found for {intent_id!r} in {directory}")
    sidecar_path = directory / f"{intent_id}{PROMOTE_SUFFIX}"

    stamp = now or _utc_now_iso()
    proposed = validate_promote_sidecar({
        "contract": PROMOTE_CONTRACT,
        "task_class": task_class,
        "requires": list(requires) if requires is not None else None,
        "max_age_s": max_age_s,
        "builders": list(builders) if builders else None,
        "promoted_by": promoted_by,
        "promoted_at": stamp,
    })

    if sidecar_path.is_file():
        try:
            existing = validate_promote_sidecar(
                json.loads(sidecar_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            if not replace:
                raise ValueError(
                    f"existing promote sidecar for {intent_id!r} is invalid ({exc}); "
                    f"re-run with --replace to overwrite it") from exc
            existing = None
        if existing is not None:
            same = all(existing[k] == proposed[k] for k in
                       ("contract", "task_class", "requires", "max_age_s",
                        "builders", "promoted_by"))
            if same:
                # Keep the ORIGINAL promoted_at and do not touch the file: an
                # unchanged re-promote must be byte-identical, not merely equal.
                return {"status": "unchanged", "path": str(sidecar_path),
                        "sidecar": existing}
            if not replace:
                raise ValueError(
                    f"promote sidecar for {intent_id!r} already exists with different "
                    f"arguments; re-run with --replace to overwrite it")
            _atomic_write_text(sidecar_path, _sidecar_bytes(proposed), replace_fn)
            return {"status": "replaced", "path": str(sidecar_path), "sidecar": proposed}

    _atomic_write_text(sidecar_path, _sidecar_bytes(proposed), replace_fn)
    return {"status": "written", "path": str(sidecar_path), "sidecar": proposed}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- candidate ----------------------------------------------------------------

# Which fields on an experiment_results.json row mark a candidate as ALREADY RUN,
# and why each one:
#   experiment_id        -- THE FIX. experiment-result.v1 rows carry this and no
#                           candidate_id; the drain compared candidate_id to
#                           candidate_id, so the "already run" set was always
#                           empty and a finished candidate stayed dispatchable.
#   derived_from_candidate -- experiment-plan.v1's provenance field. It is not in
#                           the RESULT schema today, but a harvest that copies the
#                           plan's provenance onto the result row is the only
#                           unambiguous statement of "this result came from that
#                           candidate", so it is honoured when present.
#   candidate_id         -- kept for backward compatibility: hand-written rows and
#                           the existing drain fixtures use it, and dropping it
#                           would silently un-skip anything recorded that way.
RESULT_CANDIDATE_FIELDS = ("candidate_id", "experiment_id", "derived_from_candidate")


def already_run_ids(results_rows) -> set:
    """Candidate ids that appear on any results row, under any honoured field."""
    seen: set = set()
    for row in results_rows or ():
        if not isinstance(row, dict):
            continue
        for key in RESULT_CANDIDATE_FIELDS:
            value = row.get(key)
            if isinstance(value, str) and value:
                seen.add(value)
    return seen


def rank_candidates(entries, already: set) -> list[dict]:
    """Unrun priced candidates, highest worth first, ties broken on candidate_id.

    Pure and total: two ticks over an identical worth table always pick the same
    candidate (no hidden randomness in an unattended dispatch).
    """
    usable = [e for e in entries or ()
              if isinstance(e, dict) and isinstance(e.get("candidate_id"), str)
              and e["candidate_id"] not in already]
    usable.sort(key=lambda e: (-_int_or_zero(e.get("worth_points")), e["candidate_id"]))
    return usable


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def select_candidate(worth_path=DEFAULT_CANDIDATE_WORTH_PATH,
                     results_path=DEFAULT_EXPERIMENT_RESULTS_PATH) -> Optional[dict]:
    """Highest-worth priced candidate entry not yet present in the results file.

    Returns the raw worth ENTRY (the shape ``fleet.bankedfire_drain`` has always
    returned), so existing importers keep working; ``candidate_source`` is what
    turns it into a ``Brief``.
    """
    worth_doc = _load_json(Path(worth_path), {"entries": []})
    results_doc = _load_json(Path(results_path), {"results": []})
    ranked = rank_candidates(worth_doc.get("entries", []),
                             already_run_ids(results_doc.get("results", [])))
    return ranked[0] if ranked else None


def candidate_prompt(entry: dict) -> str:
    """The drain's dispatch prompt, moved here VERBATIM.

    Byte-compatibility is the point: B-04 reworks the dispatch, and until then a
    candidate brief must produce exactly the body the drain produced, so nothing
    downstream (a conductor prompt digest, an operator reading an inbox file)
    changes meaning because the selection moved modules.
    """
    return (
        f"Idle-drain dispatch (Banked Fire P5). Run experiment candidate "
        f"{entry['candidate_id']!r} (worth_points={entry.get('worth_points')}): "
        f"{entry.get('reason', '')}\n\n"
        f"This is unattended, gated, opportunistic fleet work — treat it as a normal build."
    )


def candidate_brief(entry: dict) -> Brief:
    """Build the Brief for one priced candidate entry."""
    candidate_id = entry["candidate_id"]
    slug = safe_slug(candidate_id)
    body = candidate_prompt(entry)
    return Brief(
        slug=slug,
        title=body.splitlines()[0][:120],
        body=body,
        builders=None,
        task_class=CANDIDATE_TASK_CLASS,
        est_tokens=None,
        requires=(f"proposals/{slug}.md",),
        max_age_s=None,
        source="candidate",
        source_ref=candidate_id,
    )


def candidate_source(worth_path, results_path) -> SourceScan:
    """Every unrun priced candidate as a Brief, highest worth first."""
    worth_doc = _load_json(Path(worth_path), {"entries": []})
    results_doc = _load_json(Path(results_path), {"results": []})
    ranked = rank_candidates(worth_doc.get("entries", []),
                             already_run_ids(results_doc.get("results", [])))
    briefs: list[Brief] = []
    rejected: list[dict] = []
    for entry in ranked:
        try:
            briefs.append(candidate_brief(entry))
        except ValueError as exc:
            rejected.append({"source_ref": entry.get("candidate_id"),
                             "reason": f"invalid brief: {exc}"})
    return SourceScan(tuple(briefs), tuple(rejected))
