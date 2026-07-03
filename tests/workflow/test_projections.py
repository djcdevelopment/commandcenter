from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from tools.workflow.project_state import project_events, read_events
from tools.workflow.projections import project_board


ROOT = Path(__file__).resolve().parents[2]


class ProjectionTests(TestCase):
    def test_board_projection_is_derived_from_state(self) -> None:
        events = read_events(ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl")
        state = project_events(events)

        board = project_board(state)

        self.assertEqual(board["status"], "held")
        self.assertEqual(board["current_phase"], "promotion")
        self.assertTrue(board["operator_action_required"])
        self.assertEqual(board["decision_count"], 3)
        self.assertEqual(board["active_builder_ids"], ["builder-2"])
