"""JS1: ledger events carry optional task_class + model, additively.

Covers: legacy (pre-JS1) events still validate; new_event round-trips the two
new fields into both the NDJSON line and the sqlite index; opening a
pre-existing sqlite index (created before these columns existed) migrates in
place via ALTER TABLE and subsequent inserts succeed.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.ledger import Ledger, new_event, validate_event

CALLER = {"id": "dev-local", "runner_class": "human", "node": "omen"}


def _legacy_event(tool: str = "ping") -> dict:
    """Hand-build an event exactly as pre-JS1 new_event() would have, i.e.
    without task_class/model keys at all."""
    event = new_event(CALLER, tool, args={"x": 1}, result={"y": 2})
    del event["task_class"]
    del event["model"]
    return event


class LegacyEventStillValidatesTest(unittest.TestCase):
    def test_legacy_event_without_new_fields_validates(self):
        event = _legacy_event()
        # Must not raise -- additive fields are optional, not required.
        validate_event(event)

    def test_legacy_event_appends_via_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger")
            event_id = ledger.append(_legacy_event())
            events = ledger.query()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], event_id)


class NewEventFieldsRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / "ledger")

    def test_new_event_defaults_to_none(self):
        event = new_event(CALLER, "ping")
        self.assertIsNone(event["task_class"])
        self.assertIsNone(event["model"])
        validate_event(event)

    def test_round_trip_ndjson_and_sqlite(self):
        event = new_event(CALLER, "local_generate", task_class="inference",
                          model="qwen3-coder:30b")
        self.ledger.append(event)

        # NDJSON: read the raw line back.
        lines = self.ledger.events_path.read_text(encoding="utf-8").splitlines()
        stored = json.loads(lines[0])
        self.assertEqual(stored["task_class"], "inference")
        self.assertEqual(stored["model"], "qwen3-coder:30b")

        # query() reconstitutes from the ndjson via the index, same result.
        queried = self.ledger.query(tool="local_generate")
        self.assertEqual(len(queried), 1)
        self.assertEqual(queried[0]["task_class"], "inference")
        self.assertEqual(queried[0]["model"], "qwen3-coder:30b")

        # sqlite index itself carries the columns too.
        conn = sqlite3.connect(self.ledger.index_path)
        try:
            row = conn.execute(
                "SELECT task_class, model FROM events WHERE event_id = ?",
                (event["event_id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("inference", "qwen3-coder:30b"))


class SqliteMigrationTest(unittest.TestCase):
    def test_pre_js1_index_gains_columns_and_accepts_inserts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger"
            ledger_dir.mkdir(parents=True)
            index_path = ledger_dir / "index.sqlite"

            # Build the OLD (pre-JS1) schema by hand -- no task_class/model.
            conn = sqlite3.connect(index_path)
            try:
                with conn:
                    conn.execute(
                        "CREATE TABLE events ("
                        " event_id TEXT PRIMARY KEY,"
                        " ts TEXT NOT NULL,"
                        " caller_id TEXT NOT NULL,"
                        " runner_class TEXT NOT NULL,"
                        " tool TEXT NOT NULL,"
                        " ok INTEGER NOT NULL,"
                        " duration_ms REAL NOT NULL,"
                        " offset INTEGER NOT NULL,"
                        " length INTEGER NOT NULL)"
                    )
                    conn.execute("CREATE INDEX idx_events_ts ON events(ts)")
            finally:
                conn.close()

            conn = sqlite3.connect(index_path)
            try:
                columns_before = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            finally:
                conn.close()
            self.assertNotIn("task_class", columns_before)
            self.assertNotIn("model", columns_before)

            # Opening a Ledger against this dir must migrate in place.
            ledger = Ledger(ledger_dir)
            conn = sqlite3.connect(index_path)
            try:
                columns_after = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            finally:
                conn.close()
            self.assertIn("task_class", columns_after)
            self.assertIn("model", columns_after)

            # And a fresh insert (via append) must succeed after migration.
            event = new_event(CALLER, "ping", task_class="io", model=None)
            ledger.append(event)
            self.assertEqual(len(ledger.query()), 1)


if __name__ == "__main__":
    unittest.main()
