"""Typed arguments for the `media.render` operation.

Validation runs on the CALLER'S thread inside ``ExecutionService.submit``, not
on a worker. That is not stylistic: path containment consults ContextVars that a
``ThreadPoolExecutor`` worker does not inherit, so a check deferred to the
worker would run under the wrong (or no) scope. The same reasoning is already
recorded in service.py where `files=` packing happens.

Every field is checked and every unknown key is refused. A render job names
media paths and writes into the drafts tree; "probably fine" is not a standard
that belongs anywhere near it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from hearth.media.profiles import VARIANTS, ProfileError, get_profile
from hearth.toolsurface._media_scope import MediaPathError, resolve_media, subtree_of

# Mirrors contracts/session.schema.json so the two machines agree on what a
# session id even is.
SESSION_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
CLIP_SUFFIX_RE = re.compile(r"^[a-f0-9]{12}$")

# The AM4 pipeline caps a clip at 180 s (pipeline.py, applied at window time and
# again after every merge/extension). A longer request means the two sides have
# drifted apart, which is worth failing on rather than rendering.
MAX_CLIP_SECONDS = 180.0
MIN_CLIP_SECONDS = 0.05

MAX_SOURCE_SEGMENTS = 8
MAX_CAPTION_BYTES = 256 * 1024

ALLOWED_KEYS = frozenset({
    "session_id", "clip_id", "clip_revision", "source_segments",
    "start_seconds", "end_seconds", "captions", "captions_path",
    "variants", "profile_version",
})


class RenderArgumentError(ValueError):
    """A render request was malformed. The job is refused before it is queued."""


@dataclass(frozen=True)
class RenderJobSpec:
    session_id: str
    clip_id: str
    clip_revision: int
    source_segments: tuple
    start_seconds: float
    end_seconds: float
    variants: tuple
    profile_version: str
    captions: Optional[str] = None
    captions_path: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "clip_id": self.clip_id,
            "clip_revision": self.clip_revision,
            "source_segments": list(self.source_segments),
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "variants": list(self.variants),
            "profile_version": self.profile_version,
            "captions_path": self.captions_path,
            "has_captions": bool(self.captions or self.captions_path),
        }

    def idempotency_key(self) -> str:
        return idempotency_key_for(
            self.session_id, self.clip_id, self.clip_revision,
            self.profile_version, self.variants,
        )


def idempotency_key_for(session_id: str, clip_id: str, clip_revision: int,
                        profile_version: str, variants: Sequence) -> str:
    """Deterministic key over exactly what changes the OUTPUT.

    Same clip at the same revision through the same profile for the same
    variants is the same job -- so a dispatcher that retries, or restarts and
    resubmits, converges onto the existing job instead of rendering twice.

    ``clip_revision`` is in the key so an extended clip is genuinely a NEW job;
    it is NOT an authority claim about which revision is current (see
    hearth.media.revision for that).
    """
    material = "|".join([
        session_id, clip_id, str(int(clip_revision)), profile_version,
        ",".join(sorted(str(v) for v in variants)),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RenderArgumentError("%s must be a non-empty string" % field)
    return value


def _require_number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderArgumentError("%s must be a number" % field)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise RenderArgumentError("%s must be finite" % field)
    return number


def parse_render_arguments(arguments: Mapping) -> RenderJobSpec:
    """Validate a render request into a typed spec, or raise."""
    unknown = set(arguments) - ALLOWED_KEYS
    if unknown:
        raise RenderArgumentError(
            "unknown media.render arguments: %s" % ", ".join(sorted(unknown))
        )

    session_id = _require_string(arguments.get("session_id"), "session_id")
    if not SESSION_ID_RE.match(session_id):
        raise RenderArgumentError(
            "session_id %r does not match the session contract "
            "(yyyymmddThhmmssZ-<8 hex>)" % session_id
        )

    clip_id = _require_string(arguments.get("clip_id"), "clip_id")
    prefix = session_id + "-"
    if not clip_id.startswith(prefix) or not CLIP_SUFFIX_RE.match(clip_id[len(prefix):]):
        raise RenderArgumentError(
            "clip_id %r must be <session_id>-<12 hex>" % clip_id
        )

    revision = arguments.get("clip_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RenderArgumentError("clip_revision must be a non-negative integer")

    segments = arguments.get("source_segments")
    if not isinstance(segments, (list, tuple)) or not segments:
        raise RenderArgumentError("source_segments must be a non-empty list")
    if len(segments) > MAX_SOURCE_SEGMENTS:
        raise RenderArgumentError(
            "source_segments has %d entries; limit is %d"
            % (len(segments), MAX_SOURCE_SEGMENTS)
        )
    resolved_segments = []
    for entry in segments:
        path = _require_string(entry, "source_segments entry")
        try:
            # Read-only by construction: resolve_media refuses a write against
            # raw/, which reproduces AM4's read-only mount on the side of the
            # boundary that does not have one.
            resolve_media(path, mode="read")
            subtree = subtree_of(path)
        except MediaPathError as exc:
            raise RenderArgumentError("source segment refused: %s" % exc) from exc
        if subtree != "raw":
            raise RenderArgumentError(
                "source segments must live under raw/, got %r" % path
            )
        resolved_segments.append(path)

    start = _require_number(arguments.get("start_seconds"), "start_seconds")
    end = _require_number(arguments.get("end_seconds"), "end_seconds")
    if start < 0:
        raise RenderArgumentError("start_seconds must not be negative")
    if end <= start:
        raise RenderArgumentError("end_seconds must be greater than start_seconds")
    duration = end - start
    if duration < MIN_CLIP_SECONDS:
        raise RenderArgumentError(
            "clip is %.3fs; shorter than the %.2fs minimum" % (duration, MIN_CLIP_SECONDS)
        )
    if duration > MAX_CLIP_SECONDS:
        raise RenderArgumentError(
            "clip is %.3fs; the pipeline caps clips at %.0fs" % (duration, MAX_CLIP_SECONDS)
        )

    variants = arguments.get("variants")
    if not isinstance(variants, (list, tuple)) or not variants:
        raise RenderArgumentError("variants must be a non-empty list")
    normalised_variants = []
    for variant in variants:
        name = _require_string(variant, "variant")
        if name not in VARIANTS:
            raise RenderArgumentError(
                "unknown variant %r; expected one of %s" % (name, ", ".join(VARIANTS))
            )
        if name not in normalised_variants:
            normalised_variants.append(name)

    profile_version = _require_string(arguments.get("profile_version"), "profile_version")
    try:
        get_profile(profile_version)
    except ProfileError as exc:
        raise RenderArgumentError(str(exc)) from exc

    captions = arguments.get("captions")
    if captions is not None:
        if not isinstance(captions, str):
            raise RenderArgumentError("captions must be SRT text")
        if len(captions.encode("utf-8")) > MAX_CAPTION_BYTES:
            raise RenderArgumentError(
                "captions exceed %d bytes" % MAX_CAPTION_BYTES
            )

    captions_path = arguments.get("captions_path")
    if captions_path is not None:
        path = _require_string(captions_path, "captions_path")
        try:
            resolve_media(path, mode="read")
            subtree = subtree_of(path)
        except MediaPathError as exc:
            raise RenderArgumentError("captions_path refused: %s" % exc) from exc
        if subtree != "work":
            raise RenderArgumentError(
                "captions_path must live under work/, got %r" % path
            )
        captions_path = path

    if captions is not None and captions_path is not None:
        raise RenderArgumentError(
            "pass captions OR captions_path, not both -- two sources of caption "
            "truth is how timing drifts"
        )

    return RenderJobSpec(
        session_id=session_id,
        clip_id=clip_id,
        clip_revision=revision,
        source_segments=tuple(resolved_segments),
        start_seconds=start,
        end_seconds=end,
        variants=tuple(normalised_variants),
        profile_version=profile_version,
        captions=captions,
        captions_path=captions_path,
    )


def validate_render_arguments(operation, arguments: Mapping) -> tuple:
    """``ExecutionService._validate_arguments`` hook for the media_render handler.

    Returns ``(normalised_arguments, payload_bytes)``. The payload is the job
    spec serialised as JSON, which becomes the job's input artifact -- the
    non-LLM analogue of the prompt, so the ledger still records exactly what was
    asked for.
    """
    spec = parse_render_arguments(arguments)
    payload = json.dumps(spec.to_dict(), sort_keys=True).encode("utf-8")
    limit = getattr(operation, "max_prompt_bytes", None)
    if limit and len(payload) > limit:
        raise RenderArgumentError(
            "render job spec is %d bytes; limit is %d" % (len(payload), limit)
        )
    normalised = dict(arguments)
    normalised["_spec"] = spec
    return normalised, payload
