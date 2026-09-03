"""Unified HEARTH execution pipeline.

Every ingress adapter submits an Operation here. The scheduler resolves a
Provider, obtains global capacity, records each Invocation, stores result bytes
as artifacts, and projects only concise lifecycle data back to callers.
"""

from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Mapping, Optional

from hearth.toolsurface.backends import Backend, BackendConfigError, load_pool, select_backend

from .artifacts import ArtifactStore
from .coordination import CapacityLeaseStore, CapacityUnavailable
from .ids import new_invocation_id, new_job_id, new_request_id
from .ledger import ExecutionLedger, ExecutionLedgerError
from .model import FINAL_JOB_STATUSES, new_execution_event
from .operations import ExecutionPolicy, Operation, OperationConfigError, OperationRegistry
from .operations import load_operations

GenerateCallable = Callable[..., dict[str, Any]]


class ExecutionServiceError(RuntimeError):
    pass


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
        with self._lock:
            for state in states:
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
        allowed = {
            "prompt",
            "model",
            "backend",
            "endpoint",
            "system",
            "task",
            "files",
            "quality",
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
        for key in ("model", "backend", "endpoint", "system", "task", "quality"):
            value = normalized.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ExecutionServiceError(f"{key} must be a non-empty string")
        files = normalized.get("files")
        if files is not None and (
            not isinstance(files, list)
            or not all(isinstance(item, str) and item for item in files)
        ):
            raise ExecutionServiceError("files must be a list of non-empty path strings")
        return normalized, encoded

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
        if is_delegated:
            # Capacity for a render is a calibrated B70 lane, not a model
            # provider, so the whole backend-selection path is skipped.
            model = None
        else:
            model = arguments_value.get("model") or operation.default_model
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
            try:
                select_backend(
                    pool,
                    backend=backend,
                    model=model,
                    payload_bytes=len(prompt_bytes),
                )
            except BackendConfigError as exc:
                raise ExecutionServiceError(str(exc)) from exc

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
                    if key not in {"prompt", "packed_files", "_spec"}
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
                future = self._executor.submit(self._run, job_id)
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
    ) -> dict[str, Any]:
        """Resolve policy and provider without storing content or dispatching work."""
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
        resolved_model = model or operation.default_model
        try:
            provider, routed_by, occupancy = select_backend(
                load_pool(),
                backend=backend,
                model=resolved_model,
                payload_bytes=prompt_bytes,
            )
        except BackendConfigError as exc:
            raise ExecutionServiceError(str(exc)) from exc
        resolved_model = resolved_model or (
            provider.models[0] if provider.models else None
        )
        return {
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
            "dispatch": False,
        }

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

    def _run(self, job_id: str) -> None:
        state = self.ledger.get_job(job_id)
        if state is None or state["status"] in FINAL_JOB_STATUSES:
            return
        desired = state["desired"]
        operation = self.operations.get(state["operation"])
        arguments = dict(desired["arguments"])
        prompt_metadata = desired["input_artifact"]
        prompt = self.artifacts.read(prompt_metadata).decode("utf-8")
        policy = ExecutionPolicy(**desired["policy"])
        model = arguments.get("model") or operation.default_model
        backend_hint = arguments.get("backend")
        started_waiting = time.monotonic()
        deadline = started_waiting + policy.deadline_s
        invocation_id = new_invocation_id()
        provider: Optional[Backend] = None
        lease_id: Optional[str] = None

        try:
            provider, routed_by, occupancy = select_backend(
                load_pool(),
                backend=backend_hint,
                model=model,
                payload_bytes=len(prompt.encode("utf-8")),
            )
            model = model or (provider.models[0] if provider.models else None)
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
            for optional in ("system", "task", "files", "quality"):
                if arguments.get(optional) is not None:
                    call_arguments[optional] = arguments[optional]
            result = self._generate_call(**call_arguments)
            if self._is_cancelled(job_id):
                self._append(
                    "invocation.cancelled",
                    state,
                    invocation_id=invocation_id,
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
                    observed=self._result_observed(result),
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
                    observed=self._result_observed(result),
                    reason=reason,
                )
                self._append("job.failed", state, reason=reason)
                return
            output_artifact = self.artifacts.put(
                text,
                media_type=operation.artifact_media_type,
                filename=f"{job_id}-result.md",
            )
            self._append(
                "invocation.succeeded",
                state,
                invocation_id=invocation_id,
                observed=self._result_observed(result),
            )
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
                    self._append(
                        "invocation.failed",
                        state,
                        invocation_id=invocation_id,
                        reason=reason,
                    )
                self._append("job.failed", state, reason=reason)
        finally:
            if lease_id is not None:
                self.leases.release(lease_id)

    @staticmethod
    def _result_observed(result: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "backend",
            "model",
            "routed_by",
            "tokens_in",
            "tokens_out",
            "duration_ms",
            "max_tokens",
            "timeout_s",
        }
        return {key: copy.deepcopy(value) for key, value in result.items() if key in allowed}

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
            "tokens_in",
            "tokens_out",
            "duration_ms",
            "max_tokens",
            "timeout_s",
        ):
            if key in observed:
                result[key] = observed[key]
        return result
