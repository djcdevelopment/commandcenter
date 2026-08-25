"""Gateway side of the render lane: authority, admission, and state transitions.

    GATEWAY  owns authority, admission, job lifecycle, and the ledger.
    AGENT    owns GPU process execution, and nothing else.

The gateway never runs ffmpeg. It cannot -- it lives in Windows session 0, which
has no GPU adapter access -- and even if it could, two executors would be one too
many. So this module does three things and no more:

1. **Admission.** ``enqueue`` publishes a validated job to the handoff queue.
2. **Ingestion.** A poller turns the agent's claim/result sidecars into ledger
   events. Every state transition is written HERE, by the single process that
   owns the ledger.
3. **Reporting.** Whether an interactive executor is currently available.

Why the agent does not write the ledger itself: ``ExecutionLedger.append`` guards
with a **threading.Lock**, assigns sequence via ``SELECT MAX(sequence)+1``, and
writes at ``offset = stat().st_size``. A direct two-process test produced a
duplicate sequence number, sqlite ``OperationalError``, and a cascade of
``PermissionError`` that wedged the second writer -- 80 appends attempted, 18
events written. ``CapacityLeaseStore`` being cross-process safe does not make the
ledger so.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from hearth.execution.ids import new_invocation_id
from hearth.media import handoff
from hearth.media import lanes as lanes_mod

INGEST_SECONDS = 3.0

TERMINAL = {"succeeded", "failed", "cancelled", "rejected", "expired"}


class RenderSubsystem:
    """Admission + ingestion for `media.render`. Executes nothing."""

    def __init__(
        self,
        *,
        service,
        calibration_provider: Optional[Callable] = None,
        ingest_seconds: float = INGEST_SECONDS,
        autostart: bool = True,
    ) -> None:
        self._service = service
        self._calibration_provider = calibration_provider or lanes_mod.load_calibration
        self._ingest_seconds = ingest_seconds
        self._invocations: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        handoff.ensure_dirs()
        if autostart:
            self.start()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hearth-render-ingest", daemon=True
        )
        self._thread.start()

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        if self._thread is not None and wait:
            self._thread.join(timeout=10)
        self._thread = None

    # -------------------------------------------------------------- reporting

    def lanes(self) -> list:
        calibration = self._calibration_provider()
        if calibration is None:
            return []
        return calibration.healthy_lanes()

    def executor_status(self) -> handoff.AgentStatus:
        """Whether an interactive render executor is alive and able to render."""
        return handoff.agent_status()

    def progress_for(self, job_id: str) -> dict:
        claim = handoff.read_claim(job_id)
        if not claim:
            return {}
        return {
            "lane_id": claim.get("lane_id"),
            "started_at": claim.get("started_at"),
            "agent_pid": claim.get("pid"),
        }

    # -------------------------------------------------------------- admission

    def enqueue(self, job_id: str) -> None:
        """Publish a validated job for the interactive agent. Consumes no worker.

        Deliberately does NOT require an agent to be running. A job with no
        executor is still a valid job -- it stays queued until one appears, which
        is what makes "nobody is logged in" a pause rather than a failure.
        """
        state = self._service.ledger.get_job(job_id)
        if state is None:
            return
        desired = state.get("desired") or {}
        handoff.enqueue_job(
            job_id,
            desired.get("arguments") or {},
            deadline_s=(desired.get("policy") or {}).get("deadline_s"),
            principal=(state.get("principal") or {}).get("id"),
        )

    def cancel(self, job_id: str, *, reason: str = "cancelled by caller") -> dict:
        """Cancel a render job. Two outcomes, and the caller is told which.

        A job the agent has NOT claimed is stopped absolutely: its queue entry is
        removed, so no GPU work can ever start, and it terminalises here.

        A job already claimed cannot be stopped absolutely. The agent is a
        separate process in another session; the marker it reads is checked
        BETWEEN variants, so a variant already encoding finishes. The job stays
        non-terminal until the agent publishes its own result -- inventing a
        terminal event for work still running would make the ledger lie.

        Correctness never depends on either path: a superseded render is refused
        promotion by the commit-time revision check whether or not it is
        cancelled. This only saves GPU time.
        """
        state = self._service.ledger.get_job(job_id)
        if state is None:
            raise ValueError("unknown job_id: %s" % job_id)
        if state.get("status") in TERMINAL:
            return {"job_id": job_id, "status": state.get("status"),
                    "stopped_before_start": False, "already_terminal": True}

        started = handoff.read_claim(job_id) is not None
        handoff.request_cancel(job_id, reason=reason)
        # Registers the flag and appends job.cancellation_requested.
        self._service.cancel(job_id, reason=reason)

        if not started:
            handoff.dequeue_job(job_id)
            state = self._service.ledger.get_job(job_id) or state
            if state.get("status") not in TERMINAL:
                self._service._append("job.cancelled", state, reason=reason)
            handoff.clear_cancel(job_id)
        state = self._service.ledger.get_job(job_id) or state
        return {"job_id": job_id, "status": state.get("status"),
                "stopped_before_start": not started, "already_terminal": False}

    # -------------------------------------------------------------- ingestion

    def ingest(self) -> int:
        """Convert agent sidecars into ledger events. The gateway alone writes."""
        handled = 0
        for path in handoff.list_claims():
            record = handoff._read(path)
            if record is None:
                continue
            job_id = record.get("job_id") or path.stem
            if self._mark_running(job_id, record):
                handled += 1
        for path in handoff.list_results():
            record = handoff._read(path)
            if record is None:
                continue
            job_id = record.get("job_id") or path.stem
            if self._finalise(job_id, record):
                handled += 1
        return handled

    def _mark_running(self, job_id: str, claim: dict) -> bool:
        state = self._service.ledger.get_job(job_id)
        if state is None or state.get("status") in TERMINAL:
            return False
        if state.get("status") in ("dispatched", "running"):
            return False
        invocation_id = new_invocation_id()
        self._invocations[job_id] = invocation_id
        observed = {
            "provider": claim.get("lane_id"),
            "model": (claim.get("arguments") or {}).get("profile_version"),
            "routed_by": "render-agent",
            "occupancy": "leased",
            "agent_pid": claim.get("pid"),
            "session": claim.get("host_session"),
        }
        self._service._append("job.dispatched", state, observed=observed)
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("invocation.started", state,
                              invocation_id=invocation_id, observed=observed)
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("job.running", state)
        return True

    def _finalise(self, job_id: str, result: dict) -> bool:
        state = self._service.ledger.get_job(job_id)
        if state is None:
            handoff.clear_result(job_id)
            handoff.clear_claim(job_id)
            return False
        if state.get("status") in TERMINAL:
            handoff.clear_result(job_id)
            handoff.clear_claim(job_id)
            return False

        # A result can arrive for a job the gateway never saw start: the control
        # plane may have restarted mid-render, which must NOT kill or invalidate
        # the GPU process. Synthesise the missing transitions so the lifecycle
        # stays well-formed instead of jumping queued -> succeeded.
        claim = handoff.read_claim(job_id) or {"job_id": job_id}
        if state.get("status") not in ("dispatched", "running"):
            self._mark_running(job_id, claim)
            state = self._service.ledger.get_job(job_id) or state

        invocation_id = self._invocations.pop(job_id, None)
        if invocation_id is None:
            invocations = state.get("invocations") or []
            invocation_id = (invocations[-1]["invocation_id"] if invocations
                             else new_invocation_id())

        receipt = result.get("receipt") or {}
        payload = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8")
        artifact = self._service.artifacts.put(
            payload, media_type="application/json; charset=utf-8",
            filename="%s-render-receipt.json" % job_id,
        )
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("artifact.recorded", state,
                              invocation_id=invocation_id,
                              artifacts=[{**artifact, "role": "result"}])
        state = self._service.ledger.get_job(job_id) or state
        if result.get("ok"):
            self._service._append("invocation.succeeded", state,
                                  invocation_id=invocation_id)
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.succeeded", state)
        else:
            # A superseded clip is the revision guard WORKING, not a fault --
            # but it produced no draft, so the job is not a success either. The
            # receipt's reason says which happened.
            reason = result.get("reason") or "render did not promote"
            self._service._append("invocation.failed", state,
                                  invocation_id=invocation_id, reason=reason)
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.failed", state, reason=reason)
        handoff.clear_result(job_id)
        handoff.clear_claim(job_id)
        handoff.dequeue_job(job_id)
        handoff.clear_cancel(job_id)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.ingest()
            except Exception:
                pass
            self._stop.wait(self._ingest_seconds)
