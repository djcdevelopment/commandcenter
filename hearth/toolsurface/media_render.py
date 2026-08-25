"""MCP surface for the constrained B70 media-render lane.

WHY A DEDICATED TOOL RATHER THAN `submit_execution`
---------------------------------------------------
Capability granularity in HEARTH is per TOOL, not per operation. Granting the
BF6 dispatcher `execution` so it could call `submit_execution(operation=
"media.render")` would also hand it `llm.chat`, `cancel_execution` and the whole
execution surface. A dispatcher whose entire job is "render this clip" should be
able to do exactly that and nothing else, so it gets its own tool with its own
`media_render` capability.

These functions all return immediately. Nothing here runs ffmpeg: `submit_render`
puts a job on the render scheduler's queue and returns a job id. That matters
because the MCP door is single-threaded -- a synchronous tool blocks every other
caller and `/healthz` for its full duration, and a render lasts minutes.
"""
from __future__ import annotations

from typing import Any, Optional

from hearth.execution.defaults import get_execution_service
from hearth.observation.identity import DispatchIdentity, current_identity
from hearth.media import acceptance as acceptance_mod
from hearth.media import lanes as lanes_mod
from hearth.media.jobspec import RenderArgumentError, parse_render_arguments


def submit_render(
    session_id: str,
    clip_id: str,
    clip_revision: int,
    source_segments: list,
    start_seconds: float,
    end_seconds: float,
    variants: list,
    profile_version: str = "bf6-qsv-v1",
    captions: Optional[str] = None,
    captions_path: Optional[str] = None,
) -> dict:
    """Queue a BF6 highlight clip for rendering on a calibrated Arc Pro B70 lane.

    Paths are media-root-relative (``raw/<session>/<file>.mkv``); absolute,
    UNC and traversing paths are refused. Returns as soon as the job is queued --
    poll `get_render_status` for progress. Resubmitting the same clip at the same
    revision, profile and variants returns the SAME job rather than rendering
    twice.
    """
    service = get_execution_service()
    arguments = {
        "session_id": session_id,
        "clip_id": clip_id,
        "clip_revision": clip_revision,
        "source_segments": list(source_segments),
        "start_seconds": float(start_seconds),
        "end_seconds": float(end_seconds),
        "variants": list(variants),
        "profile_version": profile_version,
    }
    if captions is not None:
        arguments["captions"] = captions
    if captions_path is not None:
        arguments["captions_path"] = captions_path

    try:
        spec = parse_render_arguments(arguments)
    except RenderArgumentError as exc:
        raise ValueError(str(exc)) from exc

    # Note what is deliberately NOT checked here: whether an interactive render
    # agent is currently running. A job with no executor is still a valid job.
    # It stays QUEUED until an agent appears, which is what makes "nobody is
    # logged in" a pause rather than a failure. Only a gateway with no render
    # subsystem at all refuses.
    dispatcher = getattr(service, "_render_dispatcher", None)
    if dispatcher is None:
        raise RuntimeError(
            "this gateway has no render subsystem; media.render is unavailable here"
        )

    identity = current_identity()
    if identity is None:
        raise PermissionError("submit_render requires gateway caller identity")
    caller_id = identity.caller_id
    state = service.submit(
        operation_name="media.render",
        arguments=arguments,
        principal={"type": "hearth_caller", "id": caller_id, "authenticated": True},
        source={"transport": "mcp", "adapter": caller_id},
        idempotency_key=spec.idempotency_key(),
    )
    return {
        "ok": True,
        "job_id": state["job_id"],
        "status": state["status"],
        "clip_id": clip_id,
        "clip_revision": clip_revision,
        "variants": list(spec.variants),
        "idempotency_key": spec.idempotency_key(),
    }


def get_render_status(job_id: str) -> dict:
    """Status and live progress for one render job.

    Progress is parsed from ffmpeg's own ``-progress`` stream per variant. A
    terminal job carries the render receipt's artifact reference, which holds the
    validation measurements and the promotion outcome.
    """
    service = get_execution_service()
    state = service.ledger.get_job(job_id)
    if state is None:
        raise ValueError("unknown job_id: %s" % job_id)
    dispatcher = getattr(service, "_render_dispatcher", None)
    progress = dispatcher.progress_for(job_id) if dispatcher is not None else {}
    # The ledger projection lifts observed fields to the top level; it does not
    # retain an events list, so reading state["events"] silently yields nothing.
    return {
        "ok": True,
        "job_id": job_id,
        "status": state.get("status"),
        "lane": state.get("provider"),
        "child_device": state.get("child_device"),
        "profile_version": state.get("model"),
        "scheduling": state.get("scheduling") or {},
        "reason": state.get("reason"),
        "progress": progress,
        "artifacts": state.get("artifacts", []),
    }


