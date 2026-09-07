from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution import (
    ArtifactStore,
    CapacityLeaseStore,
    ExecutionLedger,
    ExecutionService,
    ExecutionServiceError,
    load_operations,
    new_execution_event,
    new_invocation_id,
    new_job_id,
    new_request_id,
)
from hearth.toolsurface.backends import load_pool


_POOL_DEFAULT = """
default = "test-provider"
"""

_BASE_RUNGS = """
[[backend]]
name = "test-provider"
endpoint = "http://127.0.0.1:9999"
api = "openai"
models = ["gpt-oss-120b"]
tags = ["default"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
timeout_s = 120
context_bytes = 65536

# A deliberately small rung, declared second so it never wins model/tag routing.
# It exists so a payload can be inside llm.chat's max_prompt_bytes (65536) while
# still being over a PINNED provider's context budget — the band where the
# operation gate admits work the provider cannot hold.
[[backend]]
name = "small-provider"
endpoint = "http://127.0.0.1:9998"
api = "openai"
models = ["gpt-oss-120b"]
[backend.settings]
parallel_slots = 1
context_bytes = 4096
"""

_BACKENDS = _POOL_DEFAULT + _BASE_RUNGS


class _ServiceFixture(unittest.TestCase):
    """Pool/ledger/artifact fixture shared by the execution-service test classes.

    Split out from ``ExecutionServiceTest`` so a new test class can reuse the
    fakes without silently re-running every inherited lifecycle test against a
    different pool. ``ExecutionServiceTest`` still carries the general suite, and
    ``PlanTaskFamilyTest`` still re-runs it against the family pool on purpose.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backends_path = self.root / "backends.toml"
        self.backends_path.write_text(_BACKENDS, encoding="utf-8")
        self.environment = patch.dict(
            os.environ, {"HEARTH_BACKENDS": str(self.backends_path)}
        )
        self.environment.start()
        self.services: list[ExecutionService] = []
        self.principal = {
            "type": "irc_account",
            "id": "derek",
            "authenticated": True,
        }
        self.source = {"transport": "irc", "adapter": "BotHerder"}

    def tearDown(self) -> None:
        for service in self.services:
            service.close()
        self.environment.stop()
        self.temporary.cleanup()

    def service(self, generate, *, workers: int = 2) -> ExecutionService:
        service = ExecutionService(
            ledger=ExecutionLedger(self.root / "ledger"),
            artifacts=ArtifactStore(self.root / "artifacts"),
            leases=CapacityLeaseStore(self.root / "coordination.sqlite"),
            operations=load_operations(),
            generate=generate,
            workers=workers,
        )
        self.services.append(service)
        return service

    def wait_final(self, service: ExecutionService, job_id: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = service.get_job(job_id)
            assert state is not None
            if state["status"] in {"succeeded", "failed", "cancelled", "expired"}:
                return state
            time.sleep(0.01)
        self.fail("job did not reach a final state")


class ExecutionServiceTest(_ServiceFixture):
    def test_success_records_lifecycle_invocation_and_full_result_artifact(self) -> None:
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "text": "three concurrency defects found\n\nComplete review.",
                "model": kwargs["model"],
                "backend": kwargs["backend"],
                "tokens_in": 7,
                "tokens_out": 8,
                "duration_ms": 12,
            }

        service = self.service(generate)
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal,
            source=self.source,
            idempotency_key="irc:server:123",
        )
        final = self.wait_final(service, submitted["job_id"])
        self.assertEqual("succeeded", final["status"])
        self.assertEqual("succeeded", final["invocations"][0]["status"])
        self.assertEqual("test-provider", calls[0]["backend"])
        result_metadata = next(
            item for item in final["artifacts"] if item.get("role") == "result"
        )
        _, content = service.read_artifact(result_metadata["artifact_id"])
        self.assertEqual(b"three concurrency defects found\n\nComplete review.", content)
        types = [event["event_type"] for event in service.events(limit=100)]
        self.assertEqual(
            [
                "request.accepted",
                "artifact.recorded",
                "job.queued",
                "job.dispatched",
                "invocation.started",
                "job.running",
                "invocation.succeeded",
                "artifact.recorded",
                "job.succeeded",
            ],
            types,
        )

    def test_idempotency_returns_existing_job_without_second_execution(self) -> None:
        calls = []

        def generate(**_kwargs):
            calls.append(True)
            return {"ok": True, "text": "done"}

        service = self.service(generate)
        arguments = {"prompt": "once"}
        first = service.submit(
            operation_name="llm.chat",
            arguments=arguments,
            principal=self.principal,
            source=self.source,
            idempotency_key="same",
        )
        self.wait_final(service, first["job_id"])
        second = service.submit(
            operation_name="llm.chat",
            arguments=arguments,
            principal=self.principal,
            source=self.source,
            idempotency_key="same",
        )
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(1, len(calls))

    def test_image_job_ledgers_redacted_arguments_and_dispatches_private_artifact(self) -> None:
        class Dispatcher:
            def __init__(self) -> None:
                self.jobs = []

            def enqueue(self, job_id: str) -> None:
                self.jobs.append(job_id)

            def close(self, **_kwargs) -> None:
                pass

        dispatcher = Dispatcher()
        service = ExecutionService(
            ledger=ExecutionLedger(self.root / "image-ledger"),
            artifacts=ArtifactStore(self.root / "image-artifacts"),
            leases=CapacityLeaseStore(self.root / "image-coordination.sqlite"),
            operations=load_operations(),
            generate=lambda **_kwargs: {"ok": True},
            image_dispatcher=dispatcher,
            workers=1,
        )
        self.services.append(service)

        submitted = service.submit(
            operation_name="image.generate",
            arguments={
                "workflow_id": "z-image-turbo",
                "parameters": {
                    "prompt": "private prompt must not enter the ledger",
                    "negative_prompt": "also private", "seed": 7, "steps": 8,
                },
                "strategy": "single", "priority": "high",
            },
            principal=self.principal, source=self.source,
            policy={"priority": 1},
        )

        public = submitted["desired"]["arguments"]
        self.assertNotIn("private prompt", str(public))
        self.assertNotIn("negative_prompt", public["parameters"])
        self.assertIn("prompt_sha256", public["parameters"])
        metadata = submitted["desired"]["input_artifact"]
        _, packed = service.read_artifact(metadata["artifact_id"])
        self.assertIn(b"private prompt must not enter the ledger", packed)
        self.assertEqual([submitted["job_id"]], dispatcher.jobs)

    def test_unauthenticated_principal_is_rejected_without_event(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "no"})
        with self.assertRaises(PermissionError):
            service.submit(
                operation_name="llm.chat",
                arguments={"prompt": "no"},
                principal={
                    "type": "irc_account",
                    "id": "anonymous",
                    "authenticated": False,
                },
                source=self.source,
            )
        self.assertEqual([], service.events())

    def test_unknown_model_fails_closed_before_queueing(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "no"})
        with self.assertRaisesRegex(ExecutionServiceError, "no provider declares"):
            service.submit(
                operation_name="llm.chat",
                arguments={"prompt": "hello", "model": "made-up"},
                principal=self.principal,
                source=self.source,
            )
        self.assertEqual([], service.events())

    def test_plan_resolves_without_dispatch_or_prompt_content(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unused"})
        plan = service.plan(
            operation_name="llm.chat",
            model="gpt-oss-120b",
            prompt_bytes=123,
            policy={"max_tokens": 512, "deadline_s": 120},
        )
        self.assertEqual("test-provider", plan["provider"])
        self.assertEqual("gpt-oss-120b", plan["model"])
        self.assertFalse(plan["dispatch"])
        self.assertEqual(1, plan["global_parallel_slots"])
        self.assertEqual([], service.events())

    def test_queued_job_can_be_cancelled(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()

        def generate(**_kwargs):
            first_started.set()
            release_first.wait(2)
            return {"ok": True, "text": "done"}

        service = self.service(generate, workers=1)
        first = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "first"},
            principal=self.principal,
            source=self.source,
        )
        self.assertTrue(first_started.wait(1))
        second = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "second"},
            principal=self.principal,
            source=self.source,
        )
        cancelled = service.cancel(second["job_id"])
        self.assertEqual("cancelled", cancelled["status"])
        release_first.set()
        self.assertEqual("succeeded", self.wait_final(service, first["job_id"])["status"])

    def test_restart_requeues_an_interrupted_invocation_as_a_new_attempt(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unused"})
        service.close()
        self.services.remove(service)
        request_id = new_request_id()
        job_id = new_job_id()
        invocation_id = new_invocation_id()

        prompt = ArtifactStore(self.root / "artifacts").put("resume me")
        ledger = ExecutionLedger(self.root / "ledger")
        for event in (
            new_execution_event(
                "request.accepted",
                request_id=request_id,
                job_id=job_id,
                operation="llm.chat",
                desired={
                    "operation": "llm.chat",
                    "arguments": {},
                    "input_artifact": prompt,
                    "policy": {"max_tokens": None, "deadline_s": 1200, "priority": 0},
                    "idempotency_key": None,
                },
            ),
            new_execution_event(
                "job.dispatched", request_id=request_id, job_id=job_id
            ),
            new_execution_event(
                "invocation.started",
                request_id=request_id,
                job_id=job_id,
                invocation_id=invocation_id,
            ),
            new_execution_event("job.running", request_id=request_id, job_id=job_id),
        ):
            ledger.append(event)

        recovered = self.service(lambda **_kwargs: {"ok": True, "text": "recovered"})
        final = self.wait_final(recovered, job_id)
        self.assertEqual("succeeded", final["status"])
        self.assertEqual(["failed", "succeeded"], [
            invocation["status"] for invocation in final["invocations"]
        ])

    # -- pinned provider vs. declared context budget -----------------------
    # llm.chat admits up to max_prompt_bytes (65536), which is larger than some
    # providers can hold. Routing by model skips a provider that cannot fit; a
    # pin must be refused for the same reason rather than dispatched to fail.

    def test_pinned_over_budget_submit_is_refused_before_a_job_exists(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unreachable"})

        with self.assertRaises(ExecutionServiceError) as ctx:
            service.submit(
                operation_name="llm.chat",
                arguments={
                    "prompt": "x" * 8000,          # inside llm.chat's 65536 gate
                    "backend": "small-provider",   # but over its 4096 budget
                },
                principal=self.principal,
                source=self.source,
            )

        message = str(ctx.exception)
        self.assertIn("payload_over_budget_for_pinned_backend", message)
        self.assertIn("8000", message)             # the numbers survive the boundary
        self.assertIn("small-provider", message)
        self.assertIn("4096", message)
        # No Job recorded means nothing was ever dispatched, either.
        self.assertEqual([], service.ledger.list_jobs(limit=10))

    def test_pinned_within_budget_submit_still_succeeds(self) -> None:
        service = self.service(lambda **kwargs: {
            "ok": True, "text": "ok", "model": kwargs["model"],
            "backend": kwargs["backend"]})
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "x" * 8000, "backend": "test-provider"},
            principal=self.principal,
            source=self.source,
        )
        final = self.wait_final(service, submitted["job_id"])
        self.assertEqual("succeeded", final["status"])

    def test_endpoint_override_resolving_to_an_over_budget_provider_is_refused(self) -> None:
        # An endpoint= argument is converted into a name pin before routing, so
        # it must be refused on the same grounds.
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unreachable"})
        with self.assertRaises(ExecutionServiceError) as ctx:
            service.submit(
                operation_name="llm.chat",
                arguments={"prompt": "x" * 8000, "endpoint": "http://127.0.0.1:9998"},
                principal=self.principal,
                source=self.source,
            )
        self.assertIn("payload_over_budget_for_pinned_backend", str(ctx.exception))
        self.assertEqual([], service.ledger.list_jobs(limit=10))

    def test_plan_refuses_an_over_budget_pin_without_dispatching(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unreachable"})
        with self.assertRaises(ExecutionServiceError) as ctx:
            service.plan(
                operation_name="llm.chat",
                model="gpt-oss-120b",
                backend="small-provider",
                prompt_bytes=8000,
            )
        self.assertIn("payload_over_budget_for_pinned_backend", str(ctx.exception))

    def test_plan_resolves_a_pin_that_fits(self) -> None:
        service = self.service(lambda **_kwargs: {"ok": True, "text": "unreachable"})
        planned = service.plan(
            operation_name="llm.chat",
            model="gpt-oss-120b",
            backend="small-provider",
            prompt_bytes=1000,
        )
        self.assertEqual("small-provider", planned["provider"])
        self.assertEqual("pinned:small-provider", planned["routed_by"])


if __name__ == "__main__":
    unittest.main()


# Rungs that serve the models routing-families.toml actually names, so a plan
# can be checked against the shipped family declaration rather than a stand-in.
# Declared BEFORE the base rungs, and tagged like the real omen-arc, because the
# family's non-pin branch routes by TAG: the fixture has to reproduce the shape
# it is measuring (a tagged rung that serves the recommended model, ahead of the
# generic one) or the tag route lands somewhere the real pool never would.
# arc-27b-like stays untagged (pin-only, like omen-arc-27b) and declares a budget
# between llm.chat's depth-rule payload (32768 B) and its gate (65536 B), so both
# "the family pin fits" and "the family pin is over budget" are reachable here.
_FAMILY_RUNGS = """
[[backend]]
name = "arc-like"
endpoint = "http://127.0.0.1:8082"
api = "openai"
models = ["qwen3-30b-a3b"]
tags = ["default", "code", "reasoning", "big-context"]
[backend.settings]
parallel_slots = 1
context_bytes = 229376

