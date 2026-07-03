from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from tools.workflow.project_state import project_events, read_events


ROOT = Path(__file__).resolve().parents[2]


class ProjectStateTests(TestCase):
    def test_happy_path_projects_to_approved(self) -> None:
        events = read_events(ROOT / "fixtures" / "workflow" / "happy-path.events.jsonl")
        state = project_events(events)

        self.assertEqual(state["workflow_id"], "wf_001")
        self.assertEqual(state["run_id"], "run_001")
        self.assertEqual(state["status"], "approved")
        self.assertEqual(state["candidate_id"], "cand_001")
        self.assertEqual(state["promotion_id"], "promo_001")
        self.assertFalse(state["operator_action_required"])
        self.assertEqual([d["decision_class"] for d in state["decisions"]], ["builder_assignment", "promotion_approval"])

    def test_hold_path_projects_waiting_on_operator(self) -> None:
        events = read_events(ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl")
        state = project_events(events)

        self.assertEqual(state["status"], "held")
        self.assertTrue(state["operator_action_required"])
        self.assertEqual(state["question_id"], "q_001")
        self.assertEqual(state["promotion_id"], "promo_002")
        self.assertEqual([d["decision_class"] for d in state["decisions"]], ["builder_assignment", "question_answer", "promotion_hold"])
