"""Tests for tools/workflow/lexicon.py -- the presentation lexicon.

The lexicon is presentation-only: lore phrasing rendered on top of the
technical ontology, never written to the ledger. These tests pin the core
contract:
  * completeness (the drift guard): every EVENT_TYPES entry has a lexicon
    events entry -- a new ontology type without lore fails here;
  * unknown-type / unknown-actor fallback returns the technical string verbatim;
  * actor resolution: the guard-dog exact rule + inventory enrichment;
  * label_event carries technical + phase display through.

stdlib only (tomllib), matching the repo's no-PyYAML rule.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase

from tools.workflow import lexicon
from tools.workflow.ontology import EVENT_TYPES


class CompletenessTests(TestCase):
    def test_every_event_type_has_a_lexicon_entry(self) -> None:
        # Drift guard: if this fails, add the listed event types to lexicon.toml.
        missing = lexicon.check_completeness()
        self.assertEqual(missing, [], f"event types missing from lexicon: {missing}")

    def test_events_cover_exactly_the_ontology(self) -> None:
        events = lexicon.load_lexicon().get("events", {})
        self.assertEqual(set(events.keys()), set(EVENT_TYPES))


class LabelEventTests(TestCase):
    def test_known_event_maps_to_lore_display(self) -> None:
        result = lexicon.label_event({"event_type": "question.raised"})
        self.assertEqual(result["display"], "The guard dog barks")
        self.assertEqual(result["technical"], "question.raised")
        self.assertEqual(result["phase_display"], "At the bench")  # builder phase
        self.assertTrue(result["gloss"])

    def test_promotion_held_maps_to_the_gate(self) -> None:
        result = lexicon.label_event({"event_type": "promotion.held"})
        self.assertEqual(result["display"], "Held at the gate")
        self.assertEqual(result["phase_display"], "The gate")  # promotion phase

    def test_unknown_event_type_falls_back_to_technical(self) -> None:
        result = lexicon.label_event({"event_type": "totally.made.up"})
        self.assertEqual(result["display"], "totally.made.up")
        self.assertEqual(result["technical"], "totally.made.up")
        self.assertIsNone(result["phase_display"])
        self.assertIsNone(result["gloss"])

    def test_idle_events_have_a_phase(self) -> None:
        observed = lexicon.label_event({"event_type": "idle.observed"})
        self.assertEqual(observed["phase_display"], "By the banked fire")
        ended = lexicon.label_event({"event_type": "idle.ended"})
        self.assertEqual(ended["phase_display"], "By the banked fire")


class ToolAwareDisplayTests(TestCase):
    def test_known_gateway_tool_overrides_event_display(self) -> None:
        result = lexicon.label_event(
            {"event_type": "work.accepted", "payload": {"tool": "mechnet_watchdog.dream"}}
        )
        self.assertEqual(result["display"], "The guard dog dreams")
        # Phase/gloss still come from the event type, unaffected by the tool.
        self.assertEqual(result["gloss"], "A new unit of work was accepted and is now tracked.")

    def test_all_specified_gateway_tools_map(self) -> None:
        expected = {
            "mechnet_watchdog.dream": "The guard dog dreams",
            "mechnet_watchdog.patrol_snapshot": "The guard dog makes its rounds",
            "mechnet_watchdog.patrol_trend": "The guard dog reads old tracks",
            "mechnet_watchdog.patrol": "The guard dog patrols",
            "mechnet_watchdog.revive": "The guard dog nudges a sleeper awake",
            "mechnet_watchdog.watchfire": "The watchfire is tended",
            "bankedfire_drain.tick": "Embers stirred",
        }
        for tool, display in expected.items():
            with self.subTest(tool=tool):
                result = lexicon.label_event(
                    {"event_type": "work.accepted", "payload": {"tool": tool}}
                )
                self.assertEqual(result["display"], display)

    def test_unknown_tool_falls_back_to_event_type_display(self) -> None:
        result = lexicon.label_event(
            {"event_type": "work.accepted", "payload": {"tool": "read_file"}}
        )
        self.assertEqual(result["display"], "Work enters the hearth")

    def test_missing_payload_falls_back_to_event_type_display(self) -> None:
        result = lexicon.label_event({"event_type": "work.accepted"})
        self.assertEqual(result["display"], "Work enters the hearth")


class LabelActorTests(TestCase):
    def test_guard_dog_exact_rule(self) -> None:
        result = lexicon.label_actor("mechnet-watchdog")
        self.assertEqual(result["display"], "the guard dog")
        self.assertEqual(result["technical"], "mechnet-watchdog")
        self.assertIn("mechnet_watchdog", result["tooltip"])

    def test_ember_drain_exact_rule(self) -> None:
        result = lexicon.label_actor("bankedfire-drain")
        self.assertEqual(result["display"], "the ember drain")

    def test_claude_frontier_exact_rule(self) -> None:
        result = lexicon.label_actor("claude-frontier")
        self.assertEqual(result["display"], "the frontier hand")

    def test_dev_local_exact_rule(self) -> None:
        result = lexicon.label_actor("dev-local")
        self.assertEqual(result["display"], "the keeper")

    def test_unknown_actor_falls_back_to_id_verbatim(self) -> None:
        result = lexicon.label_actor("some-random-caller")
        self.assertEqual(result["display"], "some-random-caller")
        self.assertIsNone(result["tooltip"])

    def test_empty_actor_id_is_safe(self) -> None:
        result = lexicon.label_actor(None)
        self.assertEqual(result["display"], "")

    def test_inventory_enrichment(self) -> None:
        inv = textwrap.dedent("""
            [meta]
            updated = "test"

            [[node]]
            name = "cc-builder-1"
            kind = "vm"
            purpose = "Frontier builder VM (claude/sonnet runner)."

            [[node]]
            name = "omen"
            kind = "physical-host"
            purpose = "Hypervisor -- not a builder, must not enrich."
        """)
        with tempfile.TemporaryDirectory() as tmp:
            inv_path = Path(tmp) / "inventory.toml"
            inv_path.write_text(inv, encoding="utf-8")

            builder = lexicon.label_actor("cc-builder-1", inventory_path=inv_path)
            self.assertEqual(builder["display"], "cc-builder-1")
            self.assertIn("Frontier builder", builder["tooltip"])

            # A physical-host is not in match_kinds -> no enrichment, id verbatim.
            host = lexicon.label_actor("omen", inventory_path=inv_path)
            self.assertEqual(host["display"], "omen")
            self.assertIsNone(host["tooltip"])

    def test_missing_inventory_is_safe(self) -> None:
        result = lexicon.label_actor(
            "cc-builder-1", inventory_path=Path("does-not-exist.toml")
        )
        self.assertEqual(result["display"], "cc-builder-1")
        self.assertIsNone(result["tooltip"])


class MetaTests(TestCase):
    def test_charter_is_presentation_only(self) -> None:
        meta = lexicon.load_lexicon().get("meta", {})
        self.assertIn("ledger stays technical", meta.get("charter", ""))