def cancel_render(job_id: str, reason: str = "superseded by a newer revision") -> dict:
    """Cancel a queued or running render job.

    Scoped deliberately: this cancels `media.render` jobs and nothing else, so
    the BF6 dispatcher can retire its own superseded work without being granted
    the whole `execution` surface and its `cancel_execution`.

    `stopped_before_start` is the honest part of the answer. True means the job
    never reached the GPU and cannot. False means an agent had already claimed
    it -- cancellation is cooperative (ADR-0030), the agent checks between
    variants, and a variant already encoding runs to completion. Nothing here
    kills an ffmpeg. Callers must not treat cancellation as the thing that keeps
    a stale render from landing: the commit-time revision check does that.
    """
    service = get_execution_service()
    state = service.ledger.get_job(job_id)
    if state is None:
        raise ValueError("unknown job_id: %s" % job_id)
    if (state.get("desired") or {}).get("operation") != "media.render":
        # Without this the tool would be a general-purpose cancel wearing a
        # narrow capability.
        raise PermissionError("cancel_render only cancels media.render jobs")

    identity = current_identity()
    if identity is None:
        raise PermissionError("cancel_render requires gateway caller identity")
    owner = (state.get("principal") or {}).get("id")
    if owner and owner != identity.caller_id:
        raise PermissionError("job %s belongs to another caller" % job_id)

    dispatcher = getattr(service, "_render_dispatcher", None)
    if dispatcher is None:
        raise RuntimeError(
            "this gateway has no render subsystem; media.render is unavailable here"
        )
    outcome = dispatcher.cancel(job_id, reason=reason)
    return {"ok": True, **outcome}


def list_render_lanes() -> dict:
    """Calibrated B70 render lanes, their health, and the accepted capacity.

    ``media_engines`` is CALIBRATED, not assumed: it records which performance-
    counter engines were observed carrying a real QSV encode on that card. On
    this driver that is ``videodecode`` -- there is no ``VideoEncode`` node --
    which is exactly why the value is measured rather than hardcoded.

    ``interactive_executor_available`` says whether a render agent is alive in
    an interactive session. The gateway itself cannot render -- session 0 has no
    GPU adapter access -- so healthy lanes with no executor means queued work is
    waiting, not broken.

    ``accepted_lane_count`` is how many lanes may run CONCURRENTLY. It comes
    from the last accepted coexistence benchmark, not from how many lanes exist;
    a stale record keeps its old count until a new benchmark is accepted.
    """
    calibration = lanes_mod.load_calibration()
    record = acceptance_mod.load_acceptance()
    dispatcher = getattr(get_execution_service(), "_render_dispatcher", None)
    executor = dispatcher.executor_status() if dispatcher is not None else None
    if calibration is None:
        return {
            "ok": False,
            "error": "no lane calibration found; run hearth.media.lanes.calibrate()",
            "lanes": [],
            "accepted_lane_count": record.accepted_lane_count,
        }
    return {
        "ok": True,
        # A lane can be calibrated-healthy while nothing can currently drive it.
        # The gateway runs in session 0 and has no GPU access at all; execution
        # belongs to an interactive-session agent. This flag is the difference
        # between "a broken install" and "nobody is logged in" -- and in the
        # latter case submitted jobs stay queued, not failed.
        "interactive_executor_available": bool(executor and executor.available),
        "executor": executor.to_dict() if executor else None,
        "lanes": [lane.to_dict() for lane in calibration.lanes],
        "healthy": [lane.lane_id for lane in calibration.healthy_lanes()],
        "calibrated_at": calibration.calibrated_at,
        "fingerprint": calibration.fingerprint,
        "accepted_lane_count": record.accepted_lane_count,
        "acceptance_stale": record.stale,
        "acceptance_detail": record.detail,
    }


def get_tools() -> list:
    """Provider entry point: the render lane's MCP tools."""
    return [submit_render, get_render_status, cancel_render, list_render_lanes]
