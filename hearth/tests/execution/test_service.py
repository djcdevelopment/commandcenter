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


_BACKENDS = """
default = "test-provider"

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
"""


class ExecutionServiceTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