[[backend]]
name = "arc-27b-like"
endpoint = "http://127.0.0.1:8084"
api = "openai"
models = ["qwen38-27b"]
tags = []
[backend.settings]
parallel_slots = 1
context_bytes = 40960
"""

_FAMILY_BACKENDS = _POOL_DEFAULT + _FAMILY_RUNGS + _BASE_RUNGS

# Derived from the shipped hearth/etc/routing-families.toml, not invented: the
# door estimates prompt tokens as bytes//4, quote_retrieval's authored floor is
# 4096 tokens and every qwen3-30b-a3b family carries a depth_override at 8192.
# A re-authored floor moves these instead of turning a test into a no-op — the
# assertions below re-derive them from the declaration.
_DEEP_BYTES = 20000          # >= quote_retrieval's floor, <= arc-27b-like's budget
_SHALLOW_BYTES = 4000        # < quote_retrieval's floor
_DEPTH_RULE_BYTES = 8192 * 4  # >= the depth_override floor, <= arc-27b-like's budget
_OVER_BUDGET_BYTES = 50000   # >= the depth_override floor, > arc-27b-like's budget


class PlanTaskFamilyTest(ExecutionServiceTest):
    """C-05: plan_execution consults the authored family evidence, content-free.

    The rule under test is deliberately narrow: a recommendation fills in the
    model ONLY when the caller named none AND a live provider serves it. A plan
    that answered with a model nothing in the pool can run would be worse than no
    advice at all -- it would predict a dispatch that cannot happen.
    """

    def setUp(self) -> None:
        super().setUp()
        self.backends_path.write_text(_FAMILY_BACKENDS, encoding="utf-8")
        self.service_ = self.service(lambda **_kwargs: {"ok": True, "text": "unreachable"})

    def test_family_fills_in_the_model_when_a_live_provider_serves_it(self) -> None:
        planned = self.service_.plan(
            operation_name="llm.chat", task_family="summarization", prompt_bytes=1000)
        self.assertEqual("qwen3-30b-a3b", planned["model"])
        self.assertEqual("arc-like", planned["provider"])
        self.assertEqual("summarization", planned["task_family"])
        self.assertEqual("qwen3-30b-a3b", planned["family_recommendation"]["model_id"])
        self.assertFalse(planned["dispatch"])
        self.assertEqual([], self.service_.events())

    def test_the_authored_depth_rule_reaches_the_plan(self) -> None:
        """ADR-0039's inversion at >= 8192 prompt tokens; //4 is the door's estimate."""
        planned = self.service_.plan(
            operation_name="llm.chat", task_family="summarization", prompt_bytes=8192 * 4)
        self.assertEqual("qwen38-27b", planned["model"])
        self.assertEqual("arc-27b-like", planned["provider"])
        self.assertTrue(planned["family_recommendation"]["depth_rule_applied"])
        self.assertEqual([], self.service_.events())

    def test_an_unserved_recommendation_keeps_the_operation_default(self) -> None:
        # The base pool declares only gpt-oss-120b, so nothing serves the family's
        # model: the plan must fall back, and say so through the recommendation.
        self.backends_path.write_text(_BACKENDS, encoding="utf-8")
        planned = self.service_.plan(
            operation_name="llm.chat", task_family="summarization", prompt_bytes=1000)
        self.assertEqual("gpt-oss-120b", planned["model"])
        self.assertEqual([], planned["family_recommendation"]["providers"])
        self.assertIsNone(planned["family_recommendation"]["backend_hint"])
        self.assertEqual([], self.service_.events())

    def test_caller_model_wins_over_the_family(self) -> None:
        planned = self.service_.plan(
            operation_name="llm.chat", model="gpt-oss-120b",
            task_family="summarization", prompt_bytes=1000)
        self.assertEqual("gpt-oss-120b", planned["model"])
        self.assertEqual("qwen3-30b-a3b", planned["family_recommendation"]["model_id"])

    def test_caller_backend_wins_and_suppresses_the_family_model(self) -> None:
        """A pin outranks the family (precedence invariant). If the family still
        filled in its model here, a pin on a rung that does not serve that model
        would raise instead of planning -- the family overriding the pin by the
        back door. The advice still rides back, untaken."""
        planned = self.service_.plan(
            operation_name="llm.chat", backend="small-provider",
            task_family="summarization", prompt_bytes=1000)
        self.assertEqual("small-provider", planned["provider"])
        self.assertEqual("pinned:small-provider", planned["routed_by"])
        self.assertEqual("gpt-oss-120b", planned["model"])  # the operation default, not the family's
        self.assertEqual("qwen3-30b-a3b", planned["family_recommendation"]["model_id"])

    def test_absent_task_family_changes_nothing(self) -> None:
        planned = self.service_.plan(
            operation_name="llm.chat", model="gpt-oss-120b", prompt_bytes=123,
            policy={"max_tokens": 512, "deadline_s": 120})
        self.assertEqual("test-provider", planned["provider"])
        self.assertEqual("gpt-oss-120b", planned["model"])
        self.assertIsNone(planned["task_family"])
        self.assertIsNone(planned["family_recommendation"])
        self.assertEqual([], self.service_.events())

    def test_unreadable_family_evidence_fails_loudly_without_planning(self) -> None:
        missing = str(self.root / "nowhere" / "routing-families.toml")
        with patch.dict(os.environ, {"HEARTH_ROUTING_FAMILIES": missing}):
            with self.assertRaises(ExecutionServiceError) as ctx:
                self.service_.plan(operation_name="llm.chat",
                                   task_family="summarization", prompt_bytes=1000)
        self.assertIn("task family config error", str(ctx.exception))
        self.assertEqual([], self.service_.events())

    def test_blank_task_family_is_refused(self) -> None:
        with self.assertRaisesRegex(ExecutionServiceError, "task_family"):
            self.service_.plan(operation_name="llm.chat", task_family="   ",
                               prompt_bytes=1000)


class TaskFamilyReachesTheProviderTest(ExecutionServiceTest):
    """C-05: the pipeline forwards task_family to the provider instead of dropping it.

    Both cases here name a `model`, which outranks the family (C-05-R1
    precedence), so the family is advisory evidence only and the provider is
    still chosen for the caller's model. `_run_job` hands the primitive
    `backend=<provider>` either way -- the service, not the primitive, is what
    consults the family on this lane. Family ROUTING through the door is covered
    by DoorLaneFamilyRoutingTest below.
    """

    def test_task_family_is_forwarded_to_the_generate_call(self) -> None:
        seen: list = []

        def generate(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "text": "ok", "model": kwargs["model"],
                    "backend": kwargs["backend"]}

        service = self.service(generate)
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "probe", "model": "gpt-oss-120b",
                       "task_family": "summarization"},
            principal=self.principal,
            source=self.source,
        )
        self.assertEqual("succeeded", self.wait_final(service, submitted["job_id"])["status"])
        self.assertEqual(1, len(seen))
        self.assertEqual("summarization", seen[0]["task_family"])
        # And the pin that makes it advisory-only on this lane:
        self.assertEqual("test-provider", seen[0]["backend"])

    def test_absent_task_family_is_not_invented(self) -> None:
        seen: list = []

        def generate(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "text": "ok", "model": kwargs["model"],
                    "backend": kwargs["backend"]}

        service = self.service(generate)
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "probe", "model": "gpt-oss-120b"},
            principal=self.principal,
            source=self.source,
        )
        self.assertEqual("succeeded", self.wait_final(service, submitted["job_id"])["status"])
        self.assertNotIn("task_family", seen[0])


class DispatchIdentityCrossesTheWorkerBoundaryTest(ExecutionServiceTest):
    """The submitting caller's identity must reach the executor worker.

    ContextVars are NOT inherited by ThreadPoolExecutor workers. `local_generate`
    moved onto this Request -> Job -> Invocation pipeline, so the observation
    emitter -- which reads `current_identity()` inside `inference.local_generate`
    -- began finding nothing and recording nothing. Measured live 2026-09-04: a
    successful pinned door call wrote neither an observation artifact nor an
    exclusion row, while the separate ledger bridge kept counting the same call.
    Every door dispatch since that refactor produced no capability evidence.
    """

    def test_identity_in_force_at_submit_reaches_the_generate_call(self) -> None:
        from hearth.observation.identity import DispatchIdentity, dispatch_identity, current_identity

        seen: list = []

        def generate(**kwargs):
            # Runs on the worker thread -- this is the boundary under test.
            seen.append(current_identity())
            return {"ok": True, "text": "ok", "model": kwargs["model"],
                    "backend": kwargs["backend"]}

        service = self.service(generate)
        identity = DispatchIdentity(caller_id="claude-frontier", runner_class="frontier",
                                    node="omen", task_id="t-1", profile="default")
        with dispatch_identity(identity):
            submitted = service.submit(
                operation_name="llm.chat",
                arguments={"prompt": "probe", "model": "gpt-oss-120b"},
                principal=self.principal,
                source=self.source,
                idempotency_key="identity:1",
            )
        final = self.wait_final(service, submitted["job_id"])
        self.assertEqual("succeeded", final["status"])
        self.assertEqual(1, len(seen))
        self.assertIsNotNone(seen[0], "identity did not cross the executor boundary")
        self.assertEqual("claude-frontier", seen[0].caller_id)
        self.assertEqual("t-1", seen[0].task_id)

    def test_no_identity_at_submit_stays_none_rather_than_borrowing_one(self) -> None:
        from hearth.observation.identity import current_identity

        seen: list = []

        def generate(**kwargs):
            seen.append(current_identity())
            return {"ok": True, "text": "ok", "model": kwargs["model"],
                    "backend": kwargs["backend"]}

        service = self.service(generate)
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "probe", "model": "gpt-oss-120b"},
            principal=self.principal,
            source=self.source,
            idempotency_key="identity:2",
        )
        final = self.wait_final(service, submitted["job_id"])
        self.assertEqual("succeeded", final["status"])
        self.assertIsNone(seen[0])


class DoorLaneFamilyRoutingTest(_ServiceFixture):
    """C-05-R1: the EXECUTION lane consults the authored family evidence.

    C-05 stamped `task_family` all the way through the pipeline but never let it
    steer: `submit` and `_run_job` picked a provider with their own
    `select_backend` call and then handed the primitive `backend=<provider>` --
    a pin, which the primitive's precedence (correctly) reads as a caller signal
    that outranks any family. So `mcp__hearth__local_generate(task_family=...)`,
    the call every agent actually makes, ledgered the family and routed as if it
    had never been passed. These tests pin the repaired contract:

      endpoint pin > backend pin > caller model > explicit quality/task
                   > task_family > operation default

    and the routed_by grammar the primitive already uses --
    ``family:<name>:tag:<t>`` / ``family:<name>:pinned:<rung>`` -- now written by
    the SERVICE, on the job record, so `get_execution` and the private
    dashboard's "Family Routes" count see door traffic. Escalation has no
    meaning on this lane: the service dispatches once and the primitive is
    pinned, so ``family:<name>:escalation:...`` cannot arise here.

    Hermetic: fake `generate`, fixture pool, no network, no door.
    """

    def setUp(self) -> None:
        super().setUp()
        self.backends_path.write_text(_FAMILY_BACKENDS, encoding="utf-8")
        self.calls: list[dict] = []
        self.svc = self.service(self.generate)

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        # Shaped like the REAL primitive's return: `_run_job` hands it
        # `backend=<provider>`, so `local_generate` resolves that as a caller pin
        # and stamps `pinned:<provider>` on its own result. Without this the
        # projection test below would be vacuous -- there would be no competing
        # routed_by for the job's family label to have to win against.
        return {"ok": True, "text": "ok", "model": kwargs["model"],
                "backend": kwargs["backend"],
                "routed_by": f"pinned:{kwargs['backend']}"}

    def run_job(self, **arguments) -> dict:
        """Submit one llm.chat job with the fake provider and return its final state."""
        service = self.svc
        submitted = service.submit(
            operation_name="llm.chat",
            arguments=arguments,
            principal=self.principal,
            source=self.source,
        )
        final = self.wait_final(service, submitted["job_id"])
        self.assertEqual("succeeded", final["status"], final.get("reason"))
        return final

    def assertFloorsStillDerived(self) -> None:
        """The byte sizes above are only meaningful while the declaration says so."""
        from hearth.scheduler.families import load_families

        families = load_families()
        self.assertEqual(4096, families.get("quote_retrieval").min_prompt_tokens)
        self.assertEqual(8192, families.get("summarization").depth_override.min_prompt_tokens)
        self.assertLess(_SHALLOW_BYTES // 4, 4096)
        self.assertGreaterEqual(_DEEP_BYTES // 4, 4096)
        self.assertGreaterEqual(_DEPTH_RULE_BYTES // 4, 8192)
        self.assertGreaterEqual(_OVER_BUDGET_BYTES // 4, 8192)
        budget = load_pool().by_name("arc-27b-like").context_bytes()
        self.assertGreaterEqual(budget, _DEEP_BYTES)
        self.assertGreaterEqual(budget, _DEPTH_RULE_BYTES)
        self.assertGreater(_OVER_BUDGET_BYTES, budget)

    # -- (a) tag route ------------------------------------------------------

    def test_family_tag_route_picks_the_rung_and_labels_the_job(self) -> None:
        final = self.run_job(prompt="think about this",
                             task_family="reasoning_planning")
        self.assertEqual("family:reasoning_planning:tag:reasoning", final["routed_by"])
        self.assertEqual("arc-like", final["provider"])
        self.assertEqual("qwen3-30b-a3b", final["model"])
        # The primitive is still handed the resolved rung as a pin -- the SERVICE
        # is what consulted the family, so the pin is the family's own choice.
        self.assertEqual(1, len(self.calls))
        self.assertEqual("arc-like", self.calls[0]["backend"])
        self.assertEqual("qwen3-30b-a3b", self.calls[0]["model"])
        self.assertEqual("reasoning_planning", self.calls[0]["task_family"])
        # ... and the evidence rides the job record, not just the tool result.
        self.assertEqual("reasoning_planning", final["task_family"])
        self.assertEqual("qwen3-30b-a3b",
                         final["family_recommendation"]["model_id"])
        self.assertEqual("family:reasoning_planning:tag:reasoning",
                         final["invocations"][0]["routed_by"])

    def test_an_unknown_family_routes_deterministically_through_default(self) -> None:
        first = self.run_job(prompt="hello", task_family="banana_peeling")
        second = self.run_job(prompt="hello", task_family="banana_peeling")
        self.assertEqual("family:default:tag:default", first["routed_by"])
        self.assertEqual(first["routed_by"], second["routed_by"])
        self.assertEqual("banana_peeling",
                         first["family_recommendation"]["requested_family"])
        self.assertEqual("default", first["family_recommendation"]["family"])

    # -- (b) pin-required route --------------------------------------------

    def test_deep_quote_retrieval_pins_the_untagged_depth_specialist(self) -> None:
        self.assertFloorsStillDerived()
        final = self.run_job(prompt="x" * _DEEP_BYTES, task_family="quote_retrieval")
        self.assertEqual("family:quote_retrieval:pinned:arc-27b-like",
                         final["routed_by"])
        self.assertEqual("arc-27b-like", final["provider"])
        self.assertEqual("qwen38-27b", final["model"])
        self.assertEqual("arc-27b-like", self.calls[0]["backend"])
        self.assertEqual("qwen38-27b", self.calls[0]["model"])
        self.assertTrue(final["family_recommendation"]["pin_required"])

    def test_quote_retrieval_below_the_floor_takes_the_tag_route(self) -> None:
        self.assertFloorsStillDerived()
        final = self.run_job(prompt="x" * _SHALLOW_BYTES,
                             task_family="quote_retrieval")
        self.assertEqual("family:quote_retrieval:tag:big-context", final["routed_by"])
        self.assertEqual("arc-like", final["provider"])
        self.assertEqual("qwen3-30b-a3b", final["model"])
        self.assertFalse(final["family_recommendation"]["pin_required"])

    # -- (c)(d)(e) caller signals outrank the family ------------------------

    def test_caller_backend_wins_and_the_family_is_only_stamped(self) -> None:
        final = self.run_job(prompt="x" * _DEEP_BYTES, backend="test-provider",
                             task_family="quote_retrieval")
        self.assertEqual("pinned:test-provider", final["routed_by"])
        self.assertNotIn("family:", final["routed_by"])
        self.assertEqual("test-provider", final["provider"])
        self.assertEqual("gpt-oss-120b", final["model"])
        # The advice that was not taken is still on the record.
        self.assertEqual("qwen38-27b", final["family_recommendation"]["model_id"])
        self.assertEqual("arc-27b-like",
                         final["family_recommendation"]["backend_hint"])

    def test_caller_model_wins_and_the_family_is_only_stamped(self) -> None:
        final = self.run_job(prompt="x" * _DEEP_BYTES, model="gpt-oss-120b",
                             task_family="quote_retrieval")
        self.assertEqual("model:gpt-oss-120b", final["routed_by"])
        self.assertNotIn("family:", final["routed_by"])
        self.assertEqual("gpt-oss-120b", final["model"])
        self.assertEqual("qwen38-27b", final["family_recommendation"]["model_id"])

    def test_explicit_quality_or_task_suppresses_family_routing(self) -> None:
        """Finding, not a fix: `quality`/`task` do not ROUTE on this lane today —
        the service never passes either to select_backend, it only forwards them
        to the primitive, which is already pinned. They are still caller signals,
        so they outrank the family and reduce it to a stamp."""
        for signal in ({"quality": "good"}, {"task": "code"}):
            with self.subTest(signal=signal):
                self.calls.clear()
                final = self.run_job(prompt="x" * _DEEP_BYTES,
                                     task_family="quote_retrieval", **signal)
                self.assertEqual("model:gpt-oss-120b", final["routed_by"])
                self.assertNotIn("family:", final["routed_by"])
                self.assertEqual("test-provider", final["provider"])
                self.assertEqual("qwen38-27b",
                                 final["family_recommendation"]["model_id"])

    # -- (f) loud refusal ---------------------------------------------------

    def test_over_budget_family_pin_is_refused_by_name_without_creating_a_job(self) -> None:
        """ADR-0031 on this lane: a family pin picks the rung, not the physics."""
        self.assertFloorsStillDerived()
        service = self.svc
        with self.assertRaises(ExecutionServiceError) as ctx:
            service.submit(
                operation_name="llm.chat",
                arguments={"prompt": "x" * _OVER_BUDGET_BYTES,
                           "task_family": "summarization"},
                principal=self.principal,
                source=self.source,
            )
        message = str(ctx.exception)
        self.assertIn("summarization", message)          # which family sent it
        self.assertIn("arc-27b-like", message)           # to which rung
        self.assertIn("payload_over_budget_for_pinned_backend", message)
        self.assertIn(str(_OVER_BUDGET_BYTES), message)  # the numbers survive
        self.assertEqual([], service.ledger.list_jobs(limit=10))
        self.assertEqual([], self.calls)                 # and nothing dispatched

    # -- (g) the absent-family oracle ---------------------------------------

    def test_absent_task_family_routes_exactly_as_before(self) -> None:
        """The regression guard: no task_family, no change — and no stamps."""
        cases = [
            ({}, "model:gpt-oss-120b", "test-provider", "gpt-oss-120b"),
            ({"model": "gpt-oss-120b"}, "model:gpt-oss-120b", "test-provider",
             "gpt-oss-120b"),
            ({"backend": "arc-like", "model": "qwen3-30b-a3b"}, "pinned:arc-like",
             "arc-like", "qwen3-30b-a3b"),
        ]
        for arguments, routed_by, provider, model in cases:
            with self.subTest(arguments=arguments):
                self.calls.clear()
                final = self.run_job(prompt="plain", **arguments)
                self.assertEqual(routed_by, final["routed_by"])
                self.assertEqual(provider, final["provider"])
                self.assertEqual(model, final["model"])
                self.assertNotIn("task_family", final)
                self.assertNotIn("family_recommendation", final)
                self.assertNotIn("task_family", self.calls[0])

    # -- (h) plan and submit cannot disagree --------------------------------

    def test_plan_agrees_with_the_submitted_job_for_every_routing_case(self) -> None:
        """One helper decides for `plan`, `submit` and `_run_job`, so a plan that
        promised a rung and a job that used another one is now a broken test
        rather than a silent divergence. `quality`/`task` are not comparable:
        `plan`'s signature cannot express them."""
        self.assertFloorsStillDerived()
        cases = [
            ("family tag", {"prompt": "think", "task_family": "reasoning_planning"}),
            ("family pin", {"prompt": "x" * _DEEP_BYTES,
                            "task_family": "quote_retrieval"}),
            ("caller backend", {"prompt": "x" * _DEEP_BYTES, "backend": "test-provider",
                                "task_family": "quote_retrieval"}),
            ("caller model", {"prompt": "x" * _DEEP_BYTES, "model": "gpt-oss-120b",
                              "task_family": "quote_retrieval"}),
            ("no family", {"prompt": "x" * _DEEP_BYTES}),
        ]
        for label, arguments in cases:
            with self.subTest(case=label):
                self.calls.clear()
                final = self.run_job(**arguments)
                planned = self.svc.plan(
                    operation_name="llm.chat",
                    model=arguments.get("model"),
                    backend=arguments.get("backend"),
                    prompt_bytes=len(arguments["prompt"].encode("utf-8")),
                    task_family=arguments.get("task_family"),
                )
                self.assertEqual(final["provider"], planned["provider"], label)
                self.assertEqual(final["model"], planned["model"], label)
                self.assertEqual(final["routed_by"], planned["routed_by"], label)

    # -- (i) what the door caller actually receives -------------------------

    def door_call(self, prompt: str, **kwargs) -> dict:
        """`_execution_local_generate` over this fixture's service (no gateway)."""
        from hearth.observation.identity import DispatchIdentity, dispatch_identity
        from hearth.toolsurface import inference

        with patch("hearth.execution.defaults.get_execution_service",
                   return_value=self.svc):
            with dispatch_identity(DispatchIdentity("claude", "frontier", "omen",
                                                    profile="research")):
                return inference._execution_local_generate(prompt, **kwargs)

    def test_the_door_result_carries_the_family_route_not_the_pin_echo(self) -> None:
        """The compatibility projection used to report the primitive's
        `pinned:<provider>` -- an echo of the service's own pin, which said
        nothing about WHY that rung was chosen. The job's routed_by wins for a
        family-routed call, so the gateway ledger row and the dashboard's
        "Family Routes" bucket see door traffic."""
        result = self.door_call("think about this", task_family="reasoning_planning")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual("family:reasoning_planning:tag:reasoning",
                         result["routed_by"])
        self.assertEqual("arc-like", result["backend"])
        self.assertEqual("qwen3-30b-a3b", result["model"])
        self.assertEqual("reasoning_planning", result["task_family"])
        self.assertEqual("reasoning_planning",
                         result["family_recommendation"]["family"])

    def test_the_door_result_shows_a_family_pin(self) -> None:
        self.assertFloorsStillDerived()
        result = self.door_call("x" * _DEEP_BYTES, task_family="quote_retrieval")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual("family:quote_retrieval:pinned:arc-27b-like",
                         result["routed_by"])
        self.assertEqual("arc-27b-like", result["backend"])
        self.assertEqual("qwen38-27b", result["model"])

    def test_the_door_result_without_a_family_is_unchanged(self) -> None:
        # inference.generate declares no default_model, so with no caller signal
        # the service falls to the pool default and the primitive's own
        # `pinned:<provider>` echo still wins the projection — byte-identical to
        # the pre-repair door result.
        result = self.door_call("plain")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual("pinned:test-provider", result["routed_by"])
        self.assertNotIn("task_family", result)
        self.assertNotIn("family_recommendation", result)
