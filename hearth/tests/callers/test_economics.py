from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.projection.economics import summarize


def make_event(runner_class: str, tool: str, ok: bool, duration_ms: int, tokens_in: int, tokens_out: int) -> dict:
    return {
        "schema": "hearth-event.v1",
        "event_id": f"he_{runner_class}_{tool}_{duration_ms}",
        "ts": "2026-07-03T12:00:00+00:00",
        "caller": {"id": f"{runner_class}-1", "runner_class": runner_class, "node": "omen"},
        "tool": tool,
        "ok": ok,
        "duration_ms": duration_ms,
        "cost": {"tokens_in": tokens_in, "tokens_out": tokens_out, "watt_s": None},
    }


class EconomicsTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "events.ndjson"

    def write_ledger(self, events: list[dict], extra_lines: list[str] | None = None) -> None:
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
            for line in extra_lines or []:
                handle.write(line + "\n")

    def test_summarize_buckets_by_runner_class_and_tool(self) -> None:
        self.write_ledger(
            [
                make_event("frontier", "run_tests", True, 1000, 500, 50),
                make_event("frontier", "run_tests", False, 2000, 700, 10),
                make_event("local", "run_tests", True, 4000, 900, 200),
                make_event("local", "record_event", True, 50, 100, 20),
            ]
        )

        summary = summarize(self.ledger)

        frontier = summary["per_runner_class"]["frontier"]
        self.assertEqual(frontier["calls"], 2)
        self.assertEqual(frontier["ok_rate"], 0.5)
        self.assertEqual(frontier["total_duration_ms"], 3000)
        self.assertEqual(frontier["tokens_in"], 1200)
        self.assertEqual(frontier["tokens_out"], 60)

        run_tests = summary["per_tool"]["run_tests"]
        self.assertEqual(run_tests["calls"], 3)
        self.assertEqual(run_tests["total_duration_ms"], 7000)

        self.assertEqual(summary["frontier_vs_local"]["local"]["calls"], 2)
        self.assertEqual(summary["frontier_vs_local"]["frontier"]["calls"], 2)
        self.assertEqual(summary["events"], 4)

    def test_missing_cost_fields_and_bad_lines_tolerated(self) -> None:
        event = make_event("local", "fs_read", True, 10, 0, 0)
        del event["cost"]
        self.write_ledger([event], extra_lines=["{ broken"])

        summary = summarize(self.ledger)

        self.assertEqual(summary["events"], 1)
        self.assertEqual(summary["parse_errors"], 1)
        self.assertEqual(summary["per_runner_class"]["local"]["tokens_in"], 0)

    def test_empty_or_missing_ledger(self) -> None:
        summary = summarize(self.ledger)  # never written
        self.assertEqual(summary["events"], 0)
        self.assertEqual(summary["frontier_vs_local"]["frontier"]["calls"], 0)
