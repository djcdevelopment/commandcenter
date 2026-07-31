"""HEARTH's canonical AI execution control plane.

The append-only :class:`ExecutionLedger` is the source of truth. Current job
state, invocation history, idempotency mappings, and the artifact index are
rebuildable projections of that history. Mutable capacity leases live beside
the ledger as disposable coordination state, never as canonical history.
"""

from .ids import new_artifact_id, new_event_id, new_invocation_id, new_job_id, new_request_id
from .artifacts import ArtifactStore, ArtifactStoreError
from .coordination import CapacityLeaseStore, CapacityUnavailable
from .ledger import ExecutionLedger, ExecutionLedgerError
from .operations import (
    ExecutionPolicy,
    Operation,
    OperationConfigError,
    OperationRegistry,
    load_operations,
)
from .service import ExecutionService, ExecutionServiceError
from .model import (
    EXECUTION_EVENT_SCHEMA,
    FINAL_JOB_STATUSES,
    JOB_STATUSES,
    ExecutionEventError,
    new_execution_event,
    validate_execution_event,
)

__all__ = [
    "EXECUTION_EVENT_SCHEMA",
    "FINAL_JOB_STATUSES",
    "JOB_STATUSES",
    "ExecutionEventError",
    "ArtifactStore",
    "ArtifactStoreError",
    "CapacityLeaseStore",
    "CapacityUnavailable",
    "ExecutionLedger",
    "ExecutionLedgerError",
    "ExecutionPolicy",
    "ExecutionService",
    "ExecutionServiceError",
    "Operation",
    "OperationConfigError",
    "OperationRegistry",
    "new_artifact_id",
    "new_event_id",
    "new_execution_event",
    "new_invocation_id",
    "new_job_id",
    "new_request_id",
    "load_operations",
    "validate_execution_event",
]
