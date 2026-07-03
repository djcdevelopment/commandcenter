from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.projection.ledger_adapter import map_event, project_ledger
from tools.workflow.validate_events import ValidationError, validate_event, validate_file


def make_hearth_event(event_id: str, **overrides: object) -> dict:
    event = {
        "schema": "hearth-event.v1",
        "event_id": event_id,
        "ts": "2026-07-03T12:00:00+00:00",
        "caller": {"id": "claude-code-derek", "runner_class": "frontier", "node": "omen"},
        "tool": "record_event",
        "args_digest": "sha256:" + "a" * 64,
        "args_preview": '{"event": {"kind": "test"}}',
        "result_digest": "sha256:" + "b" * 64,
        "ok": True,
        "error": None,
        "duration_ms": 42,
        "cost": {"tokens_in": 100, "tokens_out": 25, "watt_s": None},
        "task_id": "task-001",
    }
    event.update(overrides)
    return event


def write_ndjson(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


class MapEventTests(TestCase):
    def test_maps_to_valid_workflow_event(self) -> None:
        workflow_event = map_event(make_hearth_event("he_001"))

        validate_event(workflow_event)  # existing machinery, raises on failure
        self.assertEqual(workflow_event["event_id"], "evt_hearth_he_001")
        self.assertEqual(workflow_event["event_type"], "work.accepted")
        self.assertEqual(workflow_event["timestamp"], "2026-07-03T12:00:00+00:00")
        self.assertEqual(workflow_event["actor"], {"type": "builder", "id": "claude-code-derek"})
        self.assertEqual(workflow_event["status"], "completed")
        self.assertEqual(workflow_event["outcome"], "success")
        self.assertEqual(workflow_event["segment_id"], "task-001")

    def test_carries_economics_into_payload(self) -> None:
        payload = map_event(make_hearth_event("he_002"))["payload"]

        self.assertEqual(payload["duration_ms"], 42)
        self.assertEqual(payload["cost"], {"tokens_in": 100, "tokens_out": 25, "watt_s": None})
        self.assertEqual(payload["tool"], "record_event")
        self.assertEqual(payload["runner_class"], "frontier")
        self.assertEqual(payload["node"], "omen")
        self.assertEqual(payload["args_digest"], "sha256:" + "a" * 64)

    def test_failed_call_maps_to_failed_status(self) -> None:
        workflow_event = map_event(
            make_hearth_event("he_003", ok=False, error="tool exploded")
        )

        validate_event(workflow_event)
        self.assertEqual(workflow_event["status"], "failed")
        self.assertEqual(workflow_event["outcome"], "failure")
        self.assertEqual(workflow_event["payload"]["error"], "tool exploded")

    def test_runner_class_maps_actor_type(self) -> None:
        local = make_hearth_event("he_004", caller={"id": "omen-worker-1", "runner_class": "local", "node": "omen"})
        human = make_hearth_event("he_005", caller={"id": "derek", "runner_class": "human", "node": "omen"})

        self.assertEqual(map_event(local)["actor"]["type"], "builder")
        self.assertEqual(map_event(human)["actor"]["type"], "operator")

    def test_rejects_unknown_schema_and_runner_class(self) -> None:
        with self.assertRaises(ValidationError):
            map_event(make_hearth_event("he_006", schema="hearth-event.v2"))
        with self.assertRaises(ValidationError):
            map_event(make_hearth_event("he_007", caller={"id": "x", "runner_class": "alien", "node": "n"}))


class ProjectLedgerTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ledger = root / "ledger" / "events.ndjson"
        self.target = root / "runs" / "hearth-gateway" / "events.jsonl"
        self.cursor = root / "projection_cursor.json"

    def test_dry_run_writes_nothing(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102")])

        summary = project_ledger(self.ledger, self.target, self.cursor, dry_run=True)

        self.assertEqual(summary, {"processed": 2, "skipped": 0, "errors": []})
        self.assertFalse(self.target.exists())
        self.assertFalse(self.cursor.exists())

    def test_appends_valid_workflow_events(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102", ok=False, error="boom")])

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(validate_file(self.target), [])  # existing machinery
        lines = [json.loads(line) for line in self.target.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event_id"] for e in lines], ["evt_hearth_he_101", "evt_hearth_he_102"])

    def test_rerun_is_idempotent_via_cursor(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102")])
        project_ledger(self.ledger, self.target, self.cursor)

        second = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(second, {"processed": 0, "skipped": 2, "errors": []})

        # ledger grows append-only; only the new event is processed
        with self.ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_103")) + "\n")
        third = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(third, {"processed": 1, "skipped": 2, "errors": []})

        lines = self.target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)

    def test_bad_lines_reported_and_good_lines_still_land(self) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_101")) + "\n")
            handle.write("not json at all\n")
            handle.write(json.dumps(make_hearth_event("he_103")) + "\n")

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn(":2:", summary["errors"][0])

    def test_missing_ledger_reports_error(self) -> None:
        summary = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(len(summary["errors"]), 1)
