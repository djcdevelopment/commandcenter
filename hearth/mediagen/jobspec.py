"""Private-input packing and validation for durable MediaGen operations."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Mapping

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
TARGET_LANES = frozenset({"any", "b70@bus4", "b70@bus9"})


class MediaArgumentError(ValueError):
    pass


def _title(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise MediaArgumentError("title must be a non-empty string of at most 200 characters")
    return value.strip()


def _document(value: Mapping[str, Any], *, allow_scene_count: bool) -> tuple[dict, dict]:
    allowed = {
        "document_text", "document_name", "document_sha256", "title",
        "voice_profile", "traceparent",
    }
    if allow_scene_count:
        allowed.add("scene_count")
    unknown = set(value) - allowed
    if unknown:
        raise MediaArgumentError("unknown MediaGen arguments: %s" % ", ".join(sorted(unknown)))
    text = value.get("document_text")
    if not isinstance(text, str) or not text.strip():
        raise MediaArgumentError("document_text must be non-empty UTF-8 text")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise MediaArgumentError("document exceeds the 256 KiB limit")
    name = value.get("document_name")
    if not isinstance(name, str) or not name or name.rsplit(".", 1)[-1].lower() not in {"txt", "md"}:
        raise MediaArgumentError("document_name must end in .txt or .md")
    digest = hashlib.sha256(encoded).hexdigest()
    if value.get("document_sha256") != digest:
        raise MediaArgumentError("document_sha256 does not match document_text")
    from hearth.mediagen.audio.registry import get_voice_registry

    registry = get_voice_registry()
    voice = value.get("voice_profile", "alex_sam")
    if not isinstance(voice, str) or not registry.get(voice):
        allowed = ", ".join(registry.list_profiles())
        raise MediaArgumentError(f"voice_profile must be one of: {allowed}")
    scene_count = value.get("scene_count", 4)
    if allow_scene_count and (
        not isinstance(scene_count, int) or isinstance(scene_count, bool) or not 1 <= scene_count <= 8
    ):
        raise MediaArgumentError("scene_count must be between 1 and 8")
    private = {
        "schema": "mediagen.request.v1", "document_text": text,
        "document_name": name, "document_sha256": digest,
        "title": _title(value.get("title")), "voice_profile": voice,
        "traceparent": value.get("traceparent"),
    }
    public = {
        "document_name": name, "document_sha256": digest,
        "title": private["title"], "voice_profile": voice,
    }
    if allow_scene_count:
        private["scene_count"] = scene_count
        public["scene_count"] = scene_count
    return public, private


def _animation(value: Mapping[str, Any]) -> tuple[dict, dict]:
    allowed = {
        "motion_prompt", "source_image_b64", "source_image_name", "source_media_type",
        "source_artifact_id", "target_lane", "traceparent",
    }
    unknown = set(value) - allowed
    if unknown:
        raise MediaArgumentError("unknown media.animate arguments: %s" % ", ".join(sorted(unknown)))
    prompt = value.get("motion_prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise MediaArgumentError("motion_prompt must contain 1 to 4000 characters")
    media_type = value.get("source_media_type")
    if media_type not in {"image/png", "image/webp"}:
        raise MediaArgumentError("source image must be PNG or WebP")
    encoded = value.get("source_image_b64")
    if not isinstance(encoded, str):
        raise MediaArgumentError("source_image_b64 must be a base64 string")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise MediaArgumentError("source_image_b64 is invalid") from exc
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise MediaArgumentError("source image must contain 1 byte to 20 MiB")
    target = value.get("target_lane", "any")
    if target not in TARGET_LANES:
        raise MediaArgumentError("target_lane must be any, b70@bus4, or b70@bus9")
    name = value.get("source_image_name")
    if not isinstance(name, str) or not name:
        raise MediaArgumentError("source_image_name is required")
    private = {
        "schema": "mediagen.request.v1", "motion_prompt": prompt.strip(),
        "source_image_b64": encoded, "source_image_name": name,
        "source_media_type": media_type, "source_artifact_id": value.get("source_artifact_id"),
        "source_sha256": hashlib.sha256(content).hexdigest(), "target_lane": target,
        "traceparent": value.get("traceparent"),
    }
    public = {key: private[key] for key in (
        "source_image_name", "source_media_type", "source_artifact_id",
        "source_sha256", "target_lane",
    )}
    public["motion_prompt_sha256"] = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()
    return public, private


def validate_media_arguments(operation, arguments: Mapping[str, Any]) -> tuple[dict, bytes]:
    value = dict(arguments)
    if operation.name == "media.podcast":
        public, private = _document(value, allow_scene_count=False)
    elif operation.name == "media.pipeline":
        public, private = _document(value, allow_scene_count=True)
    elif operation.name == "media.animate":
        public, private = _animation(value)
    else:
        raise MediaArgumentError("unsupported MediaGen operation: " + operation.name)
    packed = json.dumps(private, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(packed) > operation.max_prompt_bytes:
        raise MediaArgumentError("packed MediaGen request exceeds operation input limit")
    return {**public, "_spec": private}, packed
