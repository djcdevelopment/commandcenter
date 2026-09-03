"""Durable, caller-scoped MediaGen tool surface."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from hearth.execution.defaults import get_execution_service
from hearth.execution.model import FINAL_JOB_STATUSES
from hearth.observation.identity import current_identity
from hearth.observation.telemetry import get_current_traceparent
from hearth.toolsurface._scope import resolve_in_scope


def _identity():
    identity = current_identity()
    if identity is None:
        raise PermissionError("MediaGen tools require gateway caller identity")
    return identity


def _principal(identity) -> dict:
    return {"type": "hearth_caller", "id": identity.caller_id, "authenticated": True}


def _read_document(document_path: str) -> tuple[Path, str, str]:
    path = resolve_in_scope(document_path)
    if not path.is_file() or path.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("document_path must identify a scoped .txt or .md file")
    content = path.read_bytes()
    if len(content) > 256 * 1024:
        raise ValueError("document exceeds the 256 KiB limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("document must be valid UTF-8") from exc
    if not text.strip() or "\x00" in text:
        raise ValueError("document must contain non-empty text without NUL characters")
    return path, text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_image(content: bytes, filename: str) -> str:
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError("still image must contain 1 byte to 20 MiB")
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", "pipe:0"],
            input=content, capture_output=True, timeout=30,
        )
        if completed.returncode != 0:
            raise ValueError("still image cannot be decoded")
        streams = json.loads(completed.stdout.decode("utf-8")).get("streams") or []
        stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        if stream is None:
            raise ValueError("still image cannot be decoded")
        media_type = {"png": "image/png", "webp": "image/webp"}.get(stream.get("codec_name"))
        if media_type is None:
            raise ValueError("still image must be PNG or WebP")
        width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
        if width <= 0 or height <= 0 or width * height > 16_000_000:
            raise ValueError("still image exceeds the 16 megapixel limit")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("still image cannot be decoded") from exc
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".webp"}:
        raise ValueError("still image filename must end in .png or .webp")
    if (suffix == ".png") != (media_type == "image/png"):
        raise ValueError("still image extension does not match its content")
    return media_type


def _submit(operation: str, arguments: dict, deadline_s: Optional[int], idempotency_key: Optional[str]) -> dict:
    identity = _identity()
    service = get_execution_service()
    policy = {"deadline_s": deadline_s} if deadline_s is not None else None
    state = service.submit(
        operation_name=operation, arguments=arguments,
        principal=_principal(identity), source={"transport": "mcp", "adapter": identity.caller_id},
        policy=policy, idempotency_key=idempotency_key,
    )
    return {"ok": True, "job_id": state["job_id"], "operation": operation, "status": state["status"]}


def submit_podcast(
    document_path: str, title: Optional[str] = None, voice_profile: str = "alex_sam",
    deadline_s: Optional[int] = None, idempotency_key: Optional[str] = None,
) -> dict:
    """Queue a durable TXT/Markdown-to-podcast job and return immediately."""
    path, text, digest = _read_document(document_path)
    return _submit("media.podcast", {
        "document_text": text, "document_name": path.name, "document_sha256": digest,
        "title": title, "voice_profile": voice_profile,
        "traceparent": get_current_traceparent(),
    }, deadline_s, idempotency_key)


def submit_video_animation(
    motion_prompt: str, still_artifact_id: Optional[str] = None,
    still_image_path: Optional[str] = None, target_lane: str = "any",
    deadline_s: Optional[int] = None, idempotency_key: Optional[str] = None,
) -> dict:
    """Queue a fixed 81-frame Wan animation, automatically managing GPU tenancy."""
    if (still_artifact_id is None) == (still_image_path is None):
        raise ValueError("provide exactly one of still_artifact_id or still_image_path")
    from hearth.toolsurface.image_generate import _workflow_registry
    workflow = next((item for item in _workflow_registry().get("workflows", [])
                     if item.get("id") == "wan2-i2v" and item.get("enabled") is True), None)
    if workflow is None:
        raise ValueError("wan2-i2v is not registered or enabled")
    allowed_lanes = workflow.get("allowed_lane_ids") or []
    if target_lane != "any" and target_lane not in allowed_lanes:
        raise ValueError("target lane is not enabled for wan2-i2v: " + target_lane)
    identity = _identity()
    service = get_execution_service()
    if still_artifact_id is not None:
        owner_job = service.ledger.artifact_job_id(still_artifact_id)
        owner_state = service.ledger.get_job(owner_job) if owner_job else None
        if owner_state is None:
            raise ValueError("unknown still_artifact_id")
        if (owner_state.get("principal") or {}).get("id") != identity.caller_id:
            raise PermissionError("still artifact belongs to another caller")
        metadata, content = service.read_artifact(still_artifact_id)
        filename = str(metadata.get("filename") or (still_artifact_id + ".png"))
    else:
        path = resolve_in_scope(still_image_path or "")
        if not path.is_file():
            raise ValueError("still_image_path must identify a scoped file")
        content, filename = path.read_bytes(), path.name
    media_type = _validate_image(content, filename)
    return _submit("media.animate", {
        "motion_prompt": motion_prompt,
        "source_image_b64": base64.b64encode(content).decode("ascii"),
        "source_image_name": Path(filename).name, "source_media_type": media_type,
        "source_artifact_id": still_artifact_id, "target_lane": target_lane,
        "traceparent": get_current_traceparent(),
    }, deadline_s, idempotency_key)


def submit_media_pipeline(
    document_path: str, title: Optional[str] = None, voice_profile: str = "alex_sam",
    scene_count: int = 4, deadline_s: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Queue the durable document-to-podcast-video pipeline and return immediately."""
    path, text, digest = _read_document(document_path)
    return _submit("media.pipeline", {
        "document_text": text, "document_name": path.name, "document_sha256": digest,
        "title": title, "voice_profile": voice_profile, "scene_count": scene_count,
        "traceparent": get_current_traceparent(),
    }, deadline_s, idempotency_key)


