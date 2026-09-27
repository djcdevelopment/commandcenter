"""Tests for append-only execution history (G2-C6, G2-C10)."""

import json
import unittest
from pathlib import Path

from hearth.operator import canonical, history, paths
from hearth.tests.operator.support import OperatorTestCase


class OperatorHistoryTests(OperatorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-history-test"

    def test_22_event_closed_taxonomy(self) -> None:
        """History enforces the closed event taxonomy — 22 types since WI-G2a
        added `approval.revoked` (D-112 item 5: revocation is append-only)."""
        self.assertEqual(len(history.EVENT_TYPES), 22)
        self.assertEqual(len(set(history.EVENT_TYPES)), 22)
        for event_type in ("task.received", "route.proposed", "route.validated",
                           "approval.requested", "human.decided", "approval.revoked",
                           "decision.invalidated"):
            self.assertIn(event_type, history.EVENT_TYPES)

        # The contract's enum and the writer's taxonomy are the same closed set.
        schema = canonical.contract_schema(history.CONTRACT_VERSION)
        self.assertEqual(sorted(schema["properties"]["event_type"]["enum"]),
                         sorted(history.EVENT_TYPES))

        # Disallowed event raises HistoryError
        with self.assertRaises(history.HistoryError):
            history.append("unregistered.event.type", {}, run_id=self.run_id)

    def test_monotonic_sequence_and_event_id_verification(self) -> None:
        """Events receive strictly monotonic sequence numbers and verifiable event_id."""
        ev1 = history.append("task.received", {"envelope_id": "a" * 64}, run_id=self.run_id)
        ev2 = history.append("route.proposed", {"proposal_id": "b" * 64}, run_id=self.run_id)
        ev3 = history.append("route.validated", {"verdict": "validated"}, run_id=self.run_id)

        self.assertEqual(ev1["sequence"], 1)
        self.assertEqual(ev2["sequence"], 2)
        self.assertEqual(ev3["sequence"], 3)

        # Verify event_id is SHA-256 over canonical json without event_id
        for ev in (ev1, ev2, ev3):
            preimage = {k: v for k, v in ev.items() if k != "event_id"}
            expected_id = canonical.sha256_hex(canonical.canonical_json(preimage))
            self.assertEqual(ev["event_id"], expected_id)

    def test_tolerant_reading_of_truncated_tail(self) -> None:
        """An interrupted final write reads through the last complete event — and
        is REPORTED, and blocks the next append until it has been reconciled."""
        history.append("task.received", {"envelope_id": "a" * 64}, run_id=self.run_id)
        history.append("route.proposed", {"proposal_id": "b" * 64}, run_id=self.run_id)

        hpath = paths.run_history_path(self.run_id)
        with open(hpath, "a", encoding="utf-8") as f:
            f.write('{"contract_version": "operator-history-event.v1", "sequence": 3, "incomp')

        events = history.read_run_history(self.run_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["sequence"], 1)
        self.assertEqual(events[1]["sequence"], 2)

        _, status = history.read_with_status(hpath)
        self.assertTrue(status["truncated_tail"])
        with self.assertRaises(history.HistoryError):
            history.append("route.validated", {"verdict": "validated"}, run_id=self.run_id)


if __name__ == "__main__":
    unittest.main()
