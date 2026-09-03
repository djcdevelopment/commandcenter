"""Gateway-side admission and single-writer ingestion for image generation."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from hearth.execution.ids import new_invocation_id
from hearth.imagegen import handoff
from hearth.imagegen.session import ImageSessionController

TERMINAL = {"succeeded", "failed", "cancelled", "rejected", "expired"}
MAX_PENDING = 256


class ImageGenerationSubsystem:
    """Queue image jobs for the interactive agent and ingest its sidecars."""

    def __init__(self, *, service, ingest_seconds: float = 2.0,
                 autostart: bool = True, session: Optional[ImageSessionController] = None) -> None:
        self._service = service
        self._ingest_seconds = ingest_seconds
        self._invocations: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.session = session or ImageSessionController(
            autostart=autostart, cancel_all=self.cancel_all
        )
        handoff.ensure_dirs()
        self.reconcile_terminal_queue()
        if autostart:
            self.start()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hearth-imagegen-ingest", daemon=True
        )
        self._thread.start()

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        self.session.close()
        if self._thread is not None and wait:
            self._thread.join(timeout=10)
        self._thread = None

    def executor_status(self):
        return handoff.agent_status()

    def progress_for(self, job_id: str) -> dict:
        claim = handoff.read_claim(job_id)
        if not claim:
            return {}
        return {
            "lanes": claim.get("lane_ids") or [],
            "strategy": claim.get("strategy"),
            "started_at": claim.get("started_at"),
            "agent_pid": claim.get("agent_pid"),
            "session_id": claim.get("session_id"),
            "session_epoch": claim.get("session_epoch"),
        }

    def reconcile_terminal_queue(self) -> int:
        cleaned = 0
        for path in handoff.list_queued():
            state = self._service.ledger.get_job(path.stem)
            if state is not None and state.get("status") not in TERMINAL:
                continue
            handoff.dequeue_job(path.stem)
            handoff.clear_cancel(path.stem)
            cleaned += 1
        return cleaned

    def enqueue(self, job_id: str) -> None:
        if len(handoff.list_queued()) >= MAX_PENDING:
            raise RuntimeError("image generation queue is full")
        state = self._service.ledger.get_job(job_id)
        if state is None:
            return
        desired = state.get("desired") or {}
        metadata = desired.get("input_artifact")
        if not isinstance(metadata, dict):
            raise RuntimeError("image job has no private input artifact")
        spec = json.loads(self._service.artifacts.read(metadata).decode("utf-8"))
        handoff.enqueue_job(
            job_id, spec, deadline_s=(desired.get("policy") or {}).get("deadline_s"),
            principal=(state.get("principal") or {}).get("id"),
        )

    def cancel(self, job_id: str, *, reason: str = "cancelled by caller") -> dict:
        state = self._service.ledger.get_job(job_id)
        if state is None:
            raise ValueError("unknown job_id: %s" % job_id)
        if state.get("status") in TERMINAL:
            return {"job_id": job_id, "status": state.get("status"),
                    "stopped_before_start": False, "already_terminal": True}
        started = handoff.read_claim(job_id) is not None
        handoff.request_cancel(job_id, reason=reason)
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

    def cancel_all(self, reason: str) -> None:
        job_ids = {
            path.stem for path in [*handoff.list_queued(), *handoff.list_claims()]
        }
        for job_id in sorted(job_ids):
            try:
                self.cancel(job_id, reason=reason)
            except (ValueError, RuntimeError):
                # A concurrently-finalising job is handled by the ingester.
                continue

    def ingest(self) -> int:
        handled = 0
        for path in handoff.list_claims():
            record = handoff._read(path)
            if record is not None and self._mark_running(path.stem, record):
                handled += 1
        for path in handoff.list_results():
            record = handoff._read(path)
            if record is not None and self._finalise(path.stem, record):
                handled += 1
        return handled

    def _mark_running(self, job_id: str, claim: dict) -> bool:
        state = self._service.ledger.get_job(job_id)
        if state is None or state.get("status") in TERMINAL:
            return False
        if state.get("status") in {"dispatched", "running"}:
            return False
        invocation_id = new_invocation_id()
        self._invocations[job_id] = invocation_id
        lanes = claim.get("lane_ids") or []
        observed = {
            "provider": ",".join(str(item) for item in lanes),
            "model": claim.get("workflow_id"),
            "routed_by": "imagegen-agent",
            "backend": claim.get("backend"),
            "strategy": claim.get("strategy"),
            "session_id": claim.get("session_id"),
            "session_epoch": claim.get("session_epoch"),
            "agent_pid": claim.get("agent_pid"),
        }
        self._service._append("job.dispatched", state, observed=observed)
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("invocation.started", state, invocation_id=invocation_id,
                              observed=observed)
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("job.running", state)
        return True

    @staticmethod
    def _safe_output(path_value: str) -> Optional[Path]:
        output_root = Path(os.environ.get(
            "IMAGEGEN_OUTPUT_ROOT", r"E:\omen\imagegen\data\outputs"
        )).resolve()
        try:
            path = Path(path_value).resolve(strict=True)
            path.relative_to(output_root)
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    def _finalise(self, job_id: str, result: dict) -> bool:
        state = self._service.ledger.get_job(job_id)
        if state is None or state.get("status") in TERMINAL:
            self._cleanup(job_id)
            return False
        claim = handoff.read_claim(job_id) or {"job_id": job_id}
        if state.get("status") not in {"dispatched", "running"}:
            self._mark_running(job_id, claim)
            state = self._service.ledger.get_job(job_id) or state
        invocation_id = self._invocations.pop(job_id, None)
        if invocation_id is None:
            invocations = state.get("invocations") or []
            invocation_id = invocations[-1]["invocation_id"] if invocations else new_invocation_id()

        receipt = result.get("receipt") or {}
        artifacts = []
        receipt_artifact = self._service.artifacts.put(
            json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
            media_type="application/json; charset=utf-8",
            filename=job_id + "-imagegen-receipt.json",
        )
        artifacts.append({**receipt_artifact, "role": "result"})
        for index, item in enumerate(receipt.get("outputs") or []):
            path_value = item.get("path") if isinstance(item, dict) else None
            path = self._safe_output(path_value) if isinstance(path_value, str) else None
            if path is None:
                continue
            media_type = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
            artifact = self._service.artifacts.put(
                path.read_bytes(), media_type=media_type,
                filename="%s-%d%s" % (job_id, index, path.suffix.lower()),
            )
            artifacts.append({**artifact, "role": "output"})
        self._service._append("artifact.recorded", state, invocation_id=invocation_id,
                              artifacts=artifacts)
        state = self._service.ledger.get_job(job_id) or state
        observed = {
            key: receipt[key] for key in (
                "backend", "workflow_id", "strategy", "duration_ms",
                "session_id", "session_epoch", "lane_ids",
            ) if key in receipt
        }
        if result.get("ok"):
            self._service._append("invocation.succeeded", state,
                                  invocation_id=invocation_id, observed=observed)
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.succeeded", state, observed=observed)
        else:
            reason = result.get("reason") or "image generation failed"
            event = "invocation.cancelled" if result.get("cancelled") else "invocation.failed"
            self._service._append(event, state, invocation_id=invocation_id, reason=reason,
                                  observed=observed)
            state = self._service.ledger.get_job(job_id) or state
            terminal = "job.cancelled" if result.get("cancelled") else "job.failed"
            self._service._append(terminal, state, reason=reason, observed=observed)
        self._cleanup(job_id)
        return True

    @staticmethod
    def _cleanup(job_id: str) -> None:
        handoff.clear_result(job_id)
        handoff.clear_claim(job_id)
        handoff.dequeue_job(job_id)
        handoff.clear_cancel(job_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.ingest()
            except Exception:
                pass
            self._stop.wait(self._ingest_seconds)
