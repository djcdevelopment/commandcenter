"""Narrow MCP surface for the dual-B70 image-generation pool."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from hearth.execution.defaults import get_execution_service
from hearth.imagegen import acceptance as image_acceptance
from hearth.imagegen.jobspec import ImageArgumentError, parse_image_arguments
from hearth.media import lanes as lanes_mod
from hearth.observation.identity import current_identity


def _dispatcher():
    dispatcher = getattr(get_execution_service(), "_image_dispatcher", None)
    if dispatcher is None:
        raise RuntimeError("this gateway has no image-generation subsystem")
    return dispatcher


def _workflow_registry() -> dict[str, Any]:
    path = Path(os.environ.get(
        "IMAGEGEN_WORKFLOW_REGISTRY", r"E:\omen\imagegen\config\workflows.json"
    ))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("workflow registry unavailable: %s" % exc) from exc
    workflows = value.get("workflows") if isinstance(value, dict) else None
    if value.get("schema") != "imagegen.workflow-registry.v1" or not isinstance(workflows, list):
        raise RuntimeError("workflow registry has an unsupported schema")
    return value


def submit_image(
    workflow_id: str,
    parameters: dict,
    strategy: str = "auto",
    priority: str = "normal",
    target_lane: str = "any",
    deadline_s: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Queue a registered image workflow; execution waits for an active art session."""
    identity = current_identity()
    if identity is None:
        raise PermissionError("submit_image requires gateway caller identity")
    arguments = {
        "workflow_id": workflow_id, "parameters": parameters,
        "strategy": strategy, "priority": priority,
        "target_lane": target_lane,
    }
    try:
        spec = parse_image_arguments(arguments)
    except ImageArgumentError as exc:
        raise ValueError(str(exc)) from exc
    registered = {
        item.get("id"): item for item in _workflow_registry()["workflows"]
        if isinstance(item, dict) and item.get("enabled") is True
    }
    workflow = registered.get(spec.workflow_id)
    if workflow is None:
        raise ValueError("workflow is not registered or enabled: %s" % spec.workflow_id)
    allowed_lanes = workflow.get("allowed_lane_ids") or []
    if spec.target_lane != "any" and allowed_lanes and spec.target_lane not in allowed_lanes:
        raise ValueError(
            "target lane is not enabled for workflow %s: %s" %
            (spec.workflow_id, spec.target_lane)
        )
    acceptance = image_acceptance.load_acceptance()
    if not image_acceptance.workflow_available(workflow, spec.strategy, acceptance):
        raise ValueError(
            "workflow strategy is not production-qualified: %s/%s (%s)" %
            (spec.workflow_id, spec.strategy, acceptance.detail)
        )
    policy: dict[str, Any] = {"priority": {"low": -1, "normal": 0, "high": 1}[priority]}
    if deadline_s is not None:
        if not isinstance(deadline_s, int) or isinstance(deadline_s, bool) or deadline_s < 1:
            raise ValueError("deadline_s must be a positive integer")
        policy["deadline_s"] = deadline_s
    service = get_execution_service()
    if getattr(service, "_image_dispatcher", None) is None:
        raise RuntimeError("this gateway has no image-generation subsystem")
    state = service.submit(
        operation_name="image.generate", arguments=arguments,
        principal={"type": "hearth_caller", "id": identity.caller_id, "authenticated": True},
        source={"transport": "mcp", "adapter": identity.caller_id},
        policy=policy, idempotency_key=idempotency_key,
    )
    return {
        "ok": True, "job_id": state["job_id"], "status": state["status"],
        "workflow_id": spec.workflow_id, "strategy": spec.strategy,
        "target_lane": spec.target_lane,
        "resolved_seed": spec.parameters["seed"],
        "session": _dispatcher().session.status()["session"],
    }


