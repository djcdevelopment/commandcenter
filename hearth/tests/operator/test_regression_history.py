"""Regression tests for the history-integrity and replay exploits reproduced by
the WI-G2 independent verification (report sections 7 and 8, appendix B).

The shipped reader detected no corruption: bad JSON mid-file, out-of-order and
duplicate sequences, a wrong `event_id` and a tampered payload under an unchanged
`event_id` all read back as "9 events, no error"; a truncated last line was
silently dropped AND its sequence number silently reused; the writer never
flushed or fsynced; replay was fail-open on unknown event types and versions.

Each test is named for the exploit. All were red against `a2d7a91`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hearth.operator import canonical, history, paths, replay
from hearth.tests.operator.support import OperatorTestCase


class HistoryCorruptionBase(OperatorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-history-regression"
        for index in range(4):
            history.append("attempt.recorded", {"attempt": index + 1}, run_id=self.run_id)
        self.path = paths.run_history_path(self.run_id)

    def lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    def write_lines(self, lines: list[str]) -> None:
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    def corrupt(self, mutate) -> None:
        lines = self.lines()
        mutate(lines)
        self.write_lines(lines)


class MalformedHistoryTests(HistoryCorruptionBase):
    """All five corruption forms must fail loudly on read."""

    def test_bad_json_mid_file_is_refused(self) -> None:
        self.corrupt(lambda lines: lines.__setitem__(1, '{"contract_version": broken'))
        with self.assertRaises(history.HistoryError) as ctx:
            history.read_run_history(self.run_id)
        self.assertIn("2", str(ctx.exception), "the refusal names the line")

    def test_out_of_order_sequence_is_refused(self) -> None:
        def mutate(lines):
            row = json.loads(lines[2])
            row["sequence"] = 9
            row["event_id"] = canonical.sha256_hex(canonical.canonical_json(
                {k: v for k, v in row.items() if k != "event_id"}))
            lines[2] = json.dumps(row, sort_keys=True, separators=(",", ":"))

        self.corrupt(mutate)
        with self.assertRaises(history.HistoryError):
            history.read_run_history(self.run_id)

    def test_duplicate_sequence_is_refused(self) -> None:
        def mutate(lines):
            row = json.loads(lines[3])
            row["sequence"] = 3
            row["event_id"] = canonical.sha256_hex(canonical.canonical_json(
                {k: v for k, v in row.items() if k != "event_id"}))
            lines[3] = json.dumps(row, sort_keys=True, separators=(",", ":"))

        self.corrupt(mutate)
        with self.assertRaises(history.HistoryError):
            history.read_run_history(self.run_id)

    def test_wrong_event_id_is_refused(self) -> None:
        def mutate(lines):
            row = json.loads(lines[1])
            row["event_id"] = "0" * 64
            lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"))

        self.corrupt(mutate)
        with self.assertRaises(history.HistoryError) as ctx:
            history.read_run_history(self.run_id)
        self.assertIn("event_id", str(ctx.exception))

    def test_tampered_payload_under_an_unchanged_event_id_is_refused(self) -> None:
        def mutate(lines):
            row = json.loads(lines[2])
            row["payload"] = {"attempt": 4242}
            lines[2] = json.dumps(row, sort_keys=True, separators=(",", ":"))

        self.corrupt(mutate)
        with self.assertRaises(history.HistoryError):
            history.read_run_history(self.run_id)

    def test_unknown_event_type_and_contract_version_are_refused(self) -> None:
        for field, value in (("event_type", "quantum.teleported"),
                             ("contract_version", "operator-history-event.v9")):
            with self.subTest(field=field):
                self.setUp()
                def mutate(lines, f=field, v=value):
                    row = json.loads(lines[2])
                    row[f] = v
                    row["event_id"] = canonical.sha256_hex(canonical.canonical_json(
                        {k: val for k, val in row.items() if k != "event_id"}))
                    lines[2] = json.dumps(row, sort_keys=True, separators=(",", ":"))

                self.corrupt(mutate)
                with self.assertRaises(history.HistoryError):
                    history.read_run_history(self.run_id)


class TruncatedTailTests(HistoryCorruptionBase):
    """An interrupted final write replays through the last complete event, is
    REPORTED, and blocks further append until reconciled."""

    def truncate(self) -> None:
        with open(self.path, "a", encoding="utf-8", newline="") as handle:
            handle.write('{"contract_version":"operator-history-event.v1","sequence":5,"ev')

    def test_truncated_tail_is_reported_not_silently_dropped(self) -> None:
        self.truncate()
        events, status = history.read_with_status(self.path)
        self.assertEqual(len(events), 4)
        self.assertTrue(status["truncated_tail"])

    def test_truncated_tail_blocks_further_append(self) -> None:
        self.truncate()
        with self.assertRaises(history.HistoryError) as ctx:
            history.append("attempt.recorded", {"attempt": 5}, run_id=self.run_id)
        self.assertIn("reconcile", str(ctx.exception).lower())

    def test_a_dropped_tail_never_reuses_a_sequence_number(self) -> None:
        self.truncate()
        history.reconcile_tail(self.path)
        event = history.append("attempt.recorded", {"attempt": 5}, run_id=self.run_id)
        sequences = [row["sequence"] for row in history.read_run_history(self.run_id)]
        self.assertEqual(sequences, [1, 2, 3, 4, 5])
        self.assertEqual(event["sequence"], 5)

    def test_reconciliation_preserves_the_damaged_bytes_and_accepted_history(self) -> None:
        before = self.path.read_bytes()
        self.truncate()
        report = history.reconcile_tail(self.path)
        self.assertTrue(Path(report["quarantine_path"]).is_file())
        self.assertIn("ev", Path(report["quarantine_path"]).read_text(encoding="utf-8"))
        self.assertEqual(self.path.read_bytes(), before,
                         "accepted history is never rewritten")

    def test_replay_reports_the_truncated_tail(self) -> None:
        self.truncate()
        state = replay.replay_run(self.run_id)
        self.assertEqual(state["events_replayed"], 4)
        self.assertTrue(state["truncated_tail"])


class DurabilityTests(OperatorTestCase):
    def test_append_flushes_and_fsyncs_inside_the_lock(self) -> None:
        """A crash between buffer and platter is exactly how the truncated tail
        was produced; the writer must not leave the row in a buffer."""
        import os

        seen: list[int] = []
        real_fsync = os.fsync

        def recording_fsync(fd: int) -> None:
            seen.append(fd)
            real_fsync(fd)

        os.fsync = recording_fsync
        try:
            history.append("attempt.recorded", {"attempt": 1}, run_id="run-fsync")
        finally:
            os.fsync = real_fsync
        self.assertTrue(seen, "history.append must fsync the event it wrote")


class ReplayFailClosedTests(OperatorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-replay-regression"
        history.append("task.received", {"envelope_id": "a" * 64}, run_id=self.run_id)
        self.path = paths.run_history_path(self.run_id)

    def append_raw(self, event: dict) -> None:
        event["event_id"] = canonical.sha256_hex(canonical.canonical_json(
            {k: v for k, v in event.items() if k != "event_id"}))
        with open(self.path, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def test_replay_refuses_an_unknown_event_type(self) -> None:
        self.append_raw({"contract_version": "operator-history-event.v1", "sequence": 2,
                         "timestamp": "2026-09-17T12:00:00Z", "run_id": self.run_id,
                         "event_type": "quantum.teleported", "refs": {}, "payload": {}})
        with self.assertRaises(Exception) as ctx:
            replay.replay_run(self.run_id)
        self.assertIn("quantum.teleported", str(ctx.exception))

    def test_replay_refuses_an_unknown_contract_version(self) -> None:
        self.append_raw({"contract_version": "operator-history-event.v9", "sequence": 2,
                         "timestamp": "2026-09-17T12:00:00Z", "run_id": self.run_id,
                         "event_type": "outcome.final", "refs": {},
                         "payload": {"verdict": "succeeded"}})
        with self.assertRaises(Exception) as ctx:
            replay.replay_run(self.run_id)
        self.assertIn("operator-history-event.v9", str(ctx.exception))

    def test_replay_refuses_a_row_belonging_to_another_run(self) -> None:
        self.append_raw({"contract_version": "operator-history-event.v1", "sequence": 2,
                         "timestamp": "2026-09-17T12:00:00Z", "run_id": "run-somewhere-else",
                         "event_type": "outcome.final", "refs": {},
                         "payload": {"verdict": "succeeded"}})
        with self.assertRaises(Exception):
            replay.replay_run(self.run_id)

    def test_replay_durability_is_derived_from_artifact_records(self) -> None:
        """`reconstructable = len(events) > 0` was a durability claim derived
        from an event count."""
        state = replay.replay_run(self.run_id)
        self.assertFalse(state["reconstructable"])
        self.assertTrue(state["auditable_only"])


if __name__ == "__main__":
    unittest.main()
