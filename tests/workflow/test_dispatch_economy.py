from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_economy import (
    BATTERY_COST_THRESHOLD_PERCENT,
    derive_economic_context,
)
from tools.workflow.reference_runner import (
    CANDIDATE_POOLS,
    run_reference_workflow,
    schedule,
)

ROOT = Path(__file__).resolve().parents[2]
WORK_ITEM = ROOT / "fixtures" / "workflow" / "sample-work-item.md"
DECISION_SCHEMA = json.loads(
    (ROOT / "contracts" / "scheduler-decision.v1.schema.json").read_text(encoding="utf-8")
)


class EconomyDecisionTableTests(TestCase):
    """Unit tests for derive_economic_context — one per decision-table rule."""

    def test_r1_low_battery_yields_cost_per_outcome(self) -> None:
        candidate = {"signals": {"battery_percent": 20.0}}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "cost_per_outcome")
        self.assertIn("battery", result["reason"])
        self.assertIn("battery_percent", result["signals_read"])

    def test_r1_battery_exactly_at_threshold_is_not_triggered(self) -> None:
        # threshold is strictly-less-than; 30.0 itself does NOT fire R1
        candidate = {"signals": {"battery_percent": BATTERY_COST_THRESHOLD_PERCENT}}
        result = derive_economic_context(candidate)
        self.assertNotEqual(result["objective"], "cost_per_outcome")

    def test_r1_battery_overrides_owned_mains(self) -> None:
        # Physics beats ownership: even owned+mains hardware on a draining battery
        # maps to cost_per_outcome, not knowledge_per_hour.
        candidate = {
            "ownership": "owned",
            "power_source": "mains",
            "signals": {"battery_percent": 10.0},
        }
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "cost_per_outcome")
        self.assertIn("physics beats ownership", result["reason"])

    def test_r2_metered_provider_yields_cost_per_outcome(self) -> None:
        candidate = {"ownership": "metered_provider"}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "cost_per_outcome")
        self.assertIn("metered_provider", result["reason"])
        self.assertEqual(result["signals_read"], [])

    def test_r2_battery_signal_present_but_above_threshold_does_not_block_r2(self) -> None:
        candidate = {"ownership": "metered_provider", "signals": {"battery_percent": 80.0}}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "cost_per_outcome")
        self.assertIn("metered_provider", result["reason"])

    def test_r3_owned_mains_yields_knowledge_per_hour(self) -> None:
        candidate = {"ownership": "owned", "power_source": "mains"}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "knowledge_per_hour")
        self.assertIn("mains", result["reason"])
        self.assertEqual(result["signals_read"], [])

    def test_r4_leased_ownership_yields_undetermined_with_reason(self) -> None:
        candidate = {"ownership": "leased"}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "undetermined")
        self.assertIn("leased", result["reason"])

    def test_r4_null_ownership_yields_undetermined_with_reason(self) -> None:
        candidate = {}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "undetermined")
        self.assertIn("null", result["reason"])

    def test_r4_owned_null_power_source_yields_undetermined_with_reason(self) -> None:
        candidate = {"ownership": "owned", "power_source": None}
        result = derive_economic_context(candidate)
        self.assertEqual(result["objective"], "undetermined")
        self.assertIn("power_source=null", result["reason"])

    def test_r4_undetermined_is_reported_with_reason_not_silenced(self) -> None:
        # Any undetermined case must carry a non-empty reason naming the missing input.
        for candidate in [
            {},
            {"ownership": None},
            {"ownership": "leased"},
            {"ownership": "owned", "power_source": None},
        ]:
            with self.subTest(candidate=candidate):
                result = derive_economic_context(candidate)
                self.assertEqual(result["objective"], "undetermined")
                self.assertIsInstance(result["reason"], str)
                self.assertGreater(len(result["reason"]), 0)