def _owned_media_state(job_id: str) -> tuple[Any, dict]:
    identity = _identity()
    service = get_execution_service()
    state = service.ledger.get_job(job_id)
    if state is None:
        raise ValueError("unknown job_id: " + job_id)
    if (state.get("desired") or {}).get("operation") not in {
        "media.podcast", "media.animate", "media.pipeline"
    }:
        raise PermissionError("job is not a MediaGen job")
    if (state.get("principal") or {}).get("id") != identity.caller_id:
        raise PermissionError("job belongs to another caller")
    return service, state


def get_media_status(job_id: str) -> dict:
    """Return durable MediaGen state, checkpoint progress, children, and artifacts."""
    service, state = _owned_media_state(job_id)
    checkpoint = {}
    for artifact in reversed(state.get("artifacts") or []):
        if artifact.get("role") == "checkpoint":
            try:
                checkpoint = json.loads(service.artifacts.read(artifact).decode("utf-8"))
            except Exception:
                checkpoint = {}
            break
    return {
        "ok": True, "job_id": job_id, "operation": state.get("operation"),
        "status": state.get("status"), "reason": state.get("reason"),
        "progress": checkpoint.get("progress") or {},
        "child_job_ids": checkpoint.get("child_job_ids") or [],
        "degraded": bool(state.get("degraded") or checkpoint.get("degraded")),
        "warnings": state.get("warnings") or checkpoint.get("warnings") or [],
        "artifacts": state.get("artifacts") or [],
    }


def cancel_media(job_id: str, reason: str = "cancelled by caller") -> dict:
    """Cancel an owned MediaGen parent and its active child jobs."""
    service, state = _owned_media_state(job_id)
    if state.get("status") in FINAL_JOB_STATUSES:
        return {"ok": True, "job_id": job_id, "status": state["status"], "already_terminal": True}
    dispatcher = getattr(service, "_media_dispatcher", None)
    if dispatcher is None:
        raise RuntimeError("this gateway has no MediaGen subsystem")
    return {"ok": True, **dispatcher.cancel(job_id, reason=reason)}


def get_tools() -> list:
    """Provider entry point for separately authorized MediaGen tools."""
    return [
        submit_podcast, submit_video_animation, submit_media_pipeline,
        get_media_status, cancel_media,
    ]