def get_image_status(job_id: str) -> dict:
    """Return durable lifecycle state and live agent progress for one image job."""
    service = get_execution_service()
    state = service.ledger.get_job(job_id)
    if state is None:
        raise ValueError("unknown job_id: %s" % job_id)
    if (state.get("desired") or {}).get("operation") != "image.generate":
        raise PermissionError("get_image_status only reads image.generate jobs")
    return {
        "ok": True, "job_id": job_id, "status": state.get("status"),
        "reason": state.get("reason"), "provider": state.get("provider"),
        "backend": state.get("backend"), "strategy": state.get("strategy"),
        "scheduling": state.get("scheduling") or {},
        "progress": _dispatcher().progress_for(job_id),
        "artifacts": state.get("artifacts") or [],
    }


def cancel_image(job_id: str, reason: str = "cancelled by caller") -> dict:
    """Cancel an owned image job without granting general execution cancellation."""
    service = get_execution_service()
    state = service.ledger.get_job(job_id)
    if state is None:
        raise ValueError("unknown job_id: %s" % job_id)
    if (state.get("desired") or {}).get("operation") != "image.generate":
        raise PermissionError("cancel_image only cancels image.generate jobs")
    identity = current_identity()
    if identity is None:
        raise PermissionError("cancel_image requires gateway caller identity")
    owner = (state.get("principal") or {}).get("id")
    if owner and owner != identity.caller_id:
        raise PermissionError("job %s belongs to another caller" % job_id)
    return {"ok": True, **_dispatcher().cancel(job_id, reason=reason)}


def list_image_workflows() -> dict:
    """List versioned workflows and their caller-visible parameter contracts."""
    try:
        value = _workflow_registry()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc),
                "workflows": []}
    workflows = value.get("workflows") if isinstance(value, dict) else None
    acceptance = image_acceptance.load_acceptance()
    projected = []
    for item in workflows or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        strategies = item.get("allowed_strategies") or ["single"]
        row["qualified_strategies"] = [
            strategy for strategy in strategies
            if image_acceptance.workflow_available(item, strategy, acceptance)
        ]
        row["production_available"] = bool(item.get("enabled")) and bool(
            row["qualified_strategies"]
        )
        projected.append(row)
    return {"ok": isinstance(workflows, list), "acceptance": acceptance.to_dict(),
            "workflows": projected}


def list_image_lanes() -> dict:
    """List stable B70 lane identities, accepted concurrency, and current tenancy."""
    calibration = lanes_mod.load_calibration()
    acceptance = image_acceptance.load_acceptance()
    dispatcher = _dispatcher()
    agent = dispatcher.executor_status()
    live_lanes = (agent.record or {}).get("lanes") if agent.available else None
    return {
        "ok": bool(live_lanes) or calibration is not None,
        "lanes": live_lanes or (
            [lane.to_dict() for lane in calibration.lanes] if calibration else []
        ),
        "identity_source": "imagegen-agent" if live_lanes else "media-calibration-fallback",
        "accepted_lane_count": acceptance.accepted_lane_count,
        "acceptance_stale": acceptance.stale,
        "acceptance": acceptance.to_dict(),
        "agent": agent.to_dict(),
        "session": dispatcher.session.status()["session"],
    }


def get_image_session() -> dict:
    """Return fenced image-session state, agent health, queue depth, and idle policy."""
    return _dispatcher().session.status()


def start_image_session(reason: str = "operator requested art session") -> dict:
    """Asynchronously drain ArcServe and grant both B70s to image generation."""
    return _dispatcher().session.start(reason=reason)


def stop_image_session(
    force: bool = False, reason: str = "operator requested ArcServe restore"
) -> dict:
    """Drain image work and asynchronously restore a warm, serviceable ArcServe."""
    return _dispatcher().session.stop(force=force, reason=reason)


def get_tools() -> list:
    """Provider entry point for separately-authorized image and session tools."""
    return [
        submit_image, get_image_status, cancel_image, list_image_workflows,
        list_image_lanes, get_image_session, start_image_session, stop_image_session,
    ]
