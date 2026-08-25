"""Glue between HEARTH's execution plane and the render subsystem.

``ExecutionService`` knows nothing about ffmpeg, lanes or media paths, and the
render modules know nothing about jobs, ledgers or artifacts. This module is the
only place the two meet.

It supplies the object ``ExecutionService`` calls ``enqueue(job_id)`` on, and it
owns the render scheduler behind it. Its runner is responsible for the parts of
the job lifecycle that belong to the execution plane -- the dispatched/running/
succeeded events, and the result artifact -- while delegating the actual work to
``hearth.media.render``.

The receipt is stored as a small JSON artifact, never the video. Rendered output
lands in ``drafts/`` and is referenced by path and sha256;
``get_execution_artifact`` caps inline retrieval at 1 MiB and text only, so
putting an MP4 there would be useless as well as wasteful.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from hearth.execution.ids import new_invocation_id
from hearth.media import jobspec as jobspec_mod
from hearth.media import lanes as lanes_mod
from hearth.media import occupancy as occupancy_mod
from hearth.media import render as render_mod
from hearth.media import scheduler as scheduler_mod


def _discover(name: str, env_var: str) -> str:
    configured = os.environ.get(env_var)
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    return name


class RenderSubsystem:
    """Owns the render scheduler and reports job lifecycle back to the ledger."""

    def __init__(
        self,
        *,
        service,
        calibration_provider: Optional[Callable] = None,
        gate: Optional[Callable] = None,
        occupancy: Optional[Callable] = None,
        spill: Optional[Callable] = None,
        acceptance_provider: Optional[Callable] = None,
        ffmpeg: Optional[str] = None,
        ffprobe: Optional[str] = None,
        workers: int = scheduler_mod.RENDER_WORKERS,
        autostart: bool = True,
    ) -> None:
        self._service = service
        self._ffmpeg = ffmpeg or _discover("ffmpeg", "HEARTH_FFMPEG")
        self._ffprobe = ffprobe or _discover("ffprobe", "HEARTH_FFPROBE")
        self._calibration_provider = calibration_provider or lanes_mod.load_calibration
        self._progress: dict = {}
        self.scheduler = scheduler_mod.RenderScheduler(
            runner=self._run_job,
            leases=service.leases,
            lanes_provider=self._lanes,
            gate=gate,
            occupancy=occupancy if occupancy is not None else self._default_occupancy,
            spill=spill,
            acceptance_provider=acceptance_provider,
            workers=workers,
            on_error=self._on_error,
            autostart=autostart,
        )

    # ------------------------------------------------------------- plumbing

    def _lanes(self) -> list:
        calibration = self._calibration_provider()
        if calibration is None:
            # No lane map means no capacity. Saying so is better than guessing
            # an adapter index and encoding on the integrated GPU.
            return []
        return calibration.healthy_lanes()

    @staticmethod
    def _default_occupancy(lane):
        return occupancy_mod.media_occupancy(lane)

    def enqueue(self, job_id: str) -> None:
        """The hook ``ExecutionService`` calls. Consumes no worker."""
        self.scheduler.enqueue(job_id)

    def close(self, *, wait: bool = True) -> None:
        self.scheduler.close(wait=wait)

    def progress_for(self, job_id: str) -> dict:
        return dict(self._progress.get(job_id, {}))

    # -------------------------------------------------------------- runner

    def _run_job(self, *, job_id, lane, lease_id, scheduling) -> None:
        service = self._service
        state = service.ledger.get_job(job_id)
        if state is None:
            return
        if service._is_cancelled(job_id):
            service._append("job.cancelled", state, reason="cancelled before dispatch")
            return

        arguments = (state.get("desired") or {}).get("arguments") or {}
        try:
            spec = jobspec_mod.parse_render_arguments(arguments)
        except jobspec_mod.RenderArgumentError as exc:
            service._append("job.failed", state, reason="invalid render job: %s" % exc)
            return

        invocation_id = new_invocation_id()
        # `observed` is the event contract's slot for what actually happened --
        # provider/model are not top-level event fields. The lane takes the
        # provider slot and the render profile takes the model slot, so a render
        # reads back through the same ledger surface as an inference call.
        dispatch_observed = {
            "provider": lane.lane_id,
            "model": spec.profile_version,
            "routed_by": "render-scheduler",
            "occupancy": "leased",
            "lease_id": lease_id,
            "child_device": lane.child_device,
            "scheduling": dict(scheduling or {}),
        }
        service._append("job.dispatched", state, observed=dispatch_observed)
        service._append("invocation.started", state, invocation_id=invocation_id,
                        observed=dispatch_observed)
        service._append("job.running", state)

        def on_progress(variant, key, value):
            if key in ("out_time_ms", "frame", "speed"):
                self._progress.setdefault(job_id, {}).setdefault(variant, {})[key] = value

        receipt = render_mod.render_clip(
            spec=spec, lane=lane, job_id=job_id,
            ffmpeg=self._ffmpeg, ffprobe=self._ffprobe,
            scheduling=scheduling, leases=service.leases,
            on_progress=on_progress,
            cancelled=lambda: service._is_cancelled(job_id),
        )

        payload = json.dumps(receipt.to_dict(), indent=2, sort_keys=True).encode("utf-8")
        artifact = service.artifacts.put(
            payload,
            media_type="application/json; charset=utf-8",
            filename="%s-render-receipt.json" % job_id,
        )
        state = service.ledger.get_job(job_id) or state
        service._append("artifact.recorded", state, invocation_id=invocation_id,
                        artifacts=[{**artifact, "role": "result"}])

        if receipt.ok:
            service._append("invocation.succeeded", state, invocation_id=invocation_id)
            service._append("job.succeeded", state)
            return

        # A superseded clip is the revision guard WORKING, not a fault -- but it
        # did not produce a draft, so the job is not a success either. The
        # reason on the receipt says which happened, and the review UI uses it
        # to decide whether a retry is worth offering.
        service._append("invocation.failed", state, invocation_id=invocation_id,
                        reason=receipt.error or "render did not promote")
        service._append("job.failed", state, reason=receipt.error or "render failed")

    def _on_error(self, job_id, exc) -> None:
        if job_id is None:
            return
        state = self._service.ledger.get_job(job_id)
        if state is None:
            return
        try:
            self._service._append(
                "job.failed", state, reason="render worker crashed: %s" % (exc,)
            )
        except Exception:
            pass
