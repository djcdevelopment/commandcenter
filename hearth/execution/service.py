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
from typing import Any, Callable, Mapping, NamedTuple, Optional

from hearth.observation.identity import (DispatchIdentity, current_identity,
                                         dispatch_identity)
from hearth.toolsurface.backends import Backend, BackendConfigError, load_pool, select_backend

from .artifacts import ArtifactStore
from .coordination import CapacityLeaseStore, CapacityUnavailable
from .ids import new_invocation_id, new_job_id, new_request_id
from .ledger import ExecutionLedger, ExecutionLedgerError
from .model import FINAL_JOB_STATUSES, new_execution_event
from .operations import ExecutionPolicy, Operation, OperationConfigError, OperationRegistry
from .operations import DELIBERATE, load_operations
from .pause import dispatch_paused

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
        startup_held: bool = False,
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
        self._startup_held = startup_held
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
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
        with self._lock:
            for state in states:
                if state.get("operation") in skipped_operations:
                    # Keep legacy work visible and queued; do not replay or erase it.
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
                    "task_family"):
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
                   "response_schema", "model", "task_id"}
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
        backend = normalized.get("backend")
        if not isinstance(backend, str) or not backend.strip():
            raise ExecutionServiceError("backend is required and must be a non-empty string")
        provider = load_pool().by_name(backend)
        if provider is None or not provider.settings.get("deliberate_max_tokens"):
            raise ExecutionServiceError(
                f"backend {backend!r} does not declare deliberate_max_tokens; {operation.name} refuses it"
            )
        if not isinstance(normalized.get("thinking"), bool):
            raise ExecutionServiceError("thinking is required and must be a boolean")
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

    @staticmethod
    def _select_for_route(
        pool: Any, route: FamilyRoute, payload_bytes: int,
        max_tokens: Optional[int] = None,
    ) -> tuple[Backend, str, dict[str, Any]]:
        """``select_backend`` for a resolved route, with the family named on failure.

        ``max_tokens`` is the output reserve admission checks beside the prompt
        (ADR-0031 arithmetic): the job's own policy budget when it names one,
        else the sizer's expected output when consulted, else the rung default
        inside ``select_backend`` -- the same precedence the door applies.
        """
        try:
            return select_backend(
                pool,
                backend=route.backend,
                model=route.model,
                tags=route.tags,
                payload_bytes=payload_bytes,
                max_tokens=max_tokens if max_tokens is not None else route.admission_max_tokens,
            )
        except BackendConfigError as exc:
            raise ExecutionServiceError(route.refusal(exc)) from exc

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
        if self._startup_held or dispatch_paused():
            raise ExecutionServiceError("HEARTH dispatch is paused; restart the gateway after resume")
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
        policy_value = self.operations.policy_for(operation, policy)
        if deliberate:
            if policy_value.max_tokens is None:
                raise ExecutionServiceError(f"{operation.name} requires policy.max_tokens")
            cap = int(load_pool().by_name(arguments_value["backend"]).settings["deliberate_max_tokens"])
            if policy_value.max_tokens > cap:
                raise ExecutionServiceError(
                    f"max_tokens {policy_value.max_tokens} exceeds {arguments_value['backend']} "
                    f"deliberate_max_tokens {cap}"
                )
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
            self._select_for_route(
                pool,
                route,
                admit_bytes,
                max_tokens=policy_value.max_tokens,
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
            desired = {
                "operation": operation_name,
                "arguments": {
                    key: value
                    for key, value in arguments_value.items()
                    if key not in {"prompt", "packed_files", "_spec", "messages"}
                },
                "packed_files": arguments_value.get("packed_files", []),
                "input_artifact": input_artifact,
                "policy": {
                    "max_tokens": policy_value.max_tokens,
                    "deadline_s": policy_value.deadline_s,
                    "priority": policy_value.priority,
                },
                "idempotency_key": idempotency_key,
            }
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
            )
            self._append(
                "artifact.recorded",
                initial,
                artifacts=[{**input_artifact, "role": "input"}],
            )
            self._append("job.queued", initial)
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
                future = self._executor.submit(self._run, job_id, current_identity())
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
        resolved_policy = self.operations.policy_for(operation, policy)
        route = self._family_route(
            operation,
            {"model": model, "backend": backend, "task_family": task_family},
            prompt_bytes,
        )
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
                "dispatch": False,
                "ok": False,
                "error": f"policy_refusal: {refusal}",
                "error_code": "policy_refusal",
                "refusal": refusal,
            }
        try:
            provider, routed_by, occupancy = self._select_for_route(
                load_pool(), route, prompt_bytes, max_tokens=resolved_policy.max_tokens
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

    def _run(self, job_id: str, identity: Optional[DispatchIdentity] = None) -> None:
        """Run one job on an executor worker under the submitting caller's identity.

        ``identity`` is None for jobs recovered at boot -- there is no live caller to
        attribute those to, and ``dispatch_identity(None)`` is a no-op, so they record
        nothing rather than borrowing someone else's name.
        """
        with dispatch_identity(identity):
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
            provider, routed_by, occupancy = self._select_for_route(
                load_pool(), route, payload_bytes, max_tokens=policy.max_tokens
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
            self._append("job.dispatched", state, observed=dispatch_observed)
            self._append(
                "invocation.started",
                state,
                invocation_id=invocation_id,
                observed=dispatch_observed,
            )
            self._append("job.running", state)
            call_arguments = {
                "prompt": prompt,
                "model": model,
                "backend": provider.name,
                "max_tokens": policy.max_tokens,
                "timeout_s": max(1, int(deadline - time.monotonic())),
            }
            if deliberate:
                call_arguments.update(prompt="", messages=messages, stream=True)
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
            result = self._generate_call(**call_arguments)
            observed = self._result_observed(
                result, routed_by=family_routed_by, deliberate=deliberate,
                requested=policy.max_tokens)
            # Before the cancellation check: a cancelled turn still spent the seat, so its record is kept.
            deliberate_artifacts = (
                self._record_deliberate_artifacts(state, invocation_id, job_id, result)
                if deliberate else {})
            if self._is_cancelled(job_id):
                self._append(
                    "invocation.cancelled",
                    state,
                    invocation_id=invocation_id,
                    observed=observed if deliberate else None,
                    reason="result discarded after cancellation",
                )
                self._append("job.cancelled", state, reason="cancelled during execution")
                return
            if result.get("ok") is not True:
                reason = str(result.get("error") or "provider returned an unsuccessful result")
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
        return self.ledger.get_job(job_id)

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
