"""Tests for deterministic replay and state reconstruction (G2-C10)."""

import json
import unittest

from hearth.operator import (approve, artifacts, canonical, catalog as catalog_mod,
                             history, paths, replay)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (HUMAN_APPROVER_KEY, OperatorTestCase,
                                           ORCHESTRATOR_KEY)


class ReplayDeterminismTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-replay-test"
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")

    def decided_approval(self) -> dict:
        """A genuine approval: requested, then decided by a human-class principal
        holding `approve`. Replay honours this one because it re-verifies it."""
        record = approve.request_approval(
            run_id=self.run_id, proposal_id="a" * 64, validation_id="b" * 64,
            node_ids=["n1"], targets=["direct_hearth"], authorities=["merge_push_deploy"],
            catalog_version=str(self.catalog["catalog_version"]), snapshot_id="c" * 64,
            **self.approval_requester_fields(ORCHESTRATOR_KEY),
            now=self.now)
        self.set_key(HUMAN_APPROVER_KEY)
        return approve.decide_approval(run_id=self.run_id,
                                       approval_id=record["approval_id"],
                                       decision="approve", approver=resolve_from_env(),
                                       now=self.now)

    def test_g2_c10_replay_twice_byte_identical(self) -> None:
        """G2-C10: replaying twice produces byte-identical RUN-STATE.json."""
        history.append("task.received", {"envelope_id": "e" * 64}, run_id=self.run_id,
                       envelope_id="e" * 64)
        history.append("route.proposed", {"proposal_id": "a" * 64}, run_id=self.run_id)
        history.append("route.validated", {"validation_id": "b" * 64,
                                           "verdict": "needs_approval"}, run_id=self.run_id)
        self.decided_approval()

        state1 = replay.replay_run(self.run_id)
        state_file = paths.run_state_path(self.run_id)
        self.assertTrue(state_file.is_file())
        bytes1 = state_file.read_bytes()

        state2 = replay.replay_run(self.run_id, check=True)
        bytes2 = state_file.read_bytes()

        self.assertEqual(bytes1, bytes2)
        self.assertEqual(state1, state2)
        self.assertEqual(state1["status"], "approved")
        self.assertEqual(state1["unverified_decisions"], [])
        # task.received, route.proposed, route.validated, approval.requested,
        # human.decided.
        self.assertEqual(state1["events_replayed"], 5)
        self.assertEqual(state1["last_sequence"], 5)

    def test_g2_c10_truncated_history_resilience(self) -> None:
        """G2-C10: a truncated history replays cleanly up to the last complete
        event, and says that it was truncated."""
        history.append("task.received", {"envelope_id": "e" * 64}, run_id=self.run_id,
                       envelope_id="e" * 64)
        history.append("route.proposed", {"proposal_id": "a" * 64}, run_id=self.run_id)

        hpath = paths.run_history_path(self.run_id)
        with open(hpath, "a", encoding="utf-8") as handle:
            handle.write('{"contract_version": "operator-history-event.v1", "truncated')

        state = replay.replay_run(self.run_id)
        self.assertEqual(state["events_replayed"], 2)
        self.assertEqual(state["last_sequence"], 2)
        self.assertEqual(state["status"], "proposed")
        self.assertTrue(state["truncated_tail"])

    def test_artifact_durability_reconstructable_vs_auditable_only(self) -> None:
        """`reconstructable` is true only when every required artifact recomputes
        to its recorded digest AND has a verified second location."""
        history.append("task.received", {"envelope_id": "e" * 64}, run_id=self.run_id)

        artifacts.ingest_artifact(self.run_id, {"test": "data"}, "application/json",
                                  retention_class="required", durability="local_only")
        first = replay.replay_run(self.run_id)
        self.assertFalse(first["reconstructable"])
        self.assertTrue(first["auditable_only"])

        artifacts.ingest_artifact(self.run_id, {"test": "verified_data"},
                                  "application/json", retention_class="required",
                                  durability="verified")
        index_path = paths.run_artifacts_index_path(self.run_id)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for row in index:
            row["durability"] = "verified"
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

        second = replay.replay_run(self.run_id)
        self.assertTrue(second["reconstructable"])
        self.assertFalse(second["auditable_only"])

        # A blob that no longer matches its recorded digest withdraws the claim.
        blob = paths.raw_artifacts_dir() / index[0]["sha256"]
        blob.write_bytes(b"tampered")
        third = replay.replay_run(self.run_id)
        self.assertFalse(third["reconstructable"])


if __name__ == "__main__":
    unittest.main()
