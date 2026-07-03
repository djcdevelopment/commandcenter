from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from tools.workflow.otel_adapter import to_otel_span_event
from tools.workflow.project_state import read_events


ROOT = Path(__file__).resolve().parents[2]


class OtelAdapterTests(TestCase):
    def test_decision_event_maps_semantic_attributes(self) -> None:
        events = read_events(ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl")
        event = next(item for item in events if item["event_type"] == "promotion.held")

        span_event = to_otel_span_event(event)

        self.assertEqual(span_event["name"], "promotion.held")
        self.assertEqual(span_event["attributes"]["semantic.layer"], "business")
        self.assertEqual(span_event["attributes"]["semantic.phase"], "promotion")
        self.assertEqual(span_event["attributes"]["decision_class"], "promotion_hold")
        self.assertTrue(span_event["attributes"]["operator.action_required"])
