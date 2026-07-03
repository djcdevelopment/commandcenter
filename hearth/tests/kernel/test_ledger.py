"""Ledger: append/query, append-only surface, schema rejection."""

import json
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.ledger import (
    Ledger,
    LedgerValidationError,
    new_event,
    sha256_digest,
    validate_event,
)

CALLER = {"id": "dev-local", "runner_class": "human", "node": "omen"}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / "ledger")

    def test_append_returns_event_id_and_persists_line(self):
        event = new_event(CALLER, "ping", args={"message": "hi"}, result={"pong": "hi"})
        event_id = self.ledger.append(event)
        self.assertEqual(event_id, event["event_id"])
        lines = self.ledger.events_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), event)

    def test_new_event_stamps_digests_and_preview(self):
        args = {"path": "a/b.txt", "content": "x" * 1000}
        event = new_event(CALLER, "fs_write", args=args, result=True)
        self.assertEqual(event["args_digest"], sha256_digest(args))
        self.assertEqual(event["result_digest"], sha256_digest(True))
        self.assertLessEqual(len(event["args_preview"]), 400)
        validate_event(event)

    def test_query_filters_by_caller_tool_ok_since(self):
        other = {"id": "qwen", "runner_class": "local", "node": "omen"}
        self.ledger.append(new_event(CALLER, "ping"))
        second = new_event(other, "fs_write", ok=False, error="boom")
        self.ledger.append(second)
        self.ledger.append(new_event(other, "ping"))

        self.assertEqual(len(self.ledger.query()), 3)
        self.assertEqual(len(self.ledger.query(caller="qwen")), 2)
        self.assertEqual(len(self.ledger.query(tool="ping")), 2)
        failures = self.ledger.query(ok=False)
        self.assertEqual([e["event_id"] for e in failures], [second["event_id"]])
        self.assertEqual(failures[0]["error"], "boom")
        since = self.ledger.query(since=second["ts"])
        self.assertGreaterEqual(len(since), 2)
        self.assertTrue(all(e["ts"] >= second["ts"] for e in since))

    def test_append_only_surface(self):
        first = new_event(CALLER, "ping")
        self.ledger.append(first)
        before = self.ledger.events_path.read_bytes()
        self.ledger.append(new_event(CALLER, "ping"))
        after = self.ledger.events_path.read_bytes()
        self.assertTrue(after.startswith(before))
        for forbidden in ("update", "delete", "remove", "truncate"):
            self.assertFalse(hasattr(self.ledger, forbidden),
                             f"Ledger must not expose {forbidden}()")

    def test_schema_rejection_missing_field(self):
        event = new_event(CALLER, "ping")
        del event["args_digest"]
        with self.assertRaises(LedgerValidationError):
            self.ledger.append(event)
        self.assertFalse(self.ledger.events_path.exists())

    def test_schema_rejection_bad_runner_class(self):
        event = new_event({"id": "x", "runner_class": "alien", "node": "omen"}, "ping")
        with self.assertRaises(LedgerValidationError):
            self.ledger.append(event)

    def test_schema_rejection_bad_digest(self):
        event = new_event(CALLER, "ping")
        event["args_digest"] = "md5:nope"
        with self.assertRaises(LedgerValidationError):
            self.ledger.append(event)

    def test_schema_rejection_extra_field(self):
        event = new_event(CALLER, "ping")
        event["surprise"] = 1
        with self.assertRaises(LedgerValidationError):
            self.ledger.append(event)


if __name__ == "__main__":
    unittest.main()
