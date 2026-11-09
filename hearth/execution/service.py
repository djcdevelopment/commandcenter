"""Unified HEARTH execution pipeline.

Every ingress adapter submits an Operation here. The scheduler resolves a
Provider, obtains global capacity, records each Invocation, stores result bytes
as artifacts, and projects only concise lifecycle data back to callers.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace as dc_replace
from typing import Any, Callable, Mapping, NamedTuple, Optional

from hearth.observation.identity import (DispatchIdentity, current_identity,
                                         current_traceparent, dispatch_identity,
                                         extract_trace_id, trace_context)
from hearth.toolsurface.backends import Backend, BackendConfigError, load_pool, select_backend
from hearth.scheduler.discovery import DiscoveryRefusal, adapter_error, shared_adapter

from .artifacts import ArtifactStore
from .coordination import CapacityLeaseStore, CapacityUnavailable
from .ids import new_invocation_id, new_job_id, new_request_id
from .ledger import ExecutionLedger, ExecutionLedgerError
from .model import FINAL_JOB_STATUSES, new_execution_event
from .operations import ExecutionPolicy, Operation, OperationConfigError, OperationRegistry
from .operations import DELIBERATE, load_operations

GenerateCallable = Callable[..., dict[str, Any]]


class ExecutionServiceError(RuntimeError):
    pass


class FamilyRoute(NamedTuple):
    """How one llm_chat submission is routed once the family evidence is read.

    One value, three consumers: ``plan`` (advice), ``submit`` (admission) and
    ``_run_job`` (dispatch) all build it the same way from the same arguments, so
    a plan that promised one rung and a job that used another is no longer
    reachable. C-05 filled the model in ``plan`` alone and left the two dispatch
    paths choosing for themselves -- which is exactly how the door came to stamp
    ``task_family`` on every ledger row without ever routing by it.

    ``model`` is what ``select_backend`` is ASKED for and is ``None`` on a tag
    route: passing a model there would take ``select_backend``'s by-model branch
    and the tag would never be consulted. ``preferred_model`` carries the
    family's recommendation instead, and ``resolve_model`` applies it once the
    provider is known -- never handing a rung a model it does not declare, which
    is the rung/model mismatch C-05 could produce.
    """

    backend: Optional[str]
    model: Optional[str]
    tags: Optional[list[str]]
    family_prefix: Optional[str]
    recommendation: Optional[dict[str, Any]]
    preferred_model: Optional[str] = None
    default_model: Optional[str] = None
    sizer: Optional[dict[str, Any]] = None          # ADR-0050: the sizer's answer when consulted
    admission_max_tokens: Optional[int] = None      # the sizer's output reserve for admission
    depth_override: bool = False
    recommendation_reason: Optional[str] = None

    def label(self, inner: str) -> str:
        """``routed_by`` for this route: the family prefix, then the inner reason.

        Same grammar as the primitive's (``family:<name>:tag:<t>``,
        ``family:<name>:pinned:<rung>``), so the dashboard's outermost-prefix
        rule counts door traffic in the same bucket as an in-process call. There
        is no ``family:<name>:escalation:...`` on this lane: the service
        dispatches once, and the primitive it calls is pinned, so the A2 climb
        never fires here.
        """
        mark = ""
        if self.sizer:
            mark = f"sizer:{self.sizer.get('source', 'heuristic')}:{self.sizer.get('output_class', '?')}:"
        if self.depth_override and self.backend:
            inner_reason = f"depth_override:{self.backend}"
            return f"{self.family_prefix}{mark}{inner_reason}" if self.family_prefix else f"{mark}{inner_reason}"
        if self.recommendation_reason and self.backend:
            inner_reason = f"{self.recommendation_reason}:{self.backend}"
            return f"{self.family_prefix}{mark}{inner_reason}" if self.family_prefix else f"{mark}{inner_reason}"
        return f"{self.family_prefix}{mark}{inner}" if self.family_prefix else f"{mark}{inner}"

    def resolve_model(self, provider: Backend) -> Optional[str]:
        """The model to dispatch, once ``select_backend`` has named the provider.

        A route that asked for a model by name keeps it. A tag route takes the
        family's recommended model when the chosen rung declares it, then the
        operation default on the same condition, then the rung's own first
        model -- the historical fallback. Every step checks ``provider.models``,
        so the answer is always a model that rung actually serves.
        """
        if self.model is not None:
            return self.model
        for candidate in (self.preferred_model, self.default_model):
            if candidate and candidate in provider.models:
                return candidate
        return provider.models[0] if provider.models else None

    def refusal(self, exc: BackendConfigError) -> str:
        """The routing error, saying which family sent the call where.

        Prefix only: ``str(exc)`` already carries the reason code, the payload
        size and every rung weighed (ADR-0031), and boundaries downstream match
        on those. A family route that cannot be served must never fall back to
        the operation default -- the caller asked to be routed by authored
        evidence, and a quiet substitution is the failure mode this lane exists
        to make visible.
        """
        if self.family_prefix is None or not self.recommendation:
            return str(exc)
        return (
            f"task family {self.recommendation['family']!r} recommends model "
            f"{self.recommendation['model_id']!r} on rung "
            f"{self.recommendation['backend_hint']!r}: {exc}"
        )


class ExecutionService:
    def __init__(
        self,
        *,
        ledger: Optional[ExecutionLedger] = None,
        artifacts: Optional[ArtifactStore] = None,
        leases: Optional[CapacityLeaseStore] = None,
        operations: Optional[OperationRegistry] = None,
        generate: Optional[GenerateCallable] = None,
        workers: int = 16,
        max_pending: int = 256,
        recover_pending: bool = True,
        render_dispatcher: Optional[Any] = None,
        image_dispatcher: Optional[Any] = None,
        media_dispatcher: Optional[Any] = None,
    ) -> None:
        self.ledger = ledger or ExecutionLedger()
        self.artifacts = artifacts or ArtifactStore()
        self.leases = leases or CapacityLeaseStore()
        self.operations = operations or load_operations()
        self._generate = generate
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="hearth-execution"
        )
        self._max_pending = max_pending
        # Optional. When absent, media.render submissions are refused outright
        # rather than accepted into a queue nothing drains -- a gateway without
        # the render subsystem should say so, not silently swallow work.
        self._render_dispatcher = render_dispatcher
        self._image_dispatcher = image_dispatcher
        self._media_dispatcher = media_dispatcher
        self._futures: dict[str, Future[None]] = {}
        self._cancel_requested: set[str] = set()
        self._lock = threading.RLock()
        self._route_lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        # Start the Consul watcher now (when configured) so the first opt-in request finds a
        # snapshot rather than racing an empty one (Codex M2 m6). Off families never read it.
        shared_adapter()
        if recover_pending:
            self.recover_pending()

    def close(self, *, wait: bool = True) -> None:
        for dispatcher in (
            self._render_dispatcher, self._media_dispatcher, self._image_dispatcher
        ):
            if dispatcher is not None:
                try:
                    dispatcher.close(wait=wait)
                except TypeError:
                    dispatcher.close()
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def recover_pending(self) -> int:
        """Reconcile non-terminal Jobs after a gateway restart."""
        recovered = 0
        states = self.ledger.list_jobs(
            statuses={
                "accepted",
                "queued",
                "dispatched",
                "running",
                "cancellation_requested",
            }
        )
        skipped_operations = {
            name.strip() for name in os.environ.get("HEARTH_RECOVERY_SKIP_OPERATIONS", "").split(",")
            if name.strip()
        }
        def job_is_final(job_id: str) -> bool:
            job = self.ledger.get_job(job_id)
            return job is not None and job["status"] in FINAL_JOB_STATUSES

        self.leases.release_stale(job_is_final=job_is_final)
        with self._lock:
            for state in states:
                if state.get("operation") in skipped_operations:
                    # Keep legacy work visible and queued; do not replay or erase it.
                    continue
                if (
                    (state.get("source") or {}).get("adapter") != "bf6-hatchet"
                    and (state.get("source") or {}).get("execution_mode") != "external"
                ):
                    # Any lease this Job holds was taken by the previous process,
                    # which is gone: drop it before the Job is closed or requeued,
                    # or it blocks a slot until its TTL (up to the deadline).
                    self.leases.release_job(state["job_id"])
                if (state.get("source") or {}).get("execution_mode") == "external":
                    # Ticketed or imported external work (steppewise-consul M3): checked BEFORE the
                    # deliberate branch, so an external attempt is never failed by the code path
                    # written for internal jobs. Its holder renews or its lease expires.
                    continue
                if state.get("operation") == DELIBERATE:
                    # A thinking turn costs up to an hour of a seat and is never replayed: close it
                    # (failed, or cancelled if the caller asked), with the reason on the record.
                    # Runs only at service start, before this process has dispatched anything.
                    for invocation in state["invocations"]:
                        if invocation["status"] == "running":
                            self._append(
                                "invocation.failed", state,
                                invocation_id=invocation["invocation_id"],
                                reason="scheduler restarted during a deliberate turn",
                            )
                    self._append(
                        "job.cancelled" if state["status"] == "cancellation_requested" else "job.failed",
                        state,
                        reason="inference.deliberate is never replayed after a gateway restart",
                    )
                    recovered += 1
                    continue
                if (state.get("source") or {}).get("adapter") == "bf6-hatchet":
                    # BF6WorkflowGateway owns Hatchet dispatch. A queued state
                    # can have an external run already; its signed callback owns
                    # reconciliation. Generic recovery must not resubmit it.
                    continue
                if (state.get("source") or {}).get("execution_mode") == "external":
                    # Imported observations describe work already attempted elsewhere.
                    # Only the importer may resume their interrupted lifecycle.
                    continue
                job_id = state["job_id"]
                if state["status"] == "cancellation_requested":
                    if self._is_media_job(state) and self._media_dispatcher is not None:
                        for invocation in state["invocations"]:
                            if invocation["status"] == "running":
                                self._append(
                                    "invocation.cancelled", state,
                                    invocation_id=invocation["invocation_id"],
                                    reason="cancellation reconciled after scheduler restart",
                                )
                                state = self.ledger.get_job(job_id) or state
                        self._media_dispatcher.enqueue(job_id)
                        recovered += 1
                        continue
                    self._append(
                        "job.cancelled",
                        state,
                        reason="cancellation reconciled after scheduler restart",
                    )
                    recovered += 1
                    continue
                if state["status"] in {"dispatched", "running"}:
                    for invocation in state["invocations"]:
                        if invocation["status"] == "running":
                            self._append(
                                "invocation.failed",
                                state,
                                invocation_id=invocation["invocation_id"],
                                reason="scheduler restarted during invocation",
                            )
                    self._append(
                        "job.queued",
                        state,
                        reason="requeued after scheduler restart",
                    )
                elif state["status"] == "accepted":
                    self._append("job.queued", state, reason="queued during recovery")
                if self._is_render_job(state) or self._is_image_job(state) or self._is_media_job(state):
                    # Recovered render jobs re-enter the render queue, never the
                    # shared executor. Their commit-time revision check runs
                    # again on the retry, so a job that was superseded while the
                    # gateway was down still refuses to promote.
                    dispatcher = self._render_dispatcher if self._is_render_job(state) else (
                        self._image_dispatcher if self._is_image_job(state)
                        else self._media_dispatcher
                    )
                    if dispatcher is None:
                        self._append(
                            "job.failed",
                            state,
                            reason="delegated job recovered but no matching dispatcher "
                                   "is configured",
                        )
                        continue
                    dispatcher.enqueue(job_id)
                else:
                    future = self._executor.submit(self._run, job_id)
                    self._futures[job_id] = future
                    future.add_done_callback(lambda _future, jid=job_id: self._forget(jid))
                recovered += 1
        return recovered

    def _is_render_job(self, state: Mapping[str, Any]) -> bool:
        """Whether a ledger job state belongs to the media_render handler."""
        name = state.get("operation")
        if not name:
            return False
        try:
            return self.operations.get(name).handler == "media_render"
        except (OperationConfigError, KeyError, ExecutionServiceError):
            return False

    def _is_image_job(self, state: Mapping[str, Any]) -> bool:
        """Whether a ledger job state belongs to the image_generate handler."""
        name = state.get("operation")
        if not name:
            return False
        try:
            return self.operations.get(name).handler == "image_generate"
        except (OperationConfigError, KeyError, ExecutionServiceError):
            return False

    def _is_media_job(self, state: Mapping[str, Any]) -> bool:
        """Whether a ledger job state belongs to the durable MediaGen handler."""
        name = state.get("operation")
        if not name:
            return False
        try:
            return self.operations.get(name).handler == "media_generate"
        except (OperationConfigError, KeyError, ExecutionServiceError):
            return False

    def _append(self, event_type: str, state: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        event = self.ledger.append(
            new_execution_event(
                event_type,
                request_id=state["request_id"],
                job_id=state["job_id"],
                **kwargs,
            )
        )
        with self._changed:
            self._changed.notify_all()
        return event

    @staticmethod
    def _validate_principal(principal: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(principal)
        if set(normalized) != {"type", "id", "authenticated"}:
            raise ExecutionServiceError("principal requires exactly type, id, authenticated")
        if not normalized["authenticated"]:
            raise PermissionError("execution requires an authenticated principal")
        if normalized["type"] not in {"hearth_caller", "irc_account", "service_account"}:
            raise ExecutionServiceError("unsupported principal type")
        if not isinstance(normalized["id"], str) or not normalized["id"].strip():
            raise ExecutionServiceError("principal id must not be empty")
        return normalized

    @staticmethod
    def _validate_source(source: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(source)
        if not isinstance(normalized.get("transport"), str) or not normalized["transport"]:
            raise ExecutionServiceError("source.transport must not be empty")
        if not isinstance(normalized.get("adapter"), str) or not normalized["adapter"]:
            raise ExecutionServiceError("source.adapter must not be empty")
        return normalized

    def _validate_arguments(
        self, operation: Operation, arguments: Mapping[str, Any]
    ) -> tuple[dict[str, Any], bytes]:
        normalized = copy.deepcopy(dict(arguments))
        if operation.handler == "media_render":
            # Delegated so the execution plane stays free of media specifics.
            # Runs HERE, on the caller's thread, because path containment reads
            # ContextVars that a pool worker does not inherit -- the same
            # reasoning as the `files=` packing note in submit().
            from hearth.media.jobspec import RenderArgumentError, validate_render_arguments

            try:
                return validate_render_arguments(operation, normalized)
            except RenderArgumentError as exc:
                raise ExecutionServiceError(str(exc)) from exc
        if operation.handler == "image_generate":
            from hearth.imagegen.jobspec import ImageArgumentError, validate_image_arguments

            try:
                return validate_image_arguments(operation, normalized)
            except ImageArgumentError as exc:
                raise ExecutionServiceError(str(exc)) from exc
        if operation.handler == "media_generate":
            from hearth.mediagen.jobspec import MediaArgumentError, validate_media_arguments

            try:
                return validate_media_arguments(operation, normalized)
            except MediaArgumentError as exc:
                raise ExecutionServiceError(str(exc)) from exc
        if operation.handler != "llm_chat":
            raise ExecutionServiceError(f"unsupported operation handler: {operation.handler}")
        if operation.name == DELIBERATE:
            return self._validate_deliberate(operation, normalized)
        allowed = {
            "prompt",
            "model",
            "backend",
            "endpoint",
            "system",
            "task",
            "files",
            "quality",
            "task_family",
            "task_id",  # C-06: ledger attribution only; steers nothing
            "traceparent",
            "image_path",
            "response_schema",
            "temperature",
        }
        unknown = set(normalized) - allowed
        if unknown:
            raise ExecutionServiceError(
                f"unknown {operation.name} arguments: {', '.join(sorted(unknown))}"
            )
        prompt = normalized.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ExecutionServiceError("prompt must be a non-empty string")
        encoded = prompt.encode("utf-8")
        if len(encoded) > operation.max_prompt_bytes:
            raise ExecutionServiceError(
                f"prompt is {len(encoded)} bytes; limit is {operation.max_prompt_bytes}"
            )
        for key in ("model", "backend", "endpoint", "system", "task", "quality",
                    "task_family", "traceparent"):
            value = normalized.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ExecutionServiceError(f"{key} must be a non-empty string")
        schema = normalized.get("response_schema")
        if "response_schema" in normalized and (not isinstance(schema, dict) or not schema):
            raise ExecutionServiceError("response_schema must be a non-empty JSON Schema object")
        temperature = normalized.get("temperature")
        if "temperature" in normalized and (
                isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not 0 <= temperature <= 2):
            raise ExecutionServiceError("temperature must be a number in [0, 2]")
        files = normalized.get("files")
        image_path = normalized.get("image_path")
        if image_path is not None and (not isinstance(image_path, str) or not image_path.strip()):
            raise ExecutionServiceError("image_path must be a non-empty path")
        if files is not None and (
            not isinstance(files, list)
            or not all(isinstance(item, str) and item for item in files)
        ):
            raise ExecutionServiceError("files must be a list of non-empty path strings")
        return normalized, encoded

    @staticmethod
    def _messages_bytes(messages: list[dict[str, Any]]) -> int:
        return sum(len(item["content"].encode("utf-8")) for item in messages)

    def _validate_deliberate(
        self, operation: Operation, normalized: dict[str, Any]
    ) -> tuple[dict[str, Any], bytes]:
        allowed = {"messages", "backend", "thinking", "temperature", "seed", "top_p",
                   "response_schema", "model", "task_id", "task_family", "procedure", "traceparent"}
        unknown = set(normalized) - allowed
        if unknown:
            raise ExecutionServiceError(
                f"unknown {operation.name} arguments: {', '.join(sorted(unknown))}"
            )
        messages = normalized.get("messages")
        if not isinstance(messages, list) or not messages or not all(
            isinstance(item, dict) and set(item) == {"role", "content"}
            and item["role"] in ("system", "user", "assistant")
            and isinstance(item["content"], str)
            for item in messages
        ):
            raise ExecutionServiceError(
                "messages must be a non-empty list of {role: system|user|assistant, content: str}"
            )
        if not any(item["content"].strip() for item in messages):
            raise ExecutionServiceError("messages must carry some content")
        family = normalized.get("task_family")
        if family is not None and (not isinstance(family, str) or not family.strip()):
            raise ExecutionServiceError("task_family must be a non-empty string")
        procedure = normalized.get("procedure")
        if procedure is not None and procedure not in ("one_call", "think_then_emit"):
            raise ExecutionServiceError("procedure must be 'one_call' or 'think_then_emit'")
        # With a task_family the door fills backend, thinking and procedure from the family's regime
        # (submit); without one the caller names them, as before.
        backend = normalized.get("backend")
        if backend is not None or family is None:
            if not isinstance(backend, str) or not backend.strip():
                raise ExecutionServiceError("backend is required and must be a non-empty string")
            provider = load_pool().by_name(backend)
            if provider is None or not provider.settings.get("deliberate_max_tokens"):
                raise ExecutionServiceError(
                    f"policy_refusal: backend {backend!r} does not declare deliberate_max_tokens; {operation.name} refuses it"
                )
        if "thinking" in normalized or family is None:
            if not isinstance(normalized.get("thinking"), bool):
                raise ExecutionServiceError("thinking is required and must be a boolean")
        if procedure == "think_then_emit" and normalized.get("thinking") is False:
            raise ExecutionServiceError("procedure 'think_then_emit' needs thinking")
        for key in ("model", "task_id"):
            value = normalized.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ExecutionServiceError(f"{key} must be a non-empty string")
        schema = normalized.get("response_schema")
        if "response_schema" in normalized and (not isinstance(schema, dict) or not schema):
            raise ExecutionServiceError("response_schema must be a non-empty JSON Schema object")
        # The ranges local_generate enforces at dispatch: temperature [0, 2], top_p (0, 1].
        for key, low_open, high in (("temperature", False, 2), ("top_p", True, 1)):
            value = normalized.get(key)
            if key in normalized and (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not 0 <= value <= high or (low_open and value == 0)):
                raise ExecutionServiceError(
                    f"{key} must be a number in {'(0' if low_open else '[0'}, {high}]")
        seed = normalized.get("seed")
        if "seed" in normalized and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ExecutionServiceError("seed must be an integer")
        encoded = json.dumps(messages, ensure_ascii=False).encode("utf-8")
        if len(encoded) > operation.max_prompt_bytes:
            raise ExecutionServiceError(
                f"messages are {len(encoded)} bytes; limit is {operation.max_prompt_bytes}"
            )
        return normalized, encoded

    def _apply_family_regime(
        self, operation: Operation, arguments: dict[str, Any], policy: Optional[Mapping[str, Any]],
        admit_bytes: int,
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        """Fill what a deliberate caller left to its task_family: the seat, thinking, the budget and the
        procedure. A caller who named any of them keeps it (and the regime only advises the rest).
        Refuses, rather than shrinking a thinking turn, when no live seat can hold the family's budget."""
        from hearth.execution.lab_config import is_backend_absent
        from hearth.scheduler.families import recommend as recommend_family

        family = arguments["task_family"]
        try:
            recommendation = recommend_family(family, admit_bytes // 4)
        except Exception as exc:  # noqa: BLE001 - loud: never route on a guess
            raise ExecutionServiceError(f"task family config error: {type(exc).__name__}: {exc}") from exc
        if recommendation.get("refused"):
            raise ExecutionServiceError(f"policy_refusal: {recommendation.get('refusal') or 'missing capability'}")
        regime = recommendation.get("regime")
        out = dict(arguments)
        policy_out = dict(policy or {})
        pool = load_pool()
        if out.get("backend") is None:
            names = [n for n in [recommendation.get("backend_hint"), *recommendation.get("providers", [])] if n]
            seats = [n for n in dict.fromkeys(names)
                     if pool.by_name(n) is not None and pool.by_name(n).settings.get("deliberate_max_tokens")
                     and not is_backend_absent(n)]
            if regime is not None:
                floor = regime["max_tokens"]
                seats = [n for n in seats if int(pool.by_name(n).settings["deliberate_max_tokens"]) >= floor]
            if not seats:
                raise ExecutionServiceError(
                    f"policy_refusal: no live seat for {family!r} declares deliberate_max_tokens"
                    + (f" >= {regime['max_tokens']}" if regime else "")
                    + f" (providers {recommendation.get('providers')})")
            if recommendation.get("discovery") == "enforce":
                # Consul-gated family (steppewise-consul M2): don't pin a seat here by lease count;
                # discovery admission picks among Consul-passing deliberate-capable seats, so a seat
                # in maintenance is routed around rather than pinned and refused (V5 finding).
                out["_deliberate_floor"] = int(regime["max_tokens"]) if regime is not None else 1
            else:
                picked_seat = seats[0]
                for s_name in seats:
                    s_prov = pool.by_name(s_name)
                    s_slots = self._parallel_slots(s_prov) if s_prov is not None else 1
                    if self.leases.active_count(f"provider:{s_name}") < s_slots:
                        picked_seat = s_name
                        break
                out["backend"] = picked_seat
        if regime is None:
            if not isinstance(out.get("thinking"), bool):
                raise ExecutionServiceError(
                    f"family {family!r} authors no regime: thinking is required and must be a boolean")
            return out, policy
        caller_named = "thinking" in out or "procedure" in out
        if not caller_named:
            out["thinking"] = regime["thinking"]
            out["procedure"] = regime["procedure"]
        if policy_out.get("max_tokens") is None:
            if out.get("backend") is None:
                # Deferred (discovery=enforce) selection: the regime budget itself; every candidate
                # must hold it (deliberate floor), so no pinned seat is consulted (Codex R6-high).
                policy_out["max_tokens"] = int(regime["max_tokens"])
            else:
                cap = int(pool.by_name(out["backend"]).settings["deliberate_max_tokens"])
                policy_out["max_tokens"] = min(regime["max_tokens"], cap)
        return out, policy_out

    def _normalize_regime(
        self, operation: Operation, arguments: dict[str, Any],
        policy: Optional[dict[str, Any]], admit_bytes: int,
    ) -> tuple[dict[str, Any], ExecutionPolicy, dict[str, str]]:
        """One content-free regime/policy resolution for preview and admission."""
        original_arguments, original_policy = dict(arguments), dict(policy or {})
        if operation.name == DELIBERATE and arguments.get("task_family") is not None:
            arguments, policy = self._apply_family_regime(operation, arguments, policy, admit_bytes)
        resolved = self.operations.policy_for(operation, policy)
        if operation.name == DELIBERATE:
            if not isinstance(arguments.get("thinking"), bool):
                raise ExecutionServiceError("thinking is required and must be a boolean")
            if arguments.get("procedure") == "think_then_emit" and not arguments["thinking"]:
                raise ExecutionServiceError("procedure 'think_then_emit' needs thinking")
            if resolved.max_tokens is None:
                raise ExecutionServiceError(f"{operation.name} requires policy.max_tokens")
            if arguments.get("backend") is None and arguments.get("_deliberate_floor") is not None:
                # discovery=enforce: no seat is picked yet, so the same cap is applied per candidate
                # at admission (every candidate's deliberate_max_tokens must hold this budget).
                arguments = {**arguments, "_deliberate_floor": max(int(arguments["_deliberate_floor"]),
                                                                    int(resolved.max_tokens))}
                cap = None
            else:
                cap = int(load_pool().by_name(arguments["backend"]).settings["deliberate_max_tokens"])
            if cap is not None and resolved.max_tokens > cap:
                raise ExecutionServiceError(
                    f"policy_refusal: max_tokens {resolved.max_tokens} exceeds {arguments['backend']} "
                    f"deliberate_max_tokens {cap}"
                )
        provenance = {}
        for key in ("backend", "model", "thinking", "procedure"):
            provenance[key] = ("caller" if original_arguments.get(key) is not None else
                               "family" if arguments.get(key) is not None else
                               "route_resolution" if key in {"backend", "model"} else "operation_default")
        for key in ("max_tokens", "deadline_s", "priority"):
            provenance[key] = ("caller" if original_policy.get(key) is not None else
                               "family" if (policy or {}).get(key) is not None else "operation_default")
        return arguments, resolved, provenance

    @staticmethod
    def _planning_context() -> dict[str, Any]:
        from fleet.environment import read_environment, stamp
        from hearth.execution.lab_config import active_configuration
        from datetime import datetime, timezone

        name, source = active_configuration()
        env = read_environment()
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "configuration": {"name": name, "source": source},
            "environment": {**stamp(env), "source": env["path"], "missing": env.get("missing", False)},
        }

    def _family_route(
        self,
        operation: Operation,
        arguments: Mapping[str, Any],
        prompt_bytes: int,
    ) -> FamilyRoute:
        """Resolve one llm_chat submission's route, family evidence included.

        Precedence, highest first::

            endpoint pin > backend pin > caller model > explicit quality/task
                         > task_family > operation default

        The first four are the caller speaking about THIS call; authored family
        evidence is a standing preference and yields to any of them (it is still
        stamped, so the advice that was overridden stays on the record). Note
        what ``quality``/``task`` do here: they do not route on this lane -- the
        service passes neither to ``select_backend`` -- but they are caller
        signals, so they suppress the family exactly as a pin does.

        With no caller signal the family routes: by an explicit named pin when
        the recommended rung carries no tags (opportunistic routing would never
        land there), otherwise by the family's tags. A caller-named ``model``
        suppressing the family is the C-05-R1 repair: sending the caller's model
        to a rung chosen for a different one produced a mismatch the router
        cannot see.

        Raises ``ExecutionServiceError`` when the evidence cannot be read --
        loudly, with no dispatch, rather than routing on a default nobody
        authored.
        """
        caller_model = arguments.get("model")
        backend = arguments.get("backend")
        task_family = arguments.get("task_family")
        default_model = operation.default_model
        if task_family is not None and (not isinstance(task_family, str) or not task_family.strip()):
            raise ExecutionServiceError("task_family must be a non-empty string")
        # ADR-0050: the sizer (None unless HEARTH_SIZER is on). `plan` carries no
        # prompt text, so it sizes on the declared family and byte count alone.
        sizer = None
        admission_max_tokens = None
        if "prompt" in arguments or task_family is not None or prompt_bytes:
            from hearth.sizer import size_request

            sizer = size_request(
                arguments.get("prompt") or "",
                system=arguments.get("system"),
                files=arguments.get("packed_files"),
                payload_bytes=prompt_bytes,
                task_family=task_family,
            )
        if sizer is not None:
            if task_family is not None and sizer.get("task_family") and sizer["task_family"] != task_family:
                task_family = sizer["task_family"]
            if sizer.get("expected_output_tokens"):
                admission_max_tokens = int(sizer["expected_output_tokens"])
        plain = FamilyRoute(
            backend=backend,
            model=caller_model or default_model,
            tags=None,
            family_prefix=None,
            recommendation=None,
            default_model=default_model,
            sizer=sizer,
            admission_max_tokens=admission_max_tokens,
        )
        if task_family is None:
            return plain
        # Local import: hearth.scheduler.__init__ pulls in the CP-SAT solver, and
        # admitting a job must not start depending on ortools being installed.
        from hearth.scheduler.families import recommend as recommend_family
        from hearth.scheduler.families import tags_for

        try:
            recommendation = recommend_family(task_family, prompt_bytes // 4)
        except Exception as exc:  # noqa: BLE001 — loud: never route on a guess
            raise ExecutionServiceError(
                f"task family config error: {type(exc).__name__}: {exc}") from exc
        caller_signalled = (
            backend is not None
            or arguments.get("endpoint") is not None
            or caller_model is not None
            or arguments.get("quality") is not None
            or arguments.get("task") is not None
        )
        if caller_signalled:
            return plain._replace(recommendation=recommendation)
        prefix = f"family:{recommendation['family']}:"
        if recommendation.get("refused"):
            return FamilyRoute(
                backend=None,
                model=None,
                tags=None,
                family_prefix=prefix,
                recommendation=recommendation,
                preferred_model=None,
                default_model=default_model,
                sizer=sizer,
                admission_max_tokens=admission_max_tokens,
            )
        if recommendation["pin_required"] and recommendation["backend_hint"]:
            return FamilyRoute(
                backend=recommendation["backend_hint"],
                model=recommendation["model_id"],
                tags=None,
                family_prefix=prefix,
                recommendation=recommendation,
                preferred_model=recommendation["model_id"],
                default_model=default_model,
                sizer=sizer,
                admission_max_tokens=admission_max_tokens,
            )
        if recommendation.get("depth_rule_applied") and recommendation.get("backend_hint"):
            return FamilyRoute(
                backend=recommendation["backend_hint"],
                model=recommendation["model_id"],
                tags=None,
                family_prefix=prefix,
                recommendation=recommendation,
                preferred_model=recommendation["model_id"],
                default_model=default_model,
                sizer=sizer,
                admission_max_tokens=admission_max_tokens,
                depth_override=True,
            )
        if recommendation.get("family") == "quote_retrieval" and (prompt_bytes // 4) >= 4096 and recommendation.get("backend_hint"):
            return FamilyRoute(
                backend=recommendation["backend_hint"],
                model=recommendation["model_id"],
                tags=None,
                family_prefix=prefix,
                recommendation=recommendation,
                preferred_model=recommendation["model_id"],
                default_model=default_model,
                sizer=sizer,
                admission_max_tokens=admission_max_tokens,
                recommendation_reason="evidence_floor",
            )
        return FamilyRoute(
            backend=None,
            model=None,
            tags=tags_for(recommendation["family"]),
            family_prefix=prefix,
            recommendation=recommendation,
            preferred_model=recommendation["model_id"],
            default_model=default_model,
            sizer=sizer,
            admission_max_tokens=admission_max_tokens,
        )

    def _select_for_route(
        self,
        pool: Any, route: FamilyRoute, payload_bytes: int,
        max_tokens: Optional[int] = None,
        exclude_job_id: Optional[str] = None,
    ) -> tuple[Backend, str, dict[str, Any]]:
        """``select_backend`` for a resolved route, with the family named on failure.

        ``max_tokens`` is the output reserve admission checks beside the prompt
        (ADR-0031 arithmetic): the job's own policy budget when it names one,
        else the sizer's expected output when consulted, else the rung default
        inside ``select_backend`` -- the same precedence the door applies.
        """
        try:
            from hearth.toolsurface.occupancy import check_occupancy, routing_reservation_context
            with routing_reservation_context(
                leases=self.leases,
                ledger=self.ledger,
                exclude_job_id=exclude_job_id,
            ):
                return select_backend(
                    pool,
                    backend=route.backend,
                    model=route.model,
                    tags=route.tags,
                    payload_bytes=payload_bytes,
                    max_tokens=max_tokens if max_tokens is not None else route.admission_max_tokens,
                    occupancy_check=check_occupancy,
                )
        except BackendConfigError as exc:
            raise ExecutionServiceError(route.refusal(exc)) from exc

    def _admit_select(
        self,
        pool: Any, route: FamilyRoute, payload_bytes: int,
        max_tokens: Optional[int] = None,
        exclude_job_id: Optional[str] = None,
        enforce_bytes: Optional[int] = None,
        procedure: Optional[str] = None,
        deliberate_floor: Optional[int] = None,
    ) -> tuple[Backend, str, Optional[dict[str, Any]]]:
        """Admission-time selection with Consul discovery for opt-in families (steppewise-consul M2).

        ``off`` (every family that doesn't opt in): exactly ``_select_for_route``, no record.
        ``shadow``: the normal selection, plus the same structured decision enforce would make.
        ``enforce``: candidates are the backends the FAMILY can use (its model or an authored
        ``discovery_models`` equivalent; its tags; a pin), intersected with Consul-passing
        instances whose ``max_model_len`` holds the request; refuse visibly
        (``discovery_*``, ``pin_not_eligible``, ``no_eligible_seat``) before any job or lease;
        return a binding that dispatch honours instead of selecting again.
        ``enforce_bytes`` is the full post-packing payload (Codex M2 M5).
        """
        rec = route.recommendation or {}
        mode = rec.get("discovery") or "off"
        if mode == "off":
            provider, routed_by, _ = self._select_for_route(
                pool, route, payload_bytes, max_tokens=max_tokens, exclude_job_id=exclude_job_id)
            return provider, routed_by, None
        size = enforce_bytes if enforce_bytes is not None else payload_bytes
        allowed_models = [m for m in (rec.get("discovery_models") or [rec.get("model_id")]) if m]
        capable = {b.name for b in pool.backends if self._deliberate_capable(b, deliberate_floor)}
        candidates = [
            b.name for b in pool.backends
            if b.name in capable
            and set(allowed_models).intersection(b.models)
            and (route.backend is None or b.name == route.backend)
            and (route.backend is not None or not route.tags or set(route.tags).intersection(b.tags or ()))
        ]
        # Output reserve as admission arithmetic uses it: the policy budget, else the sizer's
        # expectation, else the largest declared rung default among candidates (conservative).
        reserve = max_tokens if max_tokens is not None else route.admission_max_tokens
        if reserve is None:
            reserve = max((int(b.settings.get("max_tokens") or 0) for b in pool.backends if b.name in candidates), default=0)
        need_tokens = size // 4 + int(reserve or 0)
        adapter = shared_adapter()

        def decide() -> dict[str, Any]:
            if adapter is None:
                raise DiscoveryRefusal("discovery_unavailable", adapter_error() or "HEARTH_DISCOVERY_CONSUL not configured")
            return adapter.evaluate(pool, candidates, need_tokens=need_tokens)

        def record(decision: Optional[dict[str, Any]], provider: Optional[Backend], refusal: Optional[str]) -> dict[str, Any]:
            out: dict[str, Any] = {"mode": mode, "allowed_models": allowed_models, "candidates": candidates,
                                   "need_tokens": need_tokens}
            if decision is not None:
                out.update({k: decision[k] for k in ("discovery_source", "consul_index", "snapshot_age_ms")})
                out["eligible"] = sorted(decision["eligible"])
                out["refused"] = decision["refused"]
            if provider is not None:
                out["backend"] = provider.name
                inst = (decision or {}).get("eligible", {}).get(provider.name)
                if inst is not None:
                    out.update({"seat_id": inst["seat_id"], "instance_id": inst["id"], "generation": inst["generation"],
                                "route": inst["route"], "wire_model_id": inst["wire_model_id"], "alias": inst.get("alias")})
            if refusal is not None:
                out["refusal" if mode == "enforce" else "would_refuse"] = refusal
            return out

        unsupported = ("discovery_unsupported_procedure: think_then_emit is not admitted under discovery=enforce"
                       if procedure == "think_then_emit" else None)
        if mode == "shadow":
            provider, routed_by, _ = self._select_for_route(
                pool, route, payload_bytes, max_tokens=max_tokens, exclude_job_id=exclude_job_id)
            if unsupported is not None:   # same procedure check as enforce, recorded only (Codex R5-m2)
                return provider, routed_by, record(None, provider, unsupported)
            try:
                decision = decide()
            except DiscoveryRefusal as exc:
                rec = record(None, provider, str(exc))
                rec.update(exc.facts)
                return provider, routed_by, rec
            # The same checks enforce applies, recorded only (Codex R3-m4): eligibility, model
            # authorized AND served by the instance, and capacity for the effective budget.
            refusal = self._discovery_model_and_budget_refusal(provider, route, decision, allowed_models, size, max_tokens) \
                if provider.name in decision["eligible"] else \
                f"not_eligible: {decision['refused'].get(provider.name, 'not a family candidate')}"
            return provider, routed_by, record(decision, provider, refusal)
        # enforce
        if unsupported is not None:
            # The emit continuation's payload and budget are only known after the first call, so
            # it can't be bound to Consul capacity at admission; refused rather than half-checked (R4-M2).
            raise ExecutionServiceError(unsupported)
        try:
            decision = decide()
        except DiscoveryRefusal as exc:
            raise ExecutionServiceError(str(exc)) from exc
        eligible: dict[str, dict] = decision["eligible"]
        if route.backend is not None and route.backend not in eligible:
            reason = decision["refused"].get(route.backend) or "not a candidate for this family's models/tags"
            raise ExecutionServiceError(f"pin_not_eligible: {route.backend}: {reason}")
        if not eligible:
            raise ExecutionServiceError(
                f"no_eligible_seat: candidates={candidates} refused={json.dumps(decision['refused'], sort_keys=True)[:600]}")
        names = set(eligible)
        filtered = dc_replace(
            pool,
            backends=tuple(b for b in pool.backends if b.name in names),
            default=pool.default if pool.default in names else sorted(names)[0],
        )
        provider, routed_by, _ = self._select_for_route(
            filtered, route, size, max_tokens=max_tokens, exclude_job_id=exclude_job_id)
        if provider.name not in names:   # never a seat Consul didn't pass
            raise ExecutionServiceError(
                f"no_eligible_seat: selection produced {provider.name!r} outside {sorted(names)}")
        refusal = self._discovery_model_and_budget_refusal(provider, route, decision, allowed_models, size, max_tokens)
        if refusal is not None:
            raise ExecutionServiceError(f"no_eligible_seat: {refusal}")
        binding = record(decision, provider, None)
        binding["model"] = route.resolve_model(provider)
        # The generation budget transport will actually use (policy, else the rung default the
        # primitive applies, else its 1024), persisted and passed explicitly at dispatch (R3-M2).
        binding["reserve_tokens"] = self._discovery_budget(provider, max_tokens)
        return provider, f"discovery:{routed_by}", binding

    @staticmethod
    def _deliberate_capable(backend: Backend, floor: Optional[int]) -> bool:
        """Deferred deliberate selection (discovery=enforce): the seat must declare
        deliberate_max_tokens >= floor (regime floor raised to the request budget)."""
        if floor is None:
            return True
        try:
            return int(backend.settings.get("deliberate_max_tokens") or 0) >= int(floor)
        except (TypeError, ValueError):
            return False

    def _deliberate_pool(self, pool: Any, floor: Optional[int]) -> Any:
        """The pool restricted to deliberate-capable seats, shared by plan() and admission (R6-medium)."""
        if floor is None:
            return pool
        keep = tuple(b for b in pool.backends if self._deliberate_capable(b, floor))
        if not keep:
            raise ExecutionServiceError(
                f"policy_refusal: no seat declares deliberate_max_tokens >= {floor}")
        names = {b.name for b in keep}
        return dc_replace(pool, backends=keep, default=pool.default if pool.default in names else keep[0].name)

    @staticmethod
    def _discovery_budget(provider: Backend, max_tokens: Optional[int]) -> int:
        """The output budget the primitive will use: explicit policy, else rung setting, else 1024."""
        if max_tokens is not None:
            return int(max_tokens)
        try:
            return int(provider.settings.get("max_tokens") or 1024)
        except (TypeError, ValueError):
            return 1024

    def _discovery_model_and_budget_refusal(self, provider: Backend, route: FamilyRoute, decision: Mapping[str, Any],
                                            allowed_models: list, size: int, max_tokens: Optional[int]) -> Optional[str]:
        """Shared by shadow and enforce: the resolved model must be family-authored AND served by the
        bound instance, and input + the effective budget must fit its Consul max_model_len."""
        inst = decision["eligible"].get(provider.name) or {}
        model = route.resolve_model(provider)
        served = {m for m in (inst.get("wire_model_id"), inst.get("alias")) if m}
        if model not in allowed_models or model not in served:
            return (f"{provider.name!r} model {model!r}: family allows {allowed_models}, "
                    f"instance serves {sorted(served)}")
        need = size // 4 + self._discovery_budget(provider, max_tokens)
        mml = inst.get("max_model_len")
        if not (isinstance(mml, int) and mml > 0) or need > mml:
            return f"{provider.name!r} max_model_len {mml!r} < need {need} tokens (input + effective budget)"
        return None

    def _discovery_recheck(self, binding: Mapping[str, Any], route: FamilyRoute, payload_bytes: int) -> dict[str, Any]:
        """Dispatch-time validation of an admission binding; raises seat_lost_before_dispatch.

        Reloads the pool (never trusts an earlier provider object): the bound backend must still be
        declared at the bound route; the bound instance must still be registered, passing, same
        generation, same route and same wire-model/alias identity; the bound model must still be
        resolved and served; and the full request (input + the reserve persisted at admission)
        must fit the instance's Consul max_model_len and the bound seat's declared context
        (pin branch on a one-backend pool -- never a re-selection). Returns facts plus the
        validated provider, which dispatch uses, with its endpoint pinned for transport.
        """
        adapter = shared_adapter()
        if adapter is None:
            raise ExecutionServiceError("seat_lost_before_dispatch: discovery_unavailable: not configured")
        try:
            facts = adapter.recheck(binding["instance_id"], binding.get("generation"), binding.get("route"))
        except DiscoveryRefusal as exc:
            raise ExecutionServiceError(str(exc)) from exc
        inst = facts.pop("instance")
        bound_route = (binding.get("route") or "").rstrip("/")
        provider = load_pool().by_name(binding["backend"])
        if provider is None or provider.endpoint.rstrip("/") != bound_route:
            raise ExecutionServiceError(
                f"seat_lost_before_dispatch: backend {binding['backend']!r} now at "
                f"{provider.endpoint if provider else 'undeclared'} != bound route {bound_route}")
        if (inst.get("wire_model_id"), inst.get("alias")) != (binding.get("wire_model_id"), binding.get("alias")):
            raise ExecutionServiceError(
                f"seat_lost_before_dispatch: instance model identity changed bound="
                f"{binding.get('wire_model_id')}/{binding.get('alias')} now={inst.get('wire_model_id')}/{inst.get('alias')}")
        model = route.resolve_model(provider)
        if model != binding.get("model") or model not in {inst.get("wire_model_id"), inst.get("alias")}:
            raise ExecutionServiceError(f"seat_lost_before_dispatch: model drift bound={binding.get('model')} now={model}")
        reserve = int(binding.get("reserve_tokens") or 0)
        need = payload_bytes // 4 + reserve
        mml = inst.get("max_model_len")
        if not (isinstance(mml, int) and mml > 0) or need > mml:
            raise ExecutionServiceError(
                f"seat_lost_before_dispatch: bound seat max_model_len {mml!r} cannot hold need {need} tokens")
        try:   # declared context on the bound seat only, via the pin branch (never re-picks)
            only = dc_replace(load_pool(), backends=(provider,), default=provider.name)
            self._select_for_route(only, route._replace(backend=provider.name, tags=None, model=model),
                                   payload_bytes, max_tokens=reserve or None)
        except ExecutionServiceError as exc:
            raise ExecutionServiceError(f"seat_lost_before_dispatch: bound seat cannot hold the request: {exc}") from exc
        return {**facts, "need_tokens": need, "provider": provider}

    def submit(
        self,
        *,
        operation_name: str,
        arguments: Mapping[str, Any],
        principal: Mapping[str, Any],
        source: Mapping[str, Any],
        policy: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        principal_value = self._validate_principal(principal)
        source_value = self._validate_source(source)
        operation = self.operations.get(operation_name)
        arguments_value, prompt_bytes = self._validate_arguments(operation, arguments)
        deliberate = operation.name == DELIBERATE
        # Admission counts the message contents, not their JSON envelope.
        admit_bytes = self._messages_bytes(arguments_value["messages"]) if deliberate else len(prompt_bytes)
        # A render job has no model, no backend and no prompt to pack. It also
        # must not consume a shared pool worker -- see the dispatch branch below.
        is_render = operation.handler == "media_render"
        is_image = operation.handler == "image_generate"
        is_media = operation.handler == "media_generate"
        is_delegated = is_render or is_image or is_media
        dispatcher = self._render_dispatcher if is_render else (
            self._image_dispatcher if is_image else (
                self._media_dispatcher if is_media else None
            )
        )
        if is_delegated and dispatcher is None:
            raise ExecutionServiceError(
                "%s was submitted but this gateway has no matching dispatcher; "
                "refusing rather than queueing work nothing will execute" % operation.name
            )
        if arguments_value.get("files") and not is_delegated:
            # Resolve and pack under the gateway caller's active filesystem
            # scope before crossing the executor thread boundary. ContextVars
            # are not implicitly inherited by ThreadPoolExecutor workers.
            from hearth.toolsurface.inference import _pack_files

            packed, manifest = _pack_files(arguments_value["files"])
            arguments_value["prompt"] = f"{packed}\n\n{arguments_value['prompt']}"
            arguments_value.pop("files")
            arguments_value["packed_files"] = manifest
            prompt_bytes = arguments_value["prompt"].encode("utf-8")
            if len(prompt_bytes) > operation.max_prompt_bytes:
                raise ExecutionServiceError(
                    f"packed prompt is {len(prompt_bytes)} bytes; "
                    f"limit is {operation.max_prompt_bytes}"
                )
        arguments_value, policy_value, regime_provenance = self._normalize_regime(
            operation, arguments_value, policy, admit_bytes)
        backend = None if is_delegated else arguments_value.get("backend")
        endpoint = None if is_delegated else arguments_value.get("endpoint")
        pool = None if is_delegated else load_pool()
        if endpoint is not None:
            provider = pool.by_endpoint(endpoint)
            if provider is None:
                raise ExecutionServiceError(
                    "endpoint overrides must name a declared provider endpoint"
                )
            if backend is not None and backend != provider.name:
                raise ExecutionServiceError("backend and endpoint select different providers")
            backend = provider.name
            arguments_value["backend"] = backend
        route: Optional[FamilyRoute] = None
        discovery_record: Optional[dict[str, Any]] = None
        enforce_bytes: Optional[int] = None
        selected_provider: Optional[Backend] = None
        selected_routed_by: Optional[str] = None
        if not is_delegated:
            # Admission-time selection, resolved by the SAME helper _run_job uses
            # at dispatch, so a submission that would be refused (or family-pinned
            # onto a rung that cannot hold the payload) is refused here, before a
            # Job exists. Capacity for a render is a calibrated B70 lane, not a
            # model provider, so delegated handlers skip the whole path.
            route = self._family_route(operation, arguments_value, admit_bytes)
            if route.recommendation and route.recommendation.get("refused"):
                refusal = route.recommendation.get("refusal") or "missing capability"
                raise ExecutionServiceError(f"policy_refusal: {refusal}")
            # Full post-packing size for opt-in families (packed files are in prompt_bytes).
            # ...plus a separately supplied system string, which transport also sends (R3-M3).
            enforce_bytes = (admit_bytes if deliberate else len(prompt_bytes)) + len(
                (arguments_value.get("system") or "").encode("utf-8"))
            selected_provider, selected_routed_by, discovery_record = self._admit_select(
                pool,
                route,
                admit_bytes,
                max_tokens=policy_value.max_tokens,
                enforce_bytes=enforce_bytes,
                procedure=arguments_value.get("procedure"),
                deliberate_floor=arguments_value.get("_deliberate_floor"),
            )

        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ExecutionServiceError("idempotency_key must be a non-empty string")
            prior = self.ledger.find_by_idempotency(idempotency_key)
            if prior is not None:
                return prior

        with self._lock:
            active = sum(not future.done() for future in self._futures.values())
            if active >= self._max_pending:
                raise ExecutionServiceError("global execution queue is full")

        request_id = new_request_id()
        job_id = new_job_id()
        input_artifact = self.artifacts.put(
            prompt_bytes,
            media_type=(
                "application/json; charset=utf-8" if is_delegated
                else "text/plain; charset=utf-8"
            ),
            filename=(
                f"{job_id}-request.json" if is_delegated else f"{job_id}-prompt.txt"
            ),
        )

        with self._route_lock, self._lock:
            active = sum(not future.done() for future in self._futures.values())
            if active >= self._max_pending:
                raise ExecutionServiceError("global execution queue is full")

            if not is_delegated and route is not None:
                selected_provider, selected_routed_by, discovery_record = self._admit_select(
                    pool,
                    route,
                    admit_bytes,
                    max_tokens=policy_value.max_tokens,
                    exclude_job_id=job_id,
                    enforce_bytes=enforce_bytes,
                    procedure=arguments_value.get("procedure"),
                    deliberate_floor=arguments_value.get("_deliberate_floor"),
                )

            active_traceparent = (
                arguments_value.get("traceparent")
                or current_traceparent()
            )
            active_trace_id = extract_trace_id(active_traceparent)

            desired = {
                "operation": operation_name,
                "arguments": {
                    key: value
                    for key, value in arguments_value.items()
                    if key not in {"prompt", "packed_files", "_spec", "messages", "_deliberate_floor"}
                },
                "packed_files": arguments_value.get("packed_files", []),
                "input_artifact": input_artifact,
                "policy": {
                    "max_tokens": policy_value.max_tokens,
                    "deadline_s": policy_value.deadline_s,
                    "priority": policy_value.priority,
                },
                "idempotency_key": idempotency_key,
                "regime_provenance": regime_provenance,
                "planning_context": self._planning_context(),
            }
            if discovery_record is not None:
                # Only for families that opt in, so every other record stays byte-identical.
                desired["discovery"] = discovery_record
            trace_observed: Optional[dict[str, Any]] = None
            if active_traceparent:
                desired["traceparent"] = active_traceparent
                desired["trace_id"] = active_trace_id
                trace_observed = {
                    "traceparent": active_traceparent,
                    "trace_id": active_trace_id,
                }
            accepted_observed: Optional[dict[str, Any]] = dict(trace_observed) if trace_observed else None
            if selected_provider is not None:
                accepted_observed = dict(accepted_observed or {})
                accepted_observed["provider"] = selected_provider.name
                if selected_routed_by is not None and route is not None:
                    accepted_observed["routed_by"] = route.label(selected_routed_by)
                if discovery_record is not None:
                    accepted_observed["discovery"] = discovery_record
            initial = {
                "request_id": request_id,
                "job_id": job_id,
            }
            self._append(
                "request.accepted",
                initial,
                principal=principal_value,
                source=source_value,
                operation=operation_name,
                desired=desired,
                observed=accepted_observed,
            )
            self._append(
                "artifact.recorded",
                initial,
                artifacts=[{**input_artifact, "role": "input"}],
            )
            self._append("job.queued", initial, observed=accepted_observed)
            if is_delegated:
                # Handed to the render scheduler, which holds it as durable
                # queued state and only assigns a worker once a lane can
                # actually be leased. Submitting it to self._executor here
                # would reproduce the very bug this design avoids: a job
                # occupying one of the 16 shared workers while it waits for
                # capacity, starving llm.chat.
                dispatcher.enqueue(job_id)
            else:
                # WHO asked, captured on THIS thread. ContextVars are not inherited
                # by ThreadPoolExecutor workers -- the same boundary the files pack
                # above crosses by value. Without this the observation emitter finds
                # no identity in the worker and records nothing, so every door
                # dispatch since local_generate moved onto this pipeline produced no
                # capability evidence at all (ADR-0027; measured 2026-09-04: a
                # successful pinned door call wrote neither an observation nor an
                # exclusion row). Authority grants are deliberately NOT carried: a
                # background worker inherits the asker, not their filesystem reach.
                cur_ident = current_identity()
                if cur_ident is not None and active_traceparent and not cur_ident.traceparent:
                    cur_ident = DispatchIdentity(
                        caller_id=cur_ident.caller_id,
                        runner_class=cur_ident.runner_class,
                        node=cur_ident.node,
                        task_id=cur_ident.task_id,
                        profile=cur_ident.profile,
                        traceparent=active_traceparent,
                    )
                future = self._executor.submit(self._run, job_id, cur_ident, active_traceparent)
                self._futures[job_id] = future
                future.add_done_callback(lambda _future, jid=job_id: self._forget(jid))
        state = self.ledger.get_job(job_id)
        assert state is not None
        return state

    def plan(
        self,
        *,
        operation_name: str,
        model: Optional[str] = None,
        backend: Optional[str] = None,
        prompt_bytes: int = 0,
        policy: Optional[dict[str, Any]] = None,
        task_family: Optional[str] = None,
        response_schema: Optional[dict[str, Any]] = None,
        temperature: Optional[float] = None,
        thinking: Optional[bool] = None,
        procedure: Optional[str] = None,
    ) -> dict[str, Any]:
        """Resolve policy and provider without storing content or dispatching work.

        ``task_family`` consults the authored family evidence
        (``hearth/etc/routing-families.toml``) through the SAME ``_family_route``
        helper ``submit`` and ``_run_job`` use, so what a plan promises is what a
        submission would do — a plan that could disagree with the dispatch is
        worse than no plan, because it is believed. A caller-supplied
        ``model``/``backend`` still wins, and the recommendation always rides
        back as ``family_recommendation`` so the caller can see the advice that
        was not taken. Still content-free: no dispatch, no ledger row.
        """
        operation = self.operations.get(operation_name)
        if (
            not isinstance(prompt_bytes, int)
            or isinstance(prompt_bytes, bool)
            or prompt_bytes < 0
            or prompt_bytes > operation.max_prompt_bytes
        ):
            raise ExecutionServiceError(
                f"prompt_bytes must be between 0 and {operation.max_prompt_bytes}"
            )
        arguments = {key: value for key, value in {
            "model": model, "backend": backend, "task_family": task_family,
            "thinking": thinking, "procedure": procedure,
        }.items() if value is not None}
        if operation.name == DELIBERATE:
            # Validate controls exactly as submit does, without accepting/storing content.
            self._validate_deliberate(operation, {
                **arguments, "messages": [{"role": "user", "content": "preview"}]})
        elif thinking is not None or procedure is not None:
            raise ExecutionServiceError("thinking/procedure preview requires inference.deliberate")
        arguments, resolved_policy, provenance = self._normalize_regime(
            operation, arguments, policy, prompt_bytes)
        planning_context = self._planning_context()
        route = self._family_route(operation, arguments, prompt_bytes)
        if route.recommendation and route.recommendation.get("refused"):
            refusal = route.recommendation.get("refusal") or "missing capability"
            return {
                "operation": operation.name,
                "provider": None,
                "model": None,
                "routed_by": "policy_refusal",
                "occupancy": "unknown",
                "global_parallel_slots": 0,
                "policy": {
                    "max_tokens": resolved_policy.max_tokens,
                    "deadline_s": resolved_policy.deadline_s,
                    "priority": resolved_policy.priority,
                },
                "task_family": task_family,
                "family_recommendation": route.recommendation,
                "regime_provenance": provenance,
                "planning_context": planning_context,
                "dispatch": False,
                "ok": False,
                "error": f"policy_refusal: {refusal}",
                "error_code": "policy_refusal",
                "refusal": refusal,
            }
        try:
            provider, routed_by, occupancy = self._select_for_route(
                self._deliberate_pool(load_pool(), arguments.get("_deliberate_floor")),
                route, prompt_bytes, max_tokens=resolved_policy.max_tokens
            )
        except ExecutionServiceError as exc:
            err_msg = str(exc)
            if "marked absent under active configuration" in err_msg or "lane_absent" in err_msg:
                return {
                    "operation": operation.name,
                    "provider": None,
                    "model": None,
                    "routed_by": "policy_refusal",
                    "occupancy": "absent",
                    "global_parallel_slots": 0,
                    "policy": {
                        "max_tokens": resolved_policy.max_tokens,
                        "deadline_s": resolved_policy.deadline_s,
                        "priority": resolved_policy.priority,
                    },
                    "task_family": task_family,
                    "family_recommendation": route.recommendation,
                    "regime_provenance": provenance,
                    "planning_context": planning_context,
                    "dispatch": False,
                    "ok": False,
                    "error": f"policy_refusal: {err_msg}",
                    "error_code": "policy_refusal",
                    "refusal": err_msg,
                }
            raise
        resolved_model = route.resolve_model(provider)
        routed_by = route.label(routed_by)
        family_recommendation = route.recommendation
        from hearth.execution.capabilities import get_capability_slice
        capability_slice = get_capability_slice(provider.name, task_family)
        res = {
            "operation": operation.name,
            "provider": provider.name,
            "model": resolved_model,
            "routed_by": routed_by,
            "occupancy": occupancy.get("occupancy", "unknown"),
            "global_parallel_slots": self._parallel_slots(provider),
            "policy": {
                "max_tokens": resolved_policy.max_tokens,
                "deadline_s": resolved_policy.deadline_s,
                "priority": resolved_policy.priority,
            },
            "task_family": task_family,
            "family_recommendation": family_recommendation,
            "capability": capability_slice,
            "thinking": arguments.get("thinking"),
            "procedure": arguments.get("procedure", "one_call") if operation.name == DELIBERATE else None,
            "regime_provenance": provenance,
            "planning_context": planning_context,
            "dispatch": False,
        }
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
                raise ExecutionServiceError("temperature must be a number in [0, 2]")
            res["temperature"] = temperature
        if response_schema is not None:
            if not isinstance(response_schema, dict) or not response_schema:
                raise ExecutionServiceError("response_schema must be a non-empty JSON Schema object")
            from hearth.toolsurface.inference import response_schema_digest
            res["response_schema_sha256"] = response_schema_digest(response_schema)
            res["structured_outputs"] = provider.settings.get("structured_outputs") is True
        if occupancy.get("lane_not_live"):
            res["lane_status"] = "lane_not_live"
            res["note"] = occupancy.get("lane_reason", "lane is not live under active configuration")
        if occupancy.get("fallback_from"):
            res["fallback_from"] = occupancy["fallback_from"]   # ADR-0066: same provider and reason as dispatch
        return res

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
            self._cancel_requested.discard(job_id)
            self._changed.notify_all()

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel_requested

    @staticmethod
    def _parallel_slots(provider: Backend) -> int:
        try:
            value = int(provider.settings.get("parallel_slots", 1))
        except (TypeError, ValueError):
            value = 1
        return max(1, min(value, 128))

    def _generate_call(self, **kwargs: Any) -> dict[str, Any]:
        if self._generate is not None:
            return self._generate(**kwargs)
        from hearth.toolsurface.inference import local_generate

        return local_generate(**kwargs)

    def _run(
        self,
        job_id: str,
        identity: Optional[DispatchIdentity] = None,
        traceparent: Optional[str] = None,
    ) -> None:
        """Run one job on an executor worker under the submitting caller's identity.

        ``identity`` is None for jobs recovered at boot -- there is no live caller to
        attribute those to, and ``dispatch_identity(None)`` is a no-op, so they record
        nothing rather than borrowing someone else's name.
        """
        with trace_context(traceparent), dispatch_identity(identity):
            self._run_job(job_id)

    def _run_job(self, job_id: str) -> None:
        state = self.ledger.get_job(job_id)
        if state is None or state["status"] in FINAL_JOB_STATUSES:
            return
        if (state.get("source") or {}).get("execution_mode") == "external":
            return
        desired = state["desired"]
        operation = self.operations.get(state["operation"])
        arguments = dict(desired["arguments"])
        job_traceparent = (
            state.get("traceparent")
            or desired.get("traceparent")
            or arguments.get("traceparent")
            or current_traceparent()
        )
        job_trace_id = (
            state.get("trace_id")
            or desired.get("trace_id")
            or extract_trace_id(job_traceparent)
        )
        trace_obs = (
            {"traceparent": job_traceparent, "trace_id": job_trace_id}
            if job_traceparent
            else {}
        )
        prompt_metadata = desired["input_artifact"]
        prompt = self.artifacts.read(prompt_metadata).decode("utf-8")
        policy = ExecutionPolicy(**desired["policy"])
        deliberate = operation.name == DELIBERATE
        messages = json.loads(prompt) if deliberate else None
        payload_bytes = self._messages_bytes(messages) if deliberate else len(prompt.encode("utf-8"))
        started_waiting = time.monotonic()
        deadline = started_waiting + policy.deadline_s
        invocation_id = new_invocation_id()
        provider: Optional[Backend] = None
        lease_id: Optional[str] = None
        route = FamilyRoute(None, None, None, None, None)
        call_started: Optional[float] = None
        model: Optional[str] = None

        try:
            # The sizer reads the instruction, so the stored prompt rides along
            # (arguments never carry it past submit); popped again below.
            route = self._family_route(
                operation, {**arguments, "prompt": prompt,
                            "packed_files": desired.get("packed_files") or []},
                payload_bytes)
            if route.recommendation and route.recommendation.get("refused"):
                refusal = route.recommendation.get("refusal") or "missing capability"
                raise ExecutionServiceError(f"policy_refusal: {refusal}")
            binding = desired.get("discovery") if isinstance(desired.get("discovery"), dict) else None
            enforce_binding = binding is not None and binding.get("mode") == "enforce"
            with self._route_lock:
                if enforce_binding and arguments.get("procedure") == "think_then_emit":
                    raise ExecutionServiceError(
                        "seat_lost_before_dispatch: think_then_emit is not supported under discovery=enforce")
                if enforce_binding:
                    # Selected once, at admission (M2): dispatch never re-picks a seat.
                    provider = load_pool().by_name(binding["backend"])
                    if provider is None:
                        raise ExecutionServiceError(
                            f"seat_lost_before_dispatch: bound backend {binding['backend']!r} no longer declared")
                    enforce_payload = payload_bytes + len((arguments.get("system") or "").encode("utf-8"))
                    provider = self._discovery_recheck(binding, route, enforce_payload)["provider"]
                    routed_by, occupancy = f"discovery:bound:{binding['seat_id']}", {"occupancy": "bound"}
                else:
                    provider, routed_by, occupancy = self._select_for_route(
                        load_pool(), route, payload_bytes, max_tokens=policy.max_tokens,
                        exclude_job_id=job_id,
                    )
                routed_by = route.label(routed_by)
                # Which routed_by the Invocation (and therefore execute_sync's
                # compatibility projection) reports. The primitive is handed
                # `backend=<provider>` below, so it always answers `pinned:<provider>`
                # -- an echo of the service's own pin, which says nothing about WHY
                # that rung was chosen. For a family-routed job the service's label
                # wins; with no family the primitive's string stands, byte-identical
                # to every door result before this repair.
                family_routed_by = routed_by if route.family_prefix is not None else None
                model = route.resolve_model(provider)
                if model is None:
                    raise ExecutionServiceError(
                        f"provider {provider.name!r} declares no default model"
                    )
                # Capacity belongs to the provider endpoint, not to a model label:
                # multiple aliases/models on one llama.cpp process share slots.
                scope = f"provider:{provider.name}"
                remaining = deadline - time.monotonic()
                if not self._is_cancelled(job_id) and remaining > 0:
                    try:
                        lease_id = self.leases.acquire(
                            scope=scope,
                            job_id=job_id,
                            invocation_id=invocation_id,
                            limit=self._parallel_slots(provider),
                            ttl_seconds=max(30, remaining + 30),
                        )
                    except CapacityUnavailable:
                        lease_id = None
            while lease_id is None:
                if self._is_cancelled(job_id):
                    self._append("job.cancelled", state, reason="cancelled while queued")
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._append("job.expired", state, reason="deadline expired in queue")
                    return
                try:
                    lease_id = self.leases.acquire(
                        scope=scope,
                        job_id=job_id,
                        invocation_id=invocation_id,
                        limit=self._parallel_slots(provider),
                        ttl_seconds=max(30, remaining + 30),
                    )
                except CapacityUnavailable:
                    time.sleep(min(0.25, max(0.01, remaining)))

            dispatch_observed = {
                "provider": provider.name,
                "model": model,
                "routed_by": routed_by,
                "occupancy": occupancy.get("occupancy", "unknown"),
                "queue_ms": round((time.monotonic() - started_waiting) * 1000),
                "lease_id": lease_id,
            }
            if route.recommendation is not None:
                # Beside routed_by on the Job record, so get_execution shows the
                # evidence this route was decided on -- including when the family
                # was overridden by a caller signal and only advised. Added ONLY
                # when a family was asked for: a submission that never named one
                # keeps a byte-identical record.
                dispatch_observed["task_family"] = arguments["task_family"]
                dispatch_observed["family_recommendation"] = route.recommendation
            if route.sizer is not None:
                # ADR-0050: only when consulted, so an unsized job's record is
                # byte-identical; `observed` is an open object on this schema.
                dispatch_observed["sizer"] = route.sizer
            from hearth.execution.capabilities import get_capability_slice
            cap_slice = get_capability_slice(provider.name, arguments.get("task_family"))
            if cap_slice:
                dispatch_observed["capability"] = cap_slice
            if occupancy.get("lane_not_live"):
                dispatch_observed["lane_status"] = "lane_not_live"
            if occupancy.get("fallback_from"):
                dispatch_observed["fallback_from"] = occupancy["fallback_from"]
            if trace_obs:
                dispatch_observed.update(trace_obs)
            if enforce_binding:
                # The lease is held; this is the dispatch moment. Re-check the bound
                # instance once more so a seat lost while queued is refused, not called.
                check = self._discovery_recheck(binding, route, enforce_payload)
                provider = check.pop("provider")   # the freshly validated object; transport uses its endpoint
                dispatch_observed["discovery"] = {**binding, "dispatch_check": check}
            elif binding is not None:
                dispatch_observed["discovery"] = binding
            self._append("job.dispatched", state, observed=dispatch_observed)
            self._append(
                "invocation.started",
                state,
                invocation_id=invocation_id,
                observed=dispatch_observed,
            )
            self._append("job.running", state, observed=trace_obs or None)
            call_arguments = {
                "prompt": prompt,
                "model": model,
                "backend": provider.name,
                # Enforce: the exact budget validated against Consul capacity (R3-M2).
                "max_tokens": binding["reserve_tokens"] if enforce_binding else policy.max_tokens,
                # Enforce: the primitive's strict bound-target path (backend AND the Consul-approved
                # endpoint, validated together at transport; no fallback, no escalation) — R2-B2, R4-M1.
                **({"bound_endpoint": binding["route"]} if enforce_binding else {}),
                "timeout_s": max(1, int(deadline - time.monotonic())),
            }
            if deliberate:
                # A cancel closes the stream, so the seat stops generating and the lease is released.
                call_arguments.update(prompt="", messages=messages, stream=True,
                                      should_stop=lambda: self._is_cancelled(job_id))
            elif operation.name == "work.produce":
                # Final/repair/revision calls must be interruptible too: otherwise a thermal
                # guard only discards their eventual result while the card keeps generating.
                # The primitive supports both plain prompts and response_schema on this path.
                if provider.api != "openai":
                    raise ExecutionServiceError("work.produce requires an OpenAI-compatible streaming backend for cancellation")
                call_arguments.update(stream=True, thinking=False,
                                      should_stop=lambda: self._is_cancelled(job_id))
            elif provider.api == "openai" and self._generate is None:
                call_arguments.update(stream=True, should_stop=lambda: self._is_cancelled(job_id))
            # task_family reaches the provider as evidence, not as a second
            # route: THIS service already consulted the family above and pinned
            # the rung it chose (backend=provider.name), which the primitive
            # correctly reads as a caller pin. Forwarding it anyway keeps the
            # stamp on the provider's own result and on the observation record,
            # rather than silently dropping the reason the rung was picked.
            for optional in ("system", "task", "files", "quality", "task_family", "image_path",
                             "response_schema", "temperature") + (
                                 ("thinking", "seed", "top_p", "task_id") if deliberate else ()):
                if arguments.get(optional) is not None:
                    call_arguments[optional] = arguments[optional]
            call_started = time.monotonic()
            with trace_context(job_traceparent):
                result = self._generate_call(**call_arguments)
            observed = self._result_observed(
                result, routed_by=family_routed_by, deliberate=deliberate,
                requested=policy.max_tokens)
            if operation.name == "work.produce":
                observed.update({key: result[key] for key in
                                 ("error_code", "thinking", "finish_reason", "stream_chunks") if key in result})
            # Before the cancellation check: a cancelled turn still spent the seat, so its record is kept.
            deliberate_artifacts = (
                self._record_deliberate_artifacts(state, invocation_id, job_id, result)
                if deliberate else {})
            if deliberate and arguments.get("procedure") == "think_then_emit" and not self._is_cancelled(job_id):
                result, observed, deliberate_artifacts = self._maybe_emit(
                    state, invocation_id, job_id, provider, route, messages, call_arguments,
                    result, observed, deliberate_artifacts, deadline)
            if self._is_cancelled(job_id):
                self._append(
                    "invocation.cancelled",
                    state,
                    invocation_id=invocation_id,
                    observed=observed if deliberate or operation.name == "work.produce" else None,
                    reason="result discarded after cancellation",
                )
                self._append("job.cancelled", state, reason="cancelled during execution")
                return
            if result.get("ok") is not True:
                reason = str(result.get("error") or "provider returned an unsuccessful result")
                # Audit 2026-10-16 (C3): a failed answer stays inspectable. Whatever text the provider returned
                # (truncated or invalid JSON included) is kept as an artifact with the failure metadata beside it.
                failed_text = result.get("text")
                if isinstance(failed_text, str) and failed_text.strip():
                    failed_artifact = self.artifacts.put(
                        failed_text,
                        media_type="text/plain; charset=utf-8",
                        filename=f"{job_id}-failed-output.txt",
                    )
                    self._append(
                        "artifact.recorded",
                        state,
                        invocation_id=invocation_id,
                        artifacts=[{**failed_artifact, "role": "failed_output",
                                    "failure": {"error_code": result.get("error_code"),
                                                "finish_reason": result.get("finish_reason"),
                                                "reason": reason}}],
                    )
                    observed = {**observed, "failed_output_artifact_id": failed_artifact.get("artifact_id")}
                self._append(
                    "invocation.failed",
                    state,
                    invocation_id=invocation_id,
                    observed=observed,
                    reason=reason,
                )
                self._append("job.failed", state, reason=reason)
                return
            text = result.get("text")
            if not isinstance(text, str) or not text.strip():
                reason = "provider returned no visible output"
                self._append(
                    "invocation.failed",
                    state,
                    invocation_id=invocation_id,
                    observed={**observed, "error_code": "no_visible_output"} if deliberate else observed,
                    reason=reason,
                )
                self._append("job.failed", state, reason=reason)
                return
            output_artifact = deliberate_artifacts.get("output") or self.artifacts.put(
                text,
                media_type=operation.artifact_media_type,
                filename=f"{job_id}-result.md",
            )
            self._append(
                "invocation.succeeded",
                state,
                invocation_id=invocation_id,
                observed=observed,
            )
            if not deliberate:   # a deliberate turn recorded its output artifact (role "output") already
                self._append(
                    "artifact.recorded",
                    state,
                    invocation_id=invocation_id,
                    artifacts=[{**output_artifact, "role": "result"}],
                )
            summary = " ".join(text.split())[:280]
            self._append(
                "job.succeeded",
                state,
                observed={
                    "summary": summary,
                    "result_artifact_id": output_artifact["artifact_id"],
                    **trace_obs,
                },
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            latest = self.ledger.get_job(job_id)
            if latest is not None and latest["status"] not in FINAL_JOB_STATUSES:
                if any(
                    item["invocation_id"] == invocation_id
                    for item in latest["invocations"]
                ):
                    raised = (
                        self._result_observed(
                            {"backend": provider.name if provider else None, "model": model, "error_code": "worker_exception",
                             "thinking": arguments.get("thinking"),
                             "duration_ms": round((time.monotonic() - call_started) * 1000)
                             if call_started is not None else None},
                            deliberate=True, requested=policy.max_tokens)
                        if deliberate else None)
                    self._append(
                        "invocation.failed",
                        state,
                        invocation_id=invocation_id,
                        observed=raised,
                        reason=reason,
                    )
                self._append("job.failed", state, reason=reason)
        finally:
            if lease_id is not None:
                self.leases.release(lease_id)

    EMIT_INSTRUCTION = (
        "Your previous turn ended before you wrote the answer; the working notes above are yours. "
        "Emit the final answer now, exactly in the output form the task asks for. Use the decisions "
        "already made in the notes. Do not re-analyze and do not explain.")
    EMIT_DEFAULT_TOKENS = 4096   # a caller-pinned think_then_emit on a family that authors no emit budget

    def _maybe_emit(
        self, state: Mapping[str, Any], invocation_id: str, job_id: str, provider: Backend, route: FamilyRoute,
        messages: list[dict[str, Any]], call_arguments: dict[str, Any], result: dict[str, Any],
        observed: dict[str, Any], artifacts: dict[str, dict[str, Any]], deadline: float,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
        """think_then_emit: a thinking turn that ended without a visible answer (stopped inside its
        reasoning, or cut at max_tokens) gets ONE thinking-off turn that carries its reasoning as notes
        and asks only for the answer. Measured on 2026-10-14 (routing-families code_fix evidence): the
        emitted code compiled and passed 20/20. The emit is a form repair of the same turn, recorded as
        such: the first turn's artifacts stay, the emit's are added, observed.emit says what happened."""
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return result, observed, artifacts
        trigger = result.get("error_code") or "no_visible_output"
        reasoning = result.get("reasoning") or ""
        if trigger not in ("no_visible_output", "output_truncated") or not reasoning.strip():
            return result, {**observed, "emit": {"used": False, "trigger": trigger,
                                                 "why": "no reasoning to carry" if not reasoning.strip()
                                                 else f"{trigger} is not an emit trigger"}}, artifacts
        regime = (route.recommendation or {}).get("regime") or {}
        emit_tokens = int(regime.get("emit_tokens") or self.EMIT_DEFAULT_TOKENS)
        context = int(provider.settings.get("context_tokens") or 0)
        room = (context - emit_tokens) * 4 - self._messages_bytes(messages) - len(self.EMIT_INSTRUCTION) - 2048
        remaining = deadline - time.monotonic()
        if room <= 0 or remaining <= 5:
            return result, {**observed, "emit": {"used": False, "trigger": trigger,
                                                 "why": "no context room" if room <= 0 else "deadline spent"}}, artifacts
        notes = reasoning[-room:]
        emit_messages = [*messages, {"role": "assistant", "content": "Working notes:\n" + notes},
                         {"role": "user", "content": self.EMIT_INSTRUCTION}]
        started = time.monotonic()
        emit = self._generate_call(**{**call_arguments, "messages": emit_messages, "thinking": False,
                                      "max_tokens": emit_tokens, "timeout_s": max(1, int(remaining))})
        stored = {
            "emit_wire_request": self.artifacts.put(
                json.dumps(emit.get("wire_request"), ensure_ascii=False, indent=1, sort_keys=True),
                media_type="application/json; charset=utf-8", filename=f"{job_id}-emit-wire-request.json"),
            "emit_output": self.artifacts.put(
                emit.get("text") or "", media_type="text/plain; charset=utf-8", filename=f"{job_id}-emit-output.txt"),
        }
        self._append("artifact.recorded", state, invocation_id=invocation_id,
                     artifacts=[{**meta, "role": role} for role, meta in stored.items()])
        emit_observed = self._result_observed(emit, deliberate=True, requested=emit_tokens)
        record = {"used": True, "trigger": trigger, "notes_chars": len(notes), "reasoning_chars": len(reasoning),
                  "duration_ms": round((time.monotonic() - started) * 1000),
                  **{k: emit_observed.get(k) for k in ("finish_reason", "tokens_in", "tokens_out", "error_code")
                     if emit_observed.get(k) is not None},
                  "first_turn": {k: observed.get(k) for k in ("finish_reason", "tokens_out", "tokens_reasoning",
                                                              "duration_ms", "error_code") if observed.get(k) is not None}}
        return emit, {**observed, "emit": record}, {**artifacts, "output": stored["emit_output"]}

    def _record_deliberate_artifacts(
        self, state: Mapping[str, Any], invocation_id: str, job_id: str, result: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Store what a deliberate turn produced, whether or not it succeeded: the exact wire request,
        the visible output (possibly empty) and the reasoning. Only a run that reached the seat has them."""
        wire = result.get("wire_request")
        if wire is None and result.get("ok") is not True and not result.get("text") and not result.get("reasoning"):
            return {}
        stored = {
            "wire_request": self.artifacts.put(
                json.dumps(wire, ensure_ascii=False, indent=1, sort_keys=True),
                media_type="application/json; charset=utf-8", filename=f"{job_id}-wire-request.json"),
            "output": self.artifacts.put(
                result.get("text") or "", media_type="text/plain; charset=utf-8",
                filename=f"{job_id}-output.txt"),
            "reasoning": self.artifacts.put(
                result.get("reasoning") or "", media_type="text/plain; charset=utf-8",
                filename=f"{job_id}-reasoning.txt"),
        }
        self._append(
            "artifact.recorded", state, invocation_id=invocation_id,
            artifacts=[{**meta, "role": role} for role, meta in stored.items()],
        )
        return stored

    @staticmethod
    def _result_observed(
        result: Mapping[str, Any], *, routed_by: Optional[str] = None,
        deliberate: bool = False, requested: Optional[int] = None,
    ) -> dict[str, Any]:
        # P8: `occupancy` was dropped at the execution-ledger cutover (the kernel
        # row kept it; the invocation record did not) and `rung_state` /
        # `pool_config_hash` are the dispatch stamps inference.py adds. All three
        # ride `observed`, whose contract is an open object.
        #
        # `routed_by` overrides the provider's own reason when the SERVICE routed
        # this job by task family (see _run_job): the primitive was pinned by us,
        # so its `pinned:<provider>` is an echo, not a reason. None leaves the
        # provider's string exactly as it was.
        allowed = {
            "backend",
            "model",
            "routed_by",
            "occupancy",
            "rung_state",
            "pool_config_hash",
            "tokens_in",
            "tokens_out",
            "duration_ms",
            "max_tokens",
            "timeout_s",
            "sizer",   # ADR-0050: present only on a sized call
            "response_schema_sha256",
            "structured_output_repairs",
            "image_input",
            "temperature",
        }
        if deliberate:
            allowed |= {"finish_reason", "thinking", "tokens_reasoning", "first_reasoning_ms",
                        "first_content_ms", "stream_chunks", "error_code"}
        observed = {
            key: copy.deepcopy(value) for key, value in result.items() if key in allowed
        }
        if deliberate:
            # Every observed key is present, null when the seat or the stream did not supply it.
            for key in ("finish_reason", "thinking", "tokens_in", "tokens_out", "tokens_reasoning",
                        "first_reasoning_ms", "first_content_ms", "stream_chunks", "duration_ms",
                        "backend", "model", "error_code"):
                observed.setdefault(key, None)
            observed["max_tokens_requested"] = requested
            observed["max_tokens_applied"] = result.get("max_tokens", requested)
        if routed_by is not None:
            observed["routed_by"] = routed_by
        return observed

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        state = self.ledger.get_job(job_id)
        if state is None:
            return None
        desired = state.get("desired") or {}
        tp = state.get("traceparent") or desired.get("traceparent") or (desired.get("arguments") or {}).get("traceparent")
        if tp:
            state["traceparent"] = tp
            state["trace_id"] = state.get("trace_id") or desired.get("trace_id") or extract_trace_id(tp)
        return state

    def cancel(self, job_id: str, *, reason: str = "cancelled by caller") -> dict[str, Any]:
        state = self.ledger.get_job(job_id)
        if state is None:
            raise ExecutionServiceError(f"unknown job: {job_id}")
        if state["status"] in FINAL_JOB_STATUSES:
            return state
        with self._lock:
            if job_id not in self._cancel_requested:
                self._cancel_requested.add(job_id)
                self._append("job.cancellation_requested", state, reason=reason)
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                self._append("job.cancelled", state, reason=reason)
        latest = self.ledger.get_job(job_id)
        assert latest is not None
        return latest

    def events(
        self, *, after_sequence: int = 0, job_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return list(
            self.ledger.iter_events(
                after_sequence=after_sequence, job_id=job_id, limit=limit
            )
        )

    def watch(
        self,
        *,
        after_sequence: int = 0,
        job_id: Optional[str] = None,
        wait_seconds: float = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        events = self.events(after_sequence=after_sequence, job_id=job_id, limit=limit)
        if events or wait_seconds <= 0:
            return events
        deadline = time.monotonic() + min(wait_seconds, 30)
        with self._changed:
            while not events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
                events = self.events(
                    after_sequence=after_sequence, job_id=job_id, limit=limit
                )
        return events

    def read_artifact(self, artifact_id: str) -> tuple[dict[str, Any], bytes]:
        metadata = self.ledger.get_artifact(artifact_id)
        if metadata is None:
            raise ExecutionServiceError(f"unknown artifact: {artifact_id}")
        return metadata, self.artifacts.read(metadata)

    def execute_sync(
        self,
        *,
        operation_name: str,
        arguments: Mapping[str, Any],
        principal: Mapping[str, Any],
        source: Mapping[str, Any],
        policy: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Synchronous compatibility projection over the same durable pipeline."""
        state = self.submit(
            operation_name=operation_name,
            arguments=arguments,
            principal=principal,
            source=source,
            policy=policy,
            idempotency_key=idempotency_key,
        )
        job_id = state["job_id"]
        deadline_s = int((state["desired"].get("policy") or {}).get("deadline_s", 1200))
        deadline = time.monotonic() + deadline_s + 5
        while state["status"] not in FINAL_JOB_STATUSES and time.monotonic() < deadline:
            events = self.watch(
                job_id=job_id,
                after_sequence=int(state["last_sequence"]),
                wait_seconds=min(1, max(0, deadline - time.monotonic())),
            )
            state = self.get_job(job_id) or state
            if not events and time.monotonic() >= deadline:
                break
        execution = {
            "request_id": state["request_id"],
            "job_id": job_id,
            "status": state["status"],
        }
        if state["status"] != "succeeded":
            return {
                "ok": False,
                "error": state.get("reason") or "execution did not complete",
                "execution": execution,
            }
        artifact_id = state.get("result_artifact_id")
        if not artifact_id:
            artifact_id = next(
                (
                    item["artifact_id"]
                    for item in state["artifacts"]
                    if item.get("role") == "result"
                ),
                None,
            )
        if not artifact_id:
            return {
                "ok": False,
                "error": "execution completed without a result artifact",
                "execution": execution,
            }
        metadata, content = self.read_artifact(artifact_id)
        observed = dict(state["invocations"][-1]) if state["invocations"] else {}
        result = {
            "ok": True,
            "text": content.decode("utf-8"),
            "execution": {**execution, "artifact": metadata},
        }
        for key in (
            "backend",
            "model",
            "routed_by",
            "occupancy",
            "rung_state",
            "pool_config_hash",
            "tokens_in",
            "tokens_out",
            "duration_ms",
            "max_tokens",
            "timeout_s",
            # C-05-R1: the family stamps come from the Invocation, where
            # _run_job wrote the SERVICE's own decision -- so the door caller
            # (and the gateway ledger row built from this result) sees which
            # family routed the call, not just that one was named. Both keys are
            # absent unless a family was asked for, so a call that never passes
            # task_family gets a byte-identical result.
            "task_family",
            "family_recommendation",
            "temperature",  # present only when the request body carried one
            "finish_reason", "thinking", "tokens_reasoning", "max_tokens_requested",
            "max_tokens_applied", "first_reasoning_ms", "first_content_ms", "stream_chunks",
            "error_code",  # deliberate operation only (the keys are absent from other invocations)
        ):
            if key in observed:
                result[key] = observed[key]
        return result