class EconomyScenarioTests(TestCase):
    """Scenario test: economy-directed dispatch produces a populated economy_influence."""

    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self) -> Path:
        run_reference_workflow(WORK_ITEM, self.temp_dir, "economy-directed")
        return self.temp_dir / WORK_ITEM.stem

    def _decision(self, run_dir: Path) -> dict:
        return json.loads(
            (run_dir / "artifacts" / "decisions" / "dec_assign_001.json")
            .read_text(encoding="utf-8")
        )

    def test_economy_directed_selects_owned_mains_candidate_on_ranking(self) -> None:
        decision = self._decision(self._run())
        self.assertEqual(decision["selected"]["builder_id"], "builder-owned-mains")

    def test_economy_influence_is_populated(self) -> None:
        decision = self._decision(self._run())
        influence = decision["economy_influence"]
        self.assertIsNotNone(influence)
        self.assertEqual(influence["objective_selected"], "knowledge_per_hour")
        self.assertIsInstance(influence["reason"], str)
        self.assertGreater(len(influence["reason"]), 0)

    def test_economy_influence_has_counterfactual(self) -> None:
        decision = self._decision(self._run())
        cf = decision["economy_influence"]["counterfactual"]
        self.assertIsNotNone(cf)
        self.assertEqual(cf["objective"], "cost_per_outcome")
        self.assertEqual(cf["would_have_chosen"], "builder-metered")
        self.assertIsInstance(cf["note"], str)
        self.assertGreater(len(cf["note"]), 0)

    def test_economy_does_not_affect_ranking(self) -> None:
        # Economy layer is explanation only; the winner is determined by base_score alone.
        # builder-owned-mains (0.9) > builder-metered (0.7) — no economy bonus applied.
        decision = self._decision(self._run())
        scores = {
            entry["builder_id"]: entry["score"]
            for entry in decision["candidates_considered"]
        }
        self.assertEqual(scores["builder-owned-mains"], 0.9)
        self.assertEqual(scores["builder-metered"], 0.7)

    def test_events_stay_valid_and_run_completes(self) -> None:
        from tools.workflow.project_state import read_events
        from tools.workflow.validate_events import validate_file

        run_dir = self._run()
        self.assertEqual(validate_file(run_dir / "events.jsonl"), [])
        events = read_events(run_dir / "events.jsonl")
        self.assertIn("promotion.approved", {event["event_type"] for event in events})


class EconomySchemaStructuralTests(TestCase):
    """Schema structural conformance test, copying the set-inclusion pattern of
    test_dispatch_capability.py lines 71-79."""

    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _decision(self) -> dict:
        run_reference_workflow(WORK_ITEM, self.temp_dir, "economy-directed")
        run_dir = self.temp_dir / WORK_ITEM.stem
        return json.loads(
            (run_dir / "artifacts" / "decisions" / "dec_assign_001.json")
            .read_text(encoding="utf-8")
        )

    def test_economy_influence_conforms_to_contract(self) -> None:
        decision = self._decision()
        # top-level keys are a subset of schema properties
        self.assertLessEqual(set(decision), set(DECISION_SCHEMA["properties"]))
        block_schema = DECISION_SCHEMA["properties"]["economy_influence"]
        influence = decision["economy_influence"]
        # all required keys present
        self.assertLessEqual(set(block_schema["required"]), set(influence))
        # no extra keys beyond schema properties
        self.assertLessEqual(set(influence), set(block_schema["properties"]))
        # counterfactual sub-object conforms
        cf = influence["counterfactual"]
        self.assertIsNotNone(cf)
        cf_schema = block_schema["properties"]["counterfactual"]
        self.assertLessEqual(set(cf_schema["required"]), set(cf))
        self.assertLessEqual(set(cf), set(cf_schema["properties"]))

    def test_economy_influence_null_when_all_candidates_blocked(self) -> None:
        from tools.workflow.reference_runner import load_policy_rules

        policy_path = ROOT / "fixtures" / "workflow" / "policy" / "policy.json"
        selection = schedule(
            CANDIDATE_POOLS["known-bad-only"],
            "reference-build",
            load_policy_rules(policy_path),
        )
        self.assertIsNone(selection["selected"])
        self.assertIsNone(selection["economy_influence"])

    def test_economy_influence_present_for_non_economy_scenarios(self) -> None:
        # economy_influence is always computed when a candidate is selected;
        # for scenarios without ownership facts it is non-null with objective=undetermined.
        run_reference_workflow(WORK_ITEM, self.temp_dir, "happy")
        run_dir = self.temp_dir / WORK_ITEM.stem
        decision = json.loads(
            (run_dir / "artifacts" / "decisions" / "dec_assign_001.json")
            .read_text(encoding="utf-8")
        )
        influence = decision["economy_influence"]
        self.assertIsNotNone(influence)
        self.assertEqual(influence["objective_selected"], "undetermined")
        self.assertIsNone(influence["counterfactual"])
