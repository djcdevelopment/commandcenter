"""Gateway/agent handoff.

The contract under test is an ownership split forced by Windows session
isolation:

    GATEWAY  owns authority, admission, job lifecycle, and the ledger.
    AGENT    owns GPU process execution, and nothing else.

The agent must never write the ledger. That is not a style preference -- a
direct two-process test of ExecutionLedger produced a duplicate sequence number,
sqlite OperationalError, and a wedged second writer. So these tests pin that the
gateway performs every state transition, and that the file protocol between them
is exclusive, recoverable, and honest about a missing executor.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.operations import load_operations
from hearth.execution.service import ExecutionService
from hearth.media import handoff
from hearth.media.execution import RenderSubsystem

SESSION = "20260825T023859Z-c83a0e3b"
CLIP = SESSION + "-27275f15365a"


class HandoffBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "render"
        patcher = patch.dict(os.environ, {"HEARTH_RENDER_STATE": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        handoff.ensure_dirs()


class ProtocolTest(HandoffBase):
    def test_enqueue_publishes_a_durable_job(self) -> None:
        handoff.enqueue_job("job_1", {"clip_id": CLIP}, deadline_s=600)
        queued = handoff.list_queued()
        self.assertEqual(1, len(queued))
        self.assertEqual("job_1", queued[0].stem)

    def test_claim_is_exclusive(self) -> None:
        # The rename IS the lock. Exactly one claimer can win; a loser gets None
        # rather than a corrupt half-claim.
        handoff.enqueue_job("job_1", {"clip_id": CLIP})
        path = handoff.list_queued()[0]
        first = handoff.claim_job(path)
        second = handoff.claim_job(path)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual([], handoff.list_queued())

    def test_claim_removes_the_job_from_the_queue(self) -> None:
        handoff.enqueue_job("job_1", {"clip_id": CLIP})
        handoff.claim_job(handoff.list_queued()[0])
        self.assertEqual([], handoff.list_queued())

    def test_publish_claim_records_who_is_executing(self) -> None:
        handoff.enqueue_job("job_1", {"clip_id": CLIP})
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim("job_1", record, lane_id="b70@bus4", pid=4242)
        claim = handoff.read_claim("job_1")
        self.assertEqual("b70@bus4", claim["lane_id"])
        self.assertEqual(4242, claim["pid"])
        self.assertIn("started_at", claim)
        # The staging file must not survive the publish.
        self.assertEqual([], list((self.root / "claims").glob("*.claiming")))

    def test_requeue_returns_an_unfinished_job_rather_than_failing_it(self) -> None:
        # "The executor went away" is not "the render is impossible".
        handoff.enqueue_job("job_1", {"clip_id": CLIP})
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim("job_1", record, lane_id="b70@bus4", pid=1)
        handoff.requeue("job_1", record)
        self.assertEqual(["job_1"], [p.stem for p in handoff.list_queued()])
        self.assertIsNone(handoff.read_claim("job_1"))

    def test_result_round_trips(self) -> None:
        handoff.publish_result("job_1", {"ok": True}, ok=True)
        result = handoff.read_result("job_1")
        self.assertTrue(result["ok"])
        self.assertEqual({"ok": True}, result["receipt"])


class AgentStatusTest(HandoffBase):
    def test_no_heartbeat_means_no_executor(self) -> None:
        status = handoff.agent_status()
        self.assertFalse(status.available)
        self.assertIn("no render agent heartbeat", status.detail)

    def test_fresh_heartbeat_is_available(self) -> None:
        handoff.beat(capable=True, detail="ok", lanes=["b70@bus4"])
        status = handoff.agent_status()
        self.assertTrue(status.available)
        self.assertTrue(status.capable)
        self.assertEqual(["b70@bus4"], status.lanes)

    def test_stale_heartbeat_is_unavailable(self) -> None:
        handoff.beat(capable=True, detail="ok")
        stale = time.time() + handoff.HEARTBEAT_STALE_S + 30
        status = handoff.agent_status(now=stale)
        self.assertFalse(status.available)
        self.assertIn("stale", status.detail)

    def test_an_agent_that_cannot_render_reports_capable_false(self) -> None:
        # Present but useless is different from absent, and both are different
        # from "healthy". Session 0 produces exactly this.
        handoff.beat(capable=False, detail="no GPU adapters visible")
        status = handoff.agent_status()
        self.assertTrue(status.available)
        self.assertFalse(status.capable)


class GatewayIsTheSoleLedgerWriterTest(HandoffBase):
    """The gateway performs every state transition; the agent writes files."""

    def setUp(self) -> None:
        super().setUp()
        base = Path(self._temp.name)
        self.service = ExecutionService(
            ledger=ExecutionLedger(base / "ledger"),
            artifacts=ArtifactStore(base / "artifacts"),
            leases=CapacityLeaseStore(base / "coordination.sqlite"),
            operations=load_operations(), generate=None, workers=1,
            recover_pending=False,
        )
        self.addCleanup(lambda: self.service.close(wait=False))
        self.subsystem = RenderSubsystem(service=self.service, autostart=False)
        self.service._render_dispatcher = self.subsystem

    def _submit(self, clip=CLIP) -> str:
        root = Path(self._temp.name) / "media"
        for sub in ("raw", "work", "drafts", "approved", "_bench"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        (root / "raw" / SESSION).mkdir(parents=True, exist_ok=True)
        (root / "raw" / SESSION / "seg.mkv").write_bytes(b"x")
        with patch.dict(os.environ, {"HEARTH_MEDIA_ROOT": str(root)}):
            state = self.service.submit(
                operation_name="media.render",
                arguments={
                    "session_id": SESSION, "clip_id": clip, "clip_revision": 1,
                    "source_segments": ["raw/%s/seg.mkv" % SESSION],
                    "start_seconds": 0.0, "end_seconds": 10.0,
                    "variants": ["horizontal"], "profile_version": "bf6-qsv-v1",
                },
                principal={"type": "hearth_caller", "id": "t", "authenticated": True},
                source={"transport": "test", "adapter": "t"},
            )
        return state["job_id"]

    def _events(self, job_id) -> list:
        return [e["event_type"] for e in self.service.events(limit=200)
                if e.get("job_id") == job_id]

    def test_submit_queues_without_an_executor(self) -> None:
        # No agent exists. The job must be QUEUED, not failed.
        job_id = self._submit()
        state = self.service.ledger.get_job(job_id)
        self.assertEqual("queued", state["status"])
        self.assertEqual([job_id], [p.stem for p in handoff.list_queued()])

    def test_claim_then_result_produces_the_canonical_lifecycle(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        self.subsystem.ingest()
        self.assertEqual("running", self.service.ledger.get_job(job_id)["status"])

        handoff.publish_result(job_id, {"ok": True, "variants": []}, ok=True)
        self.subsystem.ingest()
        self.assertEqual("succeeded", self.service.ledger.get_job(job_id)["status"])
        self.assertEqual(
            ["request.accepted", "artifact.recorded", "job.queued", "job.dispatched",
             "invocation.started", "job.running", "artifact.recorded",
             "invocation.succeeded", "job.succeeded"],
            self._events(job_id))

    def test_a_failed_render_carries_its_reason(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        handoff.publish_result(job_id, {"ok": False}, ok=False,
                               reason="validation_failed")
        self.subsystem.ingest()
        state = self.service.ledger.get_job(job_id)
        self.assertEqual("failed", state["status"])
        self.assertEqual("validation_failed", state.get("reason"))

    def test_result_for_a_job_the_gateway_never_saw_start(self) -> None:
        # The control plane can restart mid-render. That must not kill the GPU
        # process, and the lifecycle must not jump queued -> succeeded.
        job_id = self._submit()
        handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_result(job_id, {"ok": True}, ok=True)
        self.subsystem.ingest()
        events = self._events(job_id)
        self.assertEqual("succeeded", self.service.ledger.get_job(job_id)["status"])
        for required in ("job.dispatched", "invocation.started", "job.running"):
            self.assertIn(required, events)

    def test_ingest_is_idempotent(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        handoff.publish_result(job_id, {"ok": True}, ok=True)
        self.subsystem.ingest()
        before = len(self._events(job_id))
        for _ in range(3):
            self.subsystem.ingest()
        self.assertEqual(before, len(self._events(job_id)),
                         "re-ingesting must not append duplicate transitions")
        self.assertEqual([], handoff.list_results())
        self.assertEqual([], handoff.list_claims())

    def test_the_receipt_is_attached_as_a_result_artifact(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        handoff.publish_result(job_id, {"ok": True, "lane": {"lane_id": "b70@bus4"}}, ok=True)
        self.subsystem.ingest()
        state = self.service.ledger.get_job(job_id)
        results = [a for a in state["artifacts"] if a["role"] == "result"]
        self.assertEqual(1, len(results))
        self.assertIn("render-receipt", results[0]["filename"])


if __name__ == "__main__":
    unittest.main()


class CancellationTest(GatewayIsTheSoleLedgerWriterTest):
    """What cancellation actually enforces -- and what it deliberately does not.

    Two stages with genuinely different strength, which is why the tool reports
    which one happened rather than a bare ok. A queued job is stopped
    absolutely. A claimed job is asked to stop, and an ffmpeg already encoding
    finishes (ADR-0030: cancellation is cooperative). Nothing here kills a
    process, and no test below pretends otherwise.
    """

    def test_a_queued_job_is_stopped_absolutely(self) -> None:
        job_id = self._submit()
        outcome = self.subsystem.cancel(job_id, reason="superseded")

        self.assertTrue(outcome["stopped_before_start"])
        self.assertEqual("cancelled", outcome["status"])
        self.assertEqual("cancelled", self.service.ledger.get_job(job_id)["status"])
        # The queue entry is gone, so no agent can ever pick it up.
        self.assertEqual([], handoff.list_queued())

    def test_a_claimed_job_is_asked_not_forced(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        self.subsystem.ingest()

        outcome = self.subsystem.cancel(job_id, reason="superseded")

        self.assertFalse(outcome["stopped_before_start"])
        # NOT terminal: the agent is still holding it, and inventing a terminal
        # event for work that is still running would make the ledger lie.
        self.assertEqual("cancellation_requested",
                         self.service.ledger.get_job(job_id)["status"])
        self.assertTrue(handoff.is_cancelled(job_id),
                        "the agent must be able to see the request")

    def test_the_agent_result_still_decides_a_claimed_job(self) -> None:
        # Cancellation does not pre-empt the outcome: whatever the agent
        # publishes is what the job becomes.
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        self.subsystem.ingest()
        self.subsystem.cancel(job_id, reason="superseded")

        handoff.publish_result(job_id, {"ok": True, "variants": []}, ok=True)
        self.subsystem.ingest()

        self.assertEqual("succeeded", self.service.ledger.get_job(job_id)["status"])
        self.assertFalse(handoff.is_cancelled(job_id), "the marker is cleaned up")

    def test_cancelling_a_terminal_job_is_a_no_op(self) -> None:
        job_id = self._submit()
        record = handoff.claim_job(handoff.list_queued()[0])
        handoff.publish_claim(job_id, record, lane_id="b70@bus4", pid=99)
        handoff.publish_result(job_id, {"ok": True, "variants": []}, ok=True)
        self.subsystem.ingest()

        outcome = self.subsystem.cancel(job_id)
        self.assertTrue(outcome["already_terminal"])
        self.assertEqual("succeeded", self.service.ledger.get_job(job_id)["status"])

    def test_an_unknown_job_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.subsystem.cancel("job_nope")

    def test_cancellation_events_come_only_from_the_gateway(self) -> None:
        # The single-writer invariant holds for this path too.
        job_id = self._submit()
        self.subsystem.cancel(job_id, reason="superseded")
        self.assertEqual(
            ["request.accepted", "artifact.recorded", "job.queued",
             "job.cancellation_requested", "job.cancelled"],
            self._events(job_id))


class CancelRenderToolScopeTest(GatewayIsTheSoleLedgerWriterTest):
    """`cancel_render` is a narrow capability and must stay narrow.

    It exists so the BF6 dispatcher can retire its own superseded work without
    being handed `execution` and its `cancel_execution`. If it could reach any
    job, or another caller's job, the narrow capability would be decoration.
    """

    def _cancel(self, job_id, caller="t"):
        from hearth.observation.identity import DispatchIdentity, dispatch_identity
        from hearth.toolsurface import media_render

        identity = DispatchIdentity(caller_id=caller, runner_class="local", node="omen")
        with patch("hearth.execution.defaults.get_execution_service",
                   return_value=self.service), \
             patch("hearth.toolsurface.media_render.get_execution_service",
                   return_value=self.service), \
             dispatch_identity(identity):
            return media_render.cancel_render(job_id)

    def test_it_cancels_a_media_render_job_it_owns(self) -> None:
        job_id = self._submit()
        outcome = self._cancel(job_id)
        self.assertTrue(outcome["ok"])
        self.assertEqual("cancelled", self.service.ledger.get_job(job_id)["status"])

    def test_it_refuses_another_callers_job(self) -> None:
        job_id = self._submit()          # submitted as caller "t"
        with self.assertRaises(PermissionError):
            self._cancel(job_id, caller="someone-else")
        self.assertEqual("queued", self.service.ledger.get_job(job_id)["status"])

    def test_it_refuses_a_job_that_is_not_media_render(self) -> None:
        state = self.service.submit(
            operation_name="llm.chat",
            arguments={"prompt": "hello"},
            principal={"type": "hearth_caller", "id": "t", "authenticated": True},
            source={"transport": "test", "adapter": "t"},
        )
        with self.assertRaises(PermissionError):
            self._cancel(state["job_id"])
