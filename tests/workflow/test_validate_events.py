from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from tools.workflow.validate_events import ValidationError, validate_event, validate_file


ROOT = Path(__file__).resolve().parents[2]


class ValidateEventsTests(TestCase):
    def test_fixtures_validate(self) -> None:
        files = [
            ROOT / "fixtures" / "workflow" / "happy-path.events.jsonl",
            ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl",
        ] + sorted((ROOT / "fixtures" / "workflow" / "runs").rglob("events.jsonl"))
        self.assertGreaterEqual(len(files), 7)
        for path in files:
            self.assertEqual(validate_file(path), [])

    def test_missing_question_id_fails(self) -> None:
        event = {
            "event_id": "evt_bad",
            "event_type": "question.raised",
            "timestamp": "2026-07-02T12:00:00Z",
            "workflow_id": "wf_bad",
            "run_id": "run_bad",
            "actor": {"type": "builder", "id": "builder-1"},
            "status": "waiting_on_operator",
            "payload": {},
        }
        with self.assertRaises(ValidationError):
            validate_event(event)

    def test_builder_assignment_requires_decision_fields(self) -> None:
        event = {
            "event_id": "evt_bad_assignment",
            "event_type": "builder.assigned",
            "timestamp": "2026-07-02T12:00:00Z",
            "workflow_id": "wf_bad",
            "run_id": "run_bad",
            "lap_id": "lap_001",
            "actor": {"type": "builder", "id": "builder-1"},
            "status": "assigned",
            "payload": {},
        }
        with self.assertRaises(ValidationError):
            validate_event(event)

    def test_question_answer_requires_decision_fields(self) -> None:
        event = {
            "event_id": "evt_bad_decision",
            "event_type": "question.answered",
            "timestamp": "2026-07-02T12:00:00Z",
            "workflow_id": "wf_bad",
            "run_id": "run_bad",
            "question_id": "q_bad",
            "actor": {"type": "operator", "id": "derek"},
            "status": "answered",
            "payload": {},
        }
        with self.assertRaises(ValidationError):
            validate_event(event)
