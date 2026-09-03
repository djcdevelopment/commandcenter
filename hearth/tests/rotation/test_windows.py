"""Rotation windows (P3, ADR-0044): schema-valid events + a jsonl readers can exclude by."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hearth.rotation.windows import (append_window_row, close_window, excluded_by, open_window,
                                     read_windows, window_row)

REPO = Path(__file__).resolve().parents[3]
SCHEMA = REPO / "contracts" / "workflow-event.schema.json"


def _validate(event: dict) -> None:
    import jsonschema
    jsonschema.Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(event)


class WindowEventTests(unittest.TestCase):
    def test_open_and_close_events_validate_against_the_workflow_event_schema(self) -> None:
        opened = open_window("rot-side-test", 8081, ["phi4-vk1"], "unit test", "claude-frontier",
                             ts="2026-09-03T10:00:00+00:00")
        _validate(opened)
        self.assertEqual(opened["event_type"], "assay.started")
        self.assertEqual(opened["run_id"], "rot-side-test")
        for outcome, etype in (("passed", "assay.passed"), ("failed", "assay.failed"), ("aborted", "assay.failed")):
            closed = close_window(opened, outcome, {"load_s": 8.2}, ts="2026-09-03T10:05:00+00:00")
            _validate(closed)
            self.assertEqual(closed["event_type"], etype)
            self.assertEqual(closed["run_id"], "rot-side-test")
            self.assertEqual(closed["payload"]["result"], outcome)
            self.assertEqual(closed["payload"]["receipts"], {"load_s": 8.2})
        with self.assertRaises(ValueError):
            close_window(opened, "meh")


class WindowLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "rotation-windows.jsonl"

    def test_rows_round_trip_and_open_ended_windows_are_open(self) -> None:
        opened = open_window("w1", 8081, ["phi4-vk1"], "r", "op", ts="2026-09-03T10:00:00+00:00")
        append_window_row(window_row(opened), self.path)
        windows = read_windows(self.path)
        self.assertEqual(len(windows), 1)
        start, end, name = windows[0]
        self.assertEqual(name, "w1")
        self.assertIsNone(end)
        t = datetime(2026, 9, 3, 10, 3, tzinfo=timezone.utc).timestamp()
        self.assertEqual(excluded_by(t, windows, now=t + 60), "w1")

        closed = close_window(opened, "passed", ts="2026-09-03T10:05:00+00:00")
        append_window_row(window_row(closed), self.path)
        windows = read_windows(self.path)
        start, end, name = windows[0]
        self.assertIsNotNone(end)
        inside = datetime(2026, 9, 3, 10, 2, tzinfo=timezone.utc).timestamp()
        after = datetime(2026, 9, 3, 10, 9, tzinfo=timezone.utc).timestamp()
        self.assertEqual(excluded_by(inside, windows), "w1")
        self.assertIsNone(excluded_by(after, windows))

    def test_file_is_bomless_utf8_with_lf(self) -> None:
        opened = open_window("w2", 8081, [], "r", "op", ts="2026-09-03T10:00:00+00:00")
        append_window_row(window_row(opened), self.path)
        raw = self.path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(json.loads(raw.decode("utf-8"))["name"], "w2")

    def test_malformed_lines_are_skipped(self) -> None:
        self.path.write_text('{"broken"\n', encoding="utf-8")
        self.assertEqual(read_windows(self.path), [])
        self.assertEqual(read_windows(self.path.with_name("absent.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
