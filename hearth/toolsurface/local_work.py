"""MCP surface for immutable, review-gated local repository work."""

from __future__ import annotations

import threading
from typing import Any, Callable

from hearth.execution.defaults import get_execution_service
from hearth.localwork import LocalWorkService
from hearth.observation.identity import DispatchIdentity, current_identity

_service: LocalWorkService | None = None
_lock = threading.Lock()


def _identity() -> DispatchIdentity:
    identity = current_identity()
    if identity is None:
        raise PermissionError("local-work tools require gateway caller identity")
    return identity


def _get_service() -> LocalWorkService:
    global _service
    with _lock:
        if _service is None:
            _service = LocalWorkService(get_execution_service())
        return _service


def _owned(work_id: str) -> tuple[LocalWorkService, dict[str, Any], DispatchIdentity]:
    identity = _identity()
    service = _get_service()
    manifest = service.get(work_id)
    owner = (manifest.get("caller") or {}).get("submitted_by")
    if identity.profile != "unrestricted" and owner != identity.caller_id:
        raise PermissionError("caller does not own this local work")
    return service, manifest, identity


def submit_local_work(intent: str, acceptance_criteria: list[str], repo: str,
                      base_commit: str, files: list[str], artifact_kind: str,
                      target_path: str | None = None, lane: str = "auto",
                      task_family: str | None = None, deadline_s: int = 900,
                      max_tokens: int | None = None, receipt_id: str | None = None,
                      idempotency_key: str | None = None) -> dict[str, Any]:
    """Submit bounded repository work to a strictly local lane for later review."""
    identity = _identity()
    return _get_service().submit(
        intent=intent, acceptance_criteria=acceptance_criteria, repo=repo,
        base_commit=base_commit, files=files, artifact_kind=artifact_kind,
        target_path=target_path, lane=lane, task_family=task_family,
        deadline_s=deadline_s, max_tokens=max_tokens, receipt_id=receipt_id,
        idempotency_key=idempotency_key, caller_id=identity.caller_id)


def watch_local_work(work_id: str, after_sequence: int = 0,
                     wait_seconds: float = 30) -> dict[str, Any]:
    """Return cursor-based local-work lifecycle events."""
    service, _, _ = _owned(work_id)
    # Reuse execution watch for the blocking behavior, then reconcile the domain view.
    manifest = service.get(work_id)
    if manifest["status"] not in {"awaiting_review", "accepted", "rejected", "superseded", "failed"}:
        state = service.execution.get_job(manifest["job_id"]) or {}
        service.execution.watch(job_id=manifest["job_id"], after_sequence=int(state.get("last_sequence", 0)),
                                wait_seconds=min(max(wait_seconds, 0), 30), limit=1)
    return service.watch(work_id, after_sequence=after_sequence)


def get_local_work(work_id: str) -> dict[str, Any]:
    """Return the current local-work-manifest.v1 projection."""
    _, manifest, _ = _owned(work_id)
    return manifest


def get_local_work_artifact(work_id: str) -> dict[str, Any]:
    """Verify and return the immutable review candidate."""
    service, _, _ = _owned(work_id)
    return service.artifact(work_id)


def record_local_work_verdict(work_id: str, decision: str,
                              criteria: list[dict[str, Any]], summary: str,
                              evidence: list[Any], receipt_id: str | None = None) -> dict[str, Any]:
    """Record the frontier caller's explicit candidate verdict."""
    service, _, identity = _owned(work_id)
    return service.verdict(work_id, decision=decision, criteria=criteria,
                           summary=summary, evidence=evidence, receipt_id=receipt_id,
                           caller_id=identity.caller_id)


def get_tools() -> list[Callable]:
    # Reconcile completed execution jobs as the provider is mounted, so a
    # gateway restart cannot strand work until a caller happens to poll it.
    _get_service().reconcile_all()
    return [submit_local_work, watch_local_work, get_local_work,
            get_local_work_artifact, record_local_work_verdict]
