"""HEARTH execution-control-plane tools.

These are protocol-neutral. Direct callers act as themselves; trusted adapter
profiles may submit on behalf of an authenticated downstream principal.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from hearth.execution import ExecutionService
from hearth.execution.defaults import get_execution_service
from hearth.observation.identity import DispatchIdentity, current_identity
from hearth.toolsurface.backends import load_pool

_DELEGATING_PROFILES = frozenset({"irc-adapter", "unrestricted"})


def _get_service() -> ExecutionService:
    return get_execution_service()


def _identity() -> DispatchIdentity:
    identity = current_identity()
    if identity is None:
        raise PermissionError("execution tools require gateway caller identity")
    return identity


def _direct_principal(identity: DispatchIdentity) -> dict[str, Any]:
    return {
        "type": "hearth_caller",
        "id": identity.caller_id,
        "authenticated": True,
    }


def _may_access(identity: DispatchIdentity, state: dict[str, Any]) -> bool:
    if identity.profile == "unrestricted":
        return True
    principal = state.get("principal") or {}
    if principal.get("type") == "hearth_caller" and principal.get("id") == identity.caller_id:
        return True
    source = state.get("source") or {}
    return (
        identity.profile in _DELEGATING_PROFILES
        and source.get("adapter") == identity.caller_id
    )


def _authorized_job(job_id: str) -> tuple[ExecutionService, dict[str, Any]]:
    service = _get_service()
    state = service.get_job(job_id)
    if state is None:
        raise ValueError(f"unknown job: {job_id}")
    if not _may_access(_identity(), state):
        raise PermissionError("caller does not own this execution")
    return service, state


def submit_execution(
    operation: str,
    arguments: dict[str, Any],
    policy: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Submit an Operation as the authenticated HEARTH caller."""
    identity = _identity()
    return _get_service().submit(
        operation_name=operation,
        arguments=arguments,
        principal=_direct_principal(identity),
        source={"transport": "mcp", "adapter": identity.caller_id},
        policy=policy,
        idempotency_key=idempotency_key,
    )


def plan_execution(
    operation: str,
    model: str | None = None,
    backend: str | None = None,
    prompt_bytes: int = 0,
    policy: dict[str, Any] | None = None,
    task_family: str | None = None,
    response_schema: dict[str, Any] | None = None,
    temperature: float | None = None,
    thinking: bool | None = None,
    procedure: str | None = None,
) -> dict[str, Any]:
    """Resolve Operation, Provider, and policy without content or dispatch.

    ``task_family`` (e.g. "quote_retrieval") consults the authored family
    evidence in ``hearth/etc/routing-families.toml``. With no caller signal the
    family routes — by the rung it names when that rung is pin-only, else by the
    family's tags — and ``routed_by`` comes back family-prefixed, exactly the
    route ``submit_execution`` would take for the same arguments (one helper
    decides for both, so a plan cannot promise a rung the dispatch would not
    use). A caller-supplied ``model``/``backend`` still wins, and the
    recommendation always rides back as ``family_recommendation``. Nothing is
    dispatched. Deliberate previews apply the same family thinking/procedure
    and policy defaults as submission; explicit overrides remain visible in
    ``regime_provenance``. ``planning_context`` timestamps the configuration
    and environment snapshot; availability may change before dispatch.
    """
    _identity()
    return _get_service().plan(
        operation_name=operation,
        model=model,
        backend=backend,
        prompt_bytes=prompt_bytes,
        policy=policy,
        task_family=task_family,
        response_schema=response_schema,
        temperature=temperature,
        thinking=thinking,
        procedure=procedure,
    )


