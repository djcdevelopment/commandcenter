"""P8 — the execution ledger's `observed` carries occupancy + the dispatch stamps.

`occupancy` was silently dropped at the execution-ledger cutover (the kernel row
kept it; the invocation record did not). `rung_state` and `pool_config_hash` are
the new stamps from inference.py. All three must survive the invocation record,
the `execute_sync` projection, and the frozen execution-event contract.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution import (
    ArtifactStore,
    CapacityLeaseStore,
    ExecutionLedger,
    ExecutionService,
    load_operations,
)
from hearth.execution.model import validate_execution_event

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

RUNG_STATE = {
    "rung": "test-provider", "port": 9999, "verdict": "degraded",
    "baseline_tok_s": 106.0, "observed_tok_s": 65.0, "frac_of_baseline": 0.6132,
    "note": "envelope is of THIS baseline epoch, not of capacity (ADR-0044)",
}
STAMPS = {"occupancy": "available", "rung_state": RUNG_STATE, "pool_config_hash": "0123abcdef45"}


class ExecutionServiceStampTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        backends_path = self.root / "backends.toml"
        backends_path.write_text(_BACKENDS, encoding="utf-8")
        self.environment = patch.dict(os.environ, {"HEARTH_BACKENDS": str(backends_path)})
        self.environment.start()
        self.services: list[ExecutionService] = []
        self.principal = {"type": "irc_account", "id": "derek", "authenticated": True}
        self.source = {"transport": "irc", "adapter": "BotHerder"}

    def tearDown(self) -> None:
        for service in self.services:
            service.close()
        self.environment.stop()
        self.temporary.cleanup()

    def service(self, generate) -> ExecutionService:
        service = ExecutionService(
            ledger=ExecutionLedger(self.root / "ledger"),
            artifacts=ArtifactStore(self.root / "artifacts"),
            leases=CapacityLeaseStore(self.root / "coordination.sqlite"),
            operations=load_operations(),
            generate=generate,
            workers=1,
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

    @staticmethod
    def _stamped(**overrides):
        def generate(**kwargs):
            result = {
                "ok": True, "text": "stamped answer", "model": kwargs["model"],
                "backend": kwargs["backend"], "routed_by": "pinned:test-provider",
                "tokens_in": 7, "tokens_out": 8, "duration_ms": 12,
                "max_tokens": 512, "timeout_s": 120, **STAMPS,
            }
            result.update(overrides)
            return result
        return generate

    def test_invocation_record_carries_occupancy_and_stamps(self) -> None:
        service = self.service(self._stamped())
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal, source=self.source,
        )
        final = self.wait_final(service, submitted["job_id"])

        self.assertEqual("succeeded", final["status"])
        invocation = final["invocations"][0]
        self.assertEqual(invocation["occupancy"], "available")
        self.assertEqual(invocation["pool_config_hash"], "0123abcdef45")
        self.assertEqual(invocation["rung_state"]["verdict"], "degraded")
        # The stamps did not displace what was already recorded.
        self.assertEqual(invocation["backend"], "test-provider")
        self.assertEqual(invocation["routed_by"], "pinned:test-provider")
        self.assertEqual(invocation["tokens_out"], 8)

    def test_succeeded_event_observed_carries_stamps_and_validates(self) -> None:
        service = self.service(self._stamped())
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal, source=self.source,
        )
        self.wait_final(service, submitted["job_id"])

        events = service.events(limit=100)
        succeeded = next(e for e in events if e["event_type"] == "invocation.succeeded")
        self.assertEqual(succeeded["observed"]["occupancy"], "available")
        self.assertEqual(succeeded["observed"]["rung_state"], RUNG_STATE)
        self.assertEqual(succeeded["observed"]["pool_config_hash"], "0123abcdef45")
        for event in events:
            validate_execution_event(event)  # the frozen v1 contract is unchanged

    def test_failed_invocation_is_stamped_too(self) -> None:
        service = self.service(self._stamped(ok=False, text=None, error="provider said no"))
        submitted = service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal, source=self.source,
        )
        final = self.wait_final(service, submitted["job_id"])

        self.assertEqual("failed", final["status"])
        failed = next(e for e in service.events(limit=100)
                      if e["event_type"] == "invocation.failed")
        self.assertEqual(failed["observed"]["rung_state"]["verdict"], "degraded")
        self.assertEqual(failed["observed"]["occupancy"], "available")

    def test_execute_sync_projection_carries_the_stamps(self) -> None:
        service = self.service(self._stamped())
        result = service.execute_sync(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal, source=self.source,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "stamped answer")
        self.assertEqual(result["occupancy"], "available")
        self.assertEqual(result["pool_config_hash"], "0123abcdef45")
        self.assertEqual(result["rung_state"]["verdict"], "degraded")
        self.assertEqual(result["routed_by"], "pinned:test-provider")

    def test_unstamped_provider_still_projects_the_admission_occupancy(self) -> None:
        """A provider that stamps nothing (a fake, an older adapter) adds no None noise
        for the P8 stamps -- but `occupancy` is present anyway, because the service
        records its OWN admission-time reading on job.dispatched/invocation.started
        and the projection dropped it until now (the cutover regression)."""
        def bare(**kwargs):
            return {"ok": True, "text": "bare", "model": kwargs["model"],
                    "backend": kwargs["backend"]}

        service = self.service(bare)
        result = service.execute_sync(
            operation_name="llm.chat",
            arguments={"prompt": "review this", "model": "gpt-oss-120b"},
            principal=self.principal, source=self.source,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["occupancy"], "available")
        for key in ("rung_state", "pool_config_hash"):
            self.assertNotIn(key, result)
