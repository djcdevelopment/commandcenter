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

    def test_display_attributes_added_but_name_stays_technical(self) -> None:
        events = read_events(ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl")
        event = next(item for item in events if item["event_type"] == "promotion.held")

        span_event = to_otel_span_event(event)
        attributes = span_event["attributes"]

        # Span name and technical attributes are UNCHANGED (tooling groups by them).
        self.assertEqual(span_event["name"], "promotion.held")
        self.assertEqual(attributes["semantic.event"], "promotion.held")

        # Presentation attributes are additive.
        self.assertEqual(attributes["display.label"], "Held at the gate")
        self.assertEqual(attributes["display.phase"], "The gate")

    def test_gateway_tool_override_flows_into_display_label(self) -> None:
        span_event = to_otel_span_event(
            {
                "event_type": "work.accepted",
                "workflow_id": "wf-hearth-gateway",
                "run_id": "hearth-gateway",
                "status": "completed",
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"tool": "mechnet_watchdog.dream"},
            }
        )
        # Span name and semantic.event stay the neutral technical type.
        self.assertEqual(span_event["name"], "work.accepted")
        self.assertEqual(span_event["attributes"]["semantic.event"], "work.accepted")
        # display.label reflects the tool-aware override.
        self.assertEqual(span_event["attributes"]["display.label"], "The guard dog dreams")

    def test_unknown_event_type_display_falls_back_to_technical(self) -> None:
        span_event = to_otel_span_event(
            {
                "event_type": "totally.made.up",
                "workflow_id": "wf",
                "run_id": "r",
                "status": "completed",
                "timestamp": "2026-07-04T00:00:00Z",
            }
        )
        self.assertEqual(span_event["name"], "totally.made.up")
        self.assertEqual(span_event["attributes"]["display.label"], "totally.made.up")
        # No phase for an unknown type -> no display.phase attribute.
        self.assertNotIn("display.phase", span_event["attributes"])