def submit_delegated_execution(
    operation: str,
    arguments: dict[str, Any],
    principal_type: str,
    principal_id: str,
    source_transport: str,
    policy: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Submit for an authenticated downstream user through a trusted adapter."""
    identity = _identity()
    if identity.profile not in _DELEGATING_PROFILES:
        raise PermissionError(
            f"profile {identity.profile!r} may not delegate execution identity"
        )
    if principal_type not in {"irc_account", "service_account"}:
        raise ValueError("delegated principal_type must be irc_account or service_account")
    if source_transport not in {"irc", "https", "cli", "notebook"}:
        raise ValueError("unsupported delegated source_transport")
    return _get_service().submit(
        operation_name=operation,
        arguments=arguments,
        principal={
            "type": principal_type,
            "id": principal_id,
            "authenticated": True,
        },
        source={
            "transport": source_transport,
            # The authenticated gateway caller, not caller-supplied text, owns
            # the adapter attribution.
            "adapter": identity.caller_id,
        },
        policy=policy,
        idempotency_key=idempotency_key,
    )


def get_execution(job_id: str) -> dict[str, Any]:
    """Read the current projection of one owned Job."""
    _, state = _authorized_job(job_id)
    return state


def cancel_execution(job_id: str, reason: str = "cancelled by caller") -> dict[str, Any]:
    """Request cancellation of one owned Job."""
    service, _ = _authorized_job(job_id)
    return service.cancel(job_id, reason=reason)


def watch_execution(
    job_id: str,
    after_sequence: int = 0,
    wait_seconds: float = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Cursor-tail lifecycle events, optionally long-polling for up to 30 seconds."""
    service, _ = _authorized_job(job_id)
    events = service.watch(
        job_id=job_id,
        after_sequence=after_sequence,
        wait_seconds=wait_seconds,
        limit=limit,
    )
    next_sequence = events[-1]["sequence"] if events else after_sequence
    return {"events": events, "next_sequence": next_sequence}


def get_execution_artifact(artifact_id: str) -> dict[str, Any]:
    """Fetch an owned text result artifact with integrity metadata."""
    service = _get_service()
    job_id = service.ledger.artifact_job_id(artifact_id)
    if job_id is None:
        raise ValueError(f"unknown artifact: {artifact_id}")
    _authorized_job(job_id)
    metadata, content = service.read_artifact(artifact_id)
    if len(content) > 1024 * 1024:
        raise ValueError("artifact exceeds the 1 MiB inline retrieval limit")
    media_type = str(metadata.get("media_type", "application/octet-stream"))
    if not (media_type.startswith("text/") or "json" in media_type):
        raise ValueError("binary artifact retrieval requires a future artifact endpoint")
    return {**metadata, "content": content.decode("utf-8")}


def _execution_summary(state: dict[str, Any]) -> dict[str, Any]:
    desired = state.get("desired") or {}
    return {
        "job_id": state.get("job_id"),
        "request_id": state.get("request_id"),
        "status": state.get("status"),
        "submitted_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "operation": desired.get("operation", state.get("operation")),
        "task_id": (desired.get("arguments") or {}).get("task_id"),
        "reason": state.get("reason"),
        "artifacts": [
            {key: artifact[key] for key in ("artifact_id", "role", "media_type", "size", "sha256")
             if key in artifact}
            for artifact in (state.get("artifacts") or []) if isinstance(artifact, dict)
        ],
    }


def list_my_executions(
    task_id: str | None = None, status: str | None = None,
    limit: int = 20, before_sequence: int | None = None,
) -> dict[str, Any]:
    """Newest executions owned by this authenticated caller, with an exclusive page cursor.

    Even unrestricted callers see only their own direct principal here. This
    does not acknowledge, cancel, or resolve work. Fetch artifacts by their IDs.
    """
    identity = _identity()
    page = _get_service().ledger.list_principal_jobs(
        principal_type="hearth_caller", principal_id=identity.caller_id,
        task_id=task_id, status=status, limit=limit, before_sequence=before_sequence,
    )
    return {"executions": [_execution_summary(state) for state in page["jobs"]],
            "next_before_sequence": page["next_before_sequence"]}


def list_owned_executions(
    principal_type: str, principal_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """List redacted canonical executions for one delegated principal."""
    identity = _identity()
    if identity.profile not in _DELEGATING_PROFILES:
        raise PermissionError(
            f"profile {identity.profile!r} may not delegate execution identity"
        )
    if principal_type not in {"irc_account", "service_account"}:
        raise ValueError("unsupported delegated principal_type")
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise ValueError("principal_id must be a non-empty string")
    page = _get_service().ledger.list_principal_jobs(
        principal_type=principal_type, principal_id=principal_id, limit=limit,
        adapter=None if identity.profile == "unrestricted" else identity.caller_id,
    )
    return [_execution_summary(state) for state in page["jobs"]]


def list_operations() -> list[dict[str, Any]]:
    """List invocable Operations, not evidence-derived capabilities."""
    return [
        {
            "name": operation.name,
            "description": operation.description,
            "default_model": operation.default_model,
            "max_prompt_bytes": operation.max_prompt_bytes,
            "max_tokens_ceiling": operation.max_tokens_ceiling,
            "deadline_ceiling_s": operation.deadline_ceiling_s,
        }
        for operation in _get_service().operations.operations
    ]


def list_execution_providers() -> list[dict[str, Any]]:
    """List declared Providers, models, and safe runtime metadata.

    Retired rungs are withheld. This surface is projected verbatim into
    outward-facing places — the IRC `!hardware`/`!catalog` views and the public
    lab pages — so a tombstone listed here reads to a member as available
    hardware. Routing still sees the full pool; only the projection is filtered.
    """
    return [
        {
            "name": provider.name,
            "api": provider.api,
            "models": list(provider.models),
            "tags": list(provider.tags),
            "parallel_slots": provider.settings.get("parallel_slots"),
            "node": provider.settings.get("node"),
            "hardware_profile_id": provider.settings.get("hardware_profile_id"),
            "context_bytes": provider.settings.get("context_bytes"),
            "max_tokens": provider.settings.get("max_tokens"),
            "timeout_s": provider.settings.get("timeout_s"),
        }
        for provider in load_pool().backends
        if not provider.retired
    ]


def get_tools() -> list[Callable]:
    return [
        submit_execution,
        plan_execution,
        submit_delegated_execution,
        get_execution,
        cancel_execution,
        watch_execution,
        get_execution_artifact,
        list_owned_executions,
        list_my_executions,
        list_operations,
        list_execution_providers,
    ]
