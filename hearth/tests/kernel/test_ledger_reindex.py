"""Ledger.reindex() + verify(): rebuilding/checking index.sqlite from the
NDJSON source of truth.

PARANOID ISOLATION: every test builds its Ledger against tmp_path (or a
tempfile.TemporaryDirectory), never the real hearth/var/ledger.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.ledger import Ledger, LedgerValidationError, new_event

CALLER = {"id": "dev-local", "runner_class": "human", "node": "omen"}


def _make_ledger(tmp: Path) -> Ledger:
    return Ledger(tmp / "ledger")


class ReindexRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.ledger = _make_ledger(self.tmp_path)

    def test_reindex_rebuilds_identical_query_results(self):
        for i in range(5):
            other = {"id": "qwen", "runner_class": "local", "node": "omen"}
            caller = CALLER if i % 2 == 0 else other
            self.ledger.append(new_event(caller, f"tool_{i}", args={"i": i},
                                          result={"ok": True}, ok=(i != 3)))

        before = self.ledger.query()
        self.assertEqual(len(before), 5)

        self.ledger.index_path.unlink()
        # Reopening builds a fresh empty index (no rows) since NDJSON isn't
        # consulted by __init__; then reindex() must repopulate it.
        ledger2 = Ledger(self.tmp_path / "ledger")
        self.assertEqual(ledger2.query(), [])

        count = ledger2.reindex()
        self.assertEqual(count, 5)

        after = ledger2.query()
        self.assertEqual(after, before)

        # Also check filtered queries still work correctly post-reindex.
        self.assertEqual(len(ledger2.query(caller="qwen")), 2)
        self.assertEqual(len(ledger2.query(ok=False)), 1)

    def test_reindex_on_empty_ledger_returns_zero(self):
        count = self.ledger.reindex()
        self.assertEqual(count, 0)
        self.assertEqual(self.ledger.query(), [])


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.ledger = _make_ledger(self.tmp_path)

    def test_verify_ok_on_healthy_store(self):
        for i in range(3):
            self.ledger.append(new_event(CALLER, f"tool_{i}"))
        result = self.ledger.verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["index_rows"], 3)
        self.assertEqual(result["ndjson_lines"], 3)
        self.assertEqual(result["mismatches"], [])

    def test_verify_flags_mismatch_after_hand_edited_index(self):
        event = new_event(CALLER, "ping")
        self.ledger.append(event)

        # Hand-corrupt the index: point the row at a bogus offset/length so
        # the slice it reads back does not parse as JSON.
        conn = sqlite3.connect(self.ledger.index_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE events SET offset = 999999, length = 5 WHERE event_id = ?",
                    (event["event_id"],),
                )
        finally:
            conn.close()

        result = self.ledger.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["mismatches"][0]["event_id"], event["event_id"])

    def test_verify_does_not_mutate_store(self):
        self.ledger.append(new_event(CALLER, "ping"))
        before_ndjson = self.ledger.events_path.read_bytes()
        conn = sqlite3.connect(self.ledger.index_path)
        try:
            before_rows = conn.execute("SELECT * FROM events").fetchall()
        finally:
            conn.close()

        self.ledger.verify()

        after_ndjson = self.ledger.events_path.read_bytes()
        conn = sqlite3.connect(self.ledger.index_path)
        try:
            after_rows = conn.execute("SELECT * FROM events").fetchall()
        finally:
            conn.close()
        self.assertEqual(before_ndjson, after_ndjson)
        self.assertEqual(before_rows, after_rows)


class TruncatedFinalLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.ledger = _make_ledger(self.tmp_path)

    def test_truncated_final_line_reindexes_n_minus_1_without_crashing(self):
        events = [new_event(CALLER, f"tool_{i}") for i in range(4)]
        for event in events:
            self.ledger.append(event)

        # Simulate a torn final write: append a partial JSON line with no
        # trailing newline, as if the process died mid-write.
        with self.ledger.events_path.open("ab") as fh:
            fh.write(b'{"schema": "hearth-event.v1", "event_id": "trunc')

        count = self.ledger.reindex()
        self.assertEqual(count, 4)
        results = self.ledger.query()
        self.assertEqual(len(results), 4)
        self.assertEqual(
            {e["event_id"] for e in results},
            {e["event_id"] for e in events},
        )


class TornMiddleLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.ledger = _make_ledger(self.tmp_path)

    def test_torn_middle_line_raises(self):
        events = [new_event(CALLER, f"tool_{i}") for i in range(3)]
        for event in events:
            self.ledger.append(event)

        # Corrupt the middle line in place: truncate its length so it no
        # longer round-trips as valid JSON, but keep a trailing newline (and
        # a valid, complete line after it) so it is NOT the last line.
        lines = self.ledger.events_path.read_bytes().splitlines(keepends=True)
        self.assertEqual(len(lines), 3)
        corrupted = lines[1][:20] + b"\n"  # torn mid-object, still newline-terminated
        lines[1] = corrupted
        self.ledger.events_path.write_bytes(b"".join(lines))

        with self.assertRaises(LedgerValidationError):
            self.ledger.reindex()


if __name__ == "__main__":
    unittest.main()
