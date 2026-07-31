"""Execution-event contract and validation.

An execution event records one immutable fact about a Request, Job, or
Invocation. The ledger assigns the monotonic ``sequence`` when it appends the
event. Current state is derived by replaying these events; callers never edit a
job row in place.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .ids import new_event_id

EXECUTION_EVENT_SCHEMA = "hearth-execution-event.v1"

EVENT_TYPES = frozenset({
    "request.accepted",
    "request.rejected",
    "job.queued",
    "job.dispatched",
    "job.running",
    "job.waiting_for_input",
    "job.waiting_for_approval",
    "job.cancellation_requested",
    "job.succeeded",
    "job.failed",
    "job.cancelled",
    "job.expired",
    "invocation.started",
    "invocation.succeeded",
    "invocation.failed",
    "invocation.cancelled",
    "artifact.recorded",
    "delivery.projected",
})

JOB_STATUSES = frozenset({
    "accepted",
    "queued",
    "dispatched",
    "running",
    "waiting_for_input",
    "waiting_for_approval",
    "cancellation_requested",
    "succeeded",
    "failed",
    "cancelled",
    "rejected",
    "expired",
})
FINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled", "rejected", "expired"})

_ID_PATTERNS = {
    "event_id": re.compile(r"^evt_[a-f0-9]{32}$"),
    "request_id": re.compile(r"^req_[a-f0-9]{32}$"),
    "job_id": re.compile(r"^job_[a-f0-9]{32}$"),
    "invocation_id": re.compile(r"^inv_[a-f0-9]{32}$"),
}
_ARTIFACT_ID_PATTERN = re.compile(r"^art_[a-f0-9]{32}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

_EVENT_KEYS = frozenset({
    "schema",
    "sequence",
    "event_id",
    "timestamp",
    "event_type",
    "request_id",
    "job_id",
    "invocation_id",
    "principal",
    "source",
    "operation",
    "desired",
    "observed",
    "artifacts",
    "reason",
})


class ExecutionEventError(ValueError):
    """Raised when an execution event violates the frozen v1 contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_execution_event(
    event_type: str,
    *,
    request_id: str,
    job_id: str,
    invocation_id: Optional[str] = None,
    principal: Optional[Mapping[str, Any]] = None,
    source: Optional[Mapping[str, Any]] = None,
    operation: Optional[str] = None,
    desired: Optional[Mapping[str, Any]] = None,
    observed: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[list[Mapping[str, Any]]] = None,
    reason: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Build an event before the ledger assigns its sequence."""
    event = {
        "schema": EXECUTION_EVENT_SCHEMA,
        "sequence": None,
        "event_id": new_event_id(),
        "timestamp": timestamp or utc_now(),
        "event_type": event_type,
        "request_id": request_id,
        "job_id": job_id,
        "invocation_id": invocation_id,
        "principal": copy.deepcopy(dict(principal)) if principal is not None else None,
        "source": copy.deepcopy(dict(source)) if source is not None else None,
        "operation": operation,
        "desired": copy.deepcopy(dict(desired)) if desired is not None else None,
        "observed": copy.deepcopy(dict(observed)) if observed is not None else None,
        "artifacts": copy.deepcopy(list(artifacts or [])),
        "reason": reason,
    }
    validate_execution_event(event, allow_unsequenced=True)
    return event


def _require_id(event: Mapping[str, Any], field: str) -> None:
    value = event.get(field)
    pattern = _ID_PATTERNS[field]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ExecutionEventError(f"{field} must match {pattern.pattern}")


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ExecutionEventError("timestamp must be a non-empty RFC3339 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionEventError("timestamp must be a valid RFC3339 timestamp") from exc


def _validate_principal(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ExecutionEventError("principal must be an object or null")
    required = {"type", "id", "authenticated"}
    if set(value) != required:
        raise ExecutionEventError(
            "principal requires exactly type, id, and authenticated"
        )
    if not isinstance(value["type"], str) or not value["type"]:
        raise ExecutionEventError("principal.type must be a non-empty string")
    if not isinstance(value["id"], str) or not value["id"]:
        raise ExecutionEventError("principal.id must be a non-empty string")
    if not isinstance(value["authenticated"], bool):
        raise ExecutionEventError("principal.authenticated must be boolean")


def _validate_source(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ExecutionEventError("source must be an object or null")
    for field in ("transport", "adapter"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ExecutionEventError(f"source.{field} must be a non-empty string")


def validate_execution_event(event: Any, *, allow_unsequenced: bool = False) -> None:
    if not isinstance(event, dict):
        raise ExecutionEventError("execution event must be an object")
    keys = set(event)
    if keys != _EVENT_KEYS:
        raise ExecutionEventError(
            f"bad execution event keys: missing={sorted(_EVENT_KEYS - keys)} "
            f"extra={sorted(keys - _EVENT_KEYS)}"
        )
    if event["schema"] != EXECUTION_EVENT_SCHEMA:
        raise ExecutionEventError(f"schema must be {EXECUTION_EVENT_SCHEMA}")
    sequence = event["sequence"]
    if sequence is None and allow_unsequenced:
        pass
    elif not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise ExecutionEventError("sequence must be a positive integer")
    _require_id(event, "event_id")
    _require_id(event, "request_id")
    _require_id(event, "job_id")
    invocation_id = event["invocation_id"]
    if invocation_id is not None:
        if not isinstance(invocation_id, str) or not _ID_PATTERNS["invocation_id"].fullmatch(
            invocation_id
        ):
            raise ExecutionEventError(
                f"invocation_id must be null or match {_ID_PATTERNS['invocation_id'].pattern}"
            )
    _validate_timestamp(event["timestamp"])
    if event["event_type"] not in EVENT_TYPES:
        raise ExecutionEventError(f"unknown event_type: {event['event_type']!r}")
    _validate_principal(event["principal"])
    _validate_source(event["source"])
    if event["operation"] is not None and (
        not isinstance(event["operation"], str) or not event["operation"]
    ):
        raise ExecutionEventError("operation must be a non-empty string or null")
    for field in ("desired", "observed"):
        if event[field] is not None and not isinstance(event[field], dict):
            raise ExecutionEventError(f"{field} must be an object or null")
    if not isinstance(event["artifacts"], list) or not all(
        isinstance(item, dict) for item in event["artifacts"]
    ):
        raise ExecutionEventError("artifacts must be a list of objects")
    if event["reason"] is not None and not isinstance(event["reason"], str):
        raise ExecutionEventError("reason must be a string or null")

    if event["event_type"].startswith("invocation.") and invocation_id is None:
        raise ExecutionEventError("invocation events require invocation_id")
    if event["event_type"] == "artifact.recorded" and not event["artifacts"]:
        raise ExecutionEventError("artifact.recorded requires artifact metadata")
    if event["event_type"] == "artifact.recorded":
        for artifact in event["artifacts"]:
            artifact_id = artifact.get("artifact_id")
            digest = artifact.get("sha256")
            size = artifact.get("size")
            media_type = artifact.get("media_type")
            if not isinstance(artifact_id, str) or not _ARTIFACT_ID_PATTERN.fullmatch(
                artifact_id
            ):
                raise ExecutionEventError("artifact_id must be an opaque art_ identifier")
            if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
                raise ExecutionEventError("artifact sha256 must be 64 lowercase hex characters")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ExecutionEventError("artifact size must be a non-negative integer")
            if not isinstance(media_type, str) or not media_type:
                raise ExecutionEventError("artifact media_type must not be empty")
