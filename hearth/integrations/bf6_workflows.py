"""HEARTH's deliberately thin BF6/Hatchet boundary.

HEARTH records acceptance, correlation, and one terminal outcome. Hatchet owns
the detailed task graph and retries; duplicating those states here would create
two schedulers and two conflicting sources of truth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.request import Request, urlopen

from hearth.execution import (
    FINAL_JOB_STATUSES,
    ExecutionLedger,
    new_artifact_id,
    new_execution_event,
    new_job_id,
    new_request_id,
)


SEGMENT_OPERATION = "bf6.process_segment"
RENDER_OPERATION = "bf6.render_clip_workflow"


class BF6WorkflowError(RuntimeError):
    pass


def _post_json(url: str, document: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def callback_signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class BF6WorkflowGateway:
    def __init__(
        self,
        *,
        ledger: Optional[ExecutionLedger] = None,
        forward: Optional[Callable[[str, Mapping[str, Any]], dict[str, Any]]] = None,
        orchestrator_url: Optional[str] = None,
        callback_base_url: Optional[str] = None,
    ) -> None:
        self.ledger = ledger or ExecutionLedger()
        self.forward = forward or _post_json
        self.orchestrator_url = (
            orchestrator_url
            or os.getenv("HEARTH_BF6_ORCHESTRATOR_URL", "http://127.0.0.1:18777")
        ).rstrip("/")
        self.callback_base_url = (
            callback_base_url
            or os.getenv("HEARTH_BF6_CALLBACK_BASE_URL", "http://127.0.0.1:8710")
        ).rstrip("/")

    def _append(self, event_type: str, request_id: str, job_id: str, **kwargs: Any) -> None:
        self.ledger.append(
            new_execution_event(
                event_type,
                request_id=request_id,
                job_id=job_id,
                **kwargs,
            )
        )

    def submit(self, document: Mapping[str, Any], *, caller: Any) -> dict[str, Any]:
        return self._submit(document, caller=caller, workflow_kind="segment")

    def submit_render(self, document: Mapping[str, Any], *, caller: Any) -> dict[str, Any]:
        return self._submit(document, caller=caller, workflow_kind="render")

    def _submit(
        self,
        document: Mapping[str, Any],
        *,
        caller: Any,
        workflow_kind: str,
    ) -> dict[str, Any]:
        session_id = str(document.get("session_id") or "").strip()
        media_namespace = str(document.get("media_namespace") or "").strip()
        if not session_id or not media_namespace:
            raise BF6WorkflowError("session_id and media_namespace are required")
        if workflow_kind == "segment":
            segment_path = str(document.get("segment_path") or "").strip()
            if not segment_path:
                raise BF6WorkflowError("segment_path is required")
            default_key = f"bf6:{media_namespace}:{session_id}:{Path(segment_path).name}"
            operation = SEGMENT_OPERATION
            endpoint = "segments"
        elif workflow_kind == "render":
            clip = document.get("clip")
            if not isinstance(clip, Mapping) or not clip.get("clip_id"):
                raise BF6WorkflowError("clip is required for a render workflow")
            revision = int(clip.get("clip_revision", 0))
            attempt = int(document.get("render_attempt", clip.get("render_attempts", 0)))
            default_key = (
                f"bf6:{media_namespace}:{clip['clip_id']}:r{revision}:a{attempt}"
            )
            operation = RENDER_OPERATION
            endpoint = "renders"
        else:
            raise BF6WorkflowError(f"unsupported BF6 workflow kind: {workflow_kind}")
        idempotency_key = str(document.get("idempotency_key") or default_key)
        previous = self.ledger.find_by_idempotency(idempotency_key)
        if previous is not None:
            return self._response(previous, idempotent=True)

        request_id, job_id = new_request_id(), new_job_id()
        principal = {
            "type": str(getattr(caller, "runner_class", "service")),
            "id": str(getattr(caller, "id", "hearth-internal")),
            "authenticated": True,
        }
        desired_arguments = {
            "session_id": session_id,
            "media_namespace": media_namespace,
            "render_mode": document.get("render_mode", "qsv"),
            "render_profile": document.get("render_profile", "bf6-qsv-v1"),
        }
        if workflow_kind == "segment":
            desired_arguments["segment_path"] = segment_path
        else:
            desired_arguments["clip_id"] = str(clip["clip_id"])
            desired_arguments["clip_revision"] = int(clip.get("clip_revision", 0))
            desired_arguments["render_attempt"] = attempt
        desired = {
            "idempotency_key": idempotency_key,
            "arguments": desired_arguments,
        }
        self._append(
            "request.accepted",
            request_id,
            job_id,
            principal=principal,
            source={"transport": "http", "adapter": "bf6-hatchet"},
            operation=operation,
            desired=desired,
        )
        self._append("job.queued", request_id, job_id)
        workflow_input: dict[str, Any] = {
            "schema_version": 2,
            "correlation": {
                "hearth_request_id": request_id,
                "hearth_job_id": job_id,
                "session_id": session_id,
                "traceparent": document.get("traceparent"),
            },
            "media_namespace": media_namespace,
            "callback_url": f"{self.callback_base_url}/integrations/bf6/outcomes/{job_id}",
            "render_profile": document.get("render_profile", "bf6-qsv-v1"),
            "render_mode": document.get("render_mode", "qsv"),
            "skip_model_signals": bool(document.get("skip_model_signals", False)),
        }
        if workflow_kind == "segment":
            workflow_input["segment_path"] = segment_path
        else:
            workflow_input["clip"] = dict(clip)
            workflow_input["render_attempt"] = attempt
            workflow_input.pop("skip_model_signals", None)
        try:
            accepted = self.forward(
                f"{self.orchestrator_url}/v1/workflows/bf6/{endpoint}", workflow_input
            )
            external_id = str(accepted["external_run_id"])
        except Exception as exc:
            self._append(
                "job.failed", request_id, job_id, reason=f"Hatchet intake failed: {exc}"
            )
            raise BF6WorkflowError(f"Hatchet intake failed: {exc}") from exc
        observed = {
            "external_run_id": external_id,
            "details_url": accepted.get("details_url"),
            "engine": "hatchet",
        }
        self._append("job.dispatched", request_id, job_id, observed=observed)
        self._append("job.running", request_id, job_id, observed=observed)
        state = self.ledger.get_job(job_id)
        assert state is not None
        return self._response(state, idempotent=False)

    @staticmethod
    def _response(state: Mapping[str, Any], *, idempotent: bool) -> dict[str, Any]:
        return {
            "request_id": state["request_id"],
            "job_id": state["job_id"],
            "status": state["status"],
            "external_run_id": state.get("external_run_id"),
            "details_url": state.get("details_url"),
            "idempotent": idempotent,
        }

    def receive_terminal(
        self,
        job_id: str,
        body: bytes,
        signature: str | None,
        *,
        secret: Optional[str] = None,
    ) -> dict[str, Any]:
        callback_secret = secret if secret is not None else os.getenv(
            "HEARTH_BF6_CALLBACK_SECRET", ""
        )
        if not callback_secret:
            raise BF6WorkflowError("HEARTH_BF6_CALLBACK_SECRET is not configured")
        expected = callback_signature(body, callback_secret)
        if not signature or not hmac.compare_digest(signature, expected):
            raise BF6WorkflowError("invalid callback signature")
        try:
            receipt = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BF6WorkflowError("callback body is not valid JSON") from exc
        state = self.ledger.get_job(job_id)
        if state is None:
            raise BF6WorkflowError(f"unknown job_id: {job_id}")
        if receipt.get("hearth_job_id") != job_id:
            raise BF6WorkflowError("receipt hearth_job_id does not match the route")
        if receipt.get("external_run_id") != state.get("external_run_id"):
            raise BF6WorkflowError("receipt external_run_id does not match dispatch")
        outcome = receipt.get("outcome")
        terminal_status = {
            "succeeded": "succeeded",
            "failed": "failed",
            "unknown": "expired",
        }.get(outcome)
        if terminal_status is None:
            raise BF6WorkflowError(f"unsupported terminal outcome: {outcome!r}")
        already_terminal = state["status"] in FINAL_JOB_STATUSES
        if already_terminal and state["status"] != terminal_status:
            raise BF6WorkflowError(
                f"receipt outcome {outcome!r} conflicts with terminal state {state['status']!r}"
            )

        request_id = state["request_id"]
        known_external_artifacts = {
            item.get("external_artifact_id") for item in state.get("artifacts", [])
        }
        artifacts = []
        for item in receipt.get("artifact_refs") or []:
            if item.get("artifact_id") in known_external_artifacts:
                continue
            artifacts.append({
                "artifact_id": new_artifact_id(),
                "media_type": (
                    "video/mp4" if item.get("kind") in {"horizontal", "vertical"} else "text/plain"
                ),
                "size": int(item["size"]),
                "sha256": str(item["sha256"]),
                "created_at": receipt.get("completed_at"),
                "kind": item.get("kind"),
                "path": item.get("path"),
                "external_artifact_id": item.get("artifact_id"),
            })
        if artifacts:
            self._append("artifact.recorded", request_id, job_id, artifacts=artifacts)
        observed = {
            "external_run_id": receipt.get("external_run_id"),
            "details_url": receipt.get("details_url"),
            "trace_id": receipt.get("trace_id"),
            "completed_at": receipt.get("completed_at"),
            "artifact_count": len(receipt.get("artifact_refs") or []),
        }
        if not already_terminal and outcome == "succeeded":
            self._append("job.succeeded", request_id, job_id, observed=observed)
        elif not already_terminal and outcome == "failed":
            self._append(
                "job.failed",
                request_id,
                job_id,
                observed=observed,
                reason=str(receipt.get("error_summary") or "external workflow failed"),
            )
        elif not already_terminal and outcome == "unknown":
            self._append(
                "job.expired",
                request_id,
                job_id,
                observed=observed,
                reason=str(receipt.get("error_summary") or "external outcome unknown"),
            )
        state = self.ledger.get_job(job_id)
        assert state is not None
        delivered = any(
            item.get("adapter") == "bf6-review"
            and item.get("external_run_id") == receipt.get("external_run_id")
            for item in state.get("deliveries", [])
        )
        if not delivered:
            self._append(
                "delivery.projected",
                request_id,
                job_id,
                observed={
                    "adapter": "bf6-review",
                    "external_run_id": receipt.get("external_run_id"),
                    "artifact_count": len(receipt.get("artifact_refs") or []),
                },
            )
        return {
            "accepted": True,
            "idempotent": already_terminal,
            "status": self.ledger.get_job(job_id)["status"],
        }

    def status(self, job_id: str) -> dict[str, Any]:
        state = self.ledger.get_job(job_id)
        if state is None:
            raise BF6WorkflowError(f"unknown job_id: {job_id}")
        return state
