from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_capacity import collect_event_files
from tools.workflow.project_findings import materialize_findings
from tools.workflow.reference_runner import (
    CANDIDATE_POOLS,
    load_policy_rules,
    run_reference_workflow,
    schedule,
)
from tools.workflow.validate_events import validate_file


ROOT = Path(__file__).resolve().parents[2]
WORK_ITEM = ROOT / "fixtures" / "workflow" / "sample-work-item.md"
POLICY = ROOT / "fixtures" / "workflow" / "policy" / "policy.json"
POLICY_SUSPENDED = ROOT / "fixtures" / "workflow" / "policy" / "policy-suspended.json"
POLICY_SCHEMA = json.loads((ROOT / "contracts" / "policy.v1.schema.json").read_text(encoding="utf-8"))
DECISION_SCHEMA = json.loads((ROOT / "contracts" / "scheduler-decision.v1.schema.json").read_text(encoding="utf-8"))


class DispatchPolicyTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, scenario: str, policy: Path) -> tuple[dict, Path]:
        state = run_reference_workflow(WORK_ITEM, self.temp_dir, scenario, policy_path=policy)
        return state, self.temp_dir / WORK_ITEM.stem

    def _decision(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "artifacts" / "decisions" / "dec_assign_001.json").read_text(encoding="utf-8"))

    def test_blocked_known_bad_combo_does_not_dispatch(self) -> None:
        state, run_dir = self._run("policy-blocked", POLICY)
        self.assertEqual(state["dispatch"]["status"], "blocked")
        self.assertIn("block:claudefarm1|qwen3-30b-a3b-awq|vllm|*|*",
                      state["dispatch"]["candidates_blocked"][0]["policy_ids"])
        self.assertFalse((run_dir / "artifacts" / "decisions").exists())
        events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("builder.assigned", {event["event_type"] for event in events})
        # the refusal still explains itself on disk
        block_artifact = json.loads((run_dir / "artifacts" / "policy-block.json").read_text(encoding="utf-8"))
        self.assertEqual(block_artifact["candidates_considered"][0]["rejected_reason"],
                         "policy: block:claudefarm1|qwen3-30b-a3b-awq|vllm|*|*")
        self.assertEqual(validate_file(run_dir / "events.jsonl"), [])

    def test_experiment_flag_dispatches_and_declares_the_experiment(self) -> None:
        state, run_dir = self._run("policy-experiment", POLICY)
        decision = self._decision(run_dir)
        self.assertEqual(decision["selected"]["backend"], "vllm")
        influence = decision["policy_influence"]
        self.assertTrue(influence["experiment_flag"])
        self.assertTrue(influence["requires_experiment_flag"])  # this dispatch IS an experiment, on the record
        matched = {rule["policy_id"] for rule in influence["matched_rules"]}
        self.assertIn("block:claudefarm1|qwen3-30b-a3b-awq|vllm|*|*", matched)
        self.assertEqual(state["status"], "approved")
        self.assertEqual(validate_file(run_dir / "events.jsonl"), [])

    def test_experiment_dispatch_carries_bias_corrected_predictions(self) -> None:
        _, run_dir = self._run("policy-experiment", POLICY)
        decision = self._decision(run_dir)
        self.assertEqual(decision["predictions"]["expected_peak_ram_mb"], 28416.0)  # 18432 + 9984
        adjustment = decision["policy_influence"]["adjustments_applied"][0]
        self.assertEqual(adjustment["before"], 18432.0)
        self.assertEqual(adjustment["after"], 28416.0)
        self.assertEqual(adjustment["policy_id"], "adjust_prediction:*|qwen3-30b-a3b-awq|*|*|expected_peak_ram_mb")

    def test_prediction_adjusted_dispatch_corrects_before_decision_is_written(self) -> None:
        _, run_dir = self._run("policy-adjusted", POLICY)
        decision = self._decision(run_dir)
        self.assertEqual(decision["predictions"]["expected_generation_tokens_per_second"], 32.0)  # 40 - 8
        influence = decision["policy_influence"]
        effects = {rule["effect"] for rule in influence["matched_rules"]}
        self.assertIn("adjust_prediction", effects)
        self.assertIn("prefer", effects)  # preference noted, and it did not gate anything
        self.assertFalse(influence["requires_experiment_flag"])

    def test_suspended_policy_dispatches_but_stays_on_the_record(self) -> None:
        _, run_dir = self._run("policy-suspended", POLICY_SUSPENDED)
        decision = self._decision(run_dir)
        self.assertEqual(decision["selected"]["backend"], "vllm")  # suspended rule gates nothing
        influence = decision["policy_influence"]
        self.assertFalse(influence["requires_experiment_flag"])
        matched = {rule["policy_id"]: rule["status"] for rule in influence["matched_rules"]}
        self.assertEqual(matched["block:claudefarm1|qwen3-30b-a3b-awq|vllm|*|*"], "suspended")

    def test_unpoliced_scenarios_say_so_and_stay_unchanged(self) -> None:
        state = run_reference_workflow(WORK_ITEM, self.temp_dir, "happy")
        decision = self._decision(self.temp_dir / WORK_ITEM.stem)
        self.assertFalse(decision["policy_influence"]["policy_evaluated"])
        self.assertEqual(decision["decision_reason"], "only local reference builder registered")
        self.assertEqual(decision["predictions"]["expected_generation_tokens_per_second"], 40.0)
        self.assertEqual(state["status"], "approved")

    def test_policy_scenarios_refuse_to_run_without_policy(self) -> None:
        with self.assertRaises(ValueError):
            run_reference_workflow(WORK_ITEM, self.temp_dir, "policy-blocked")

    def test_schedule_prefers_known_good_and_records_the_blocked_rival(self) -> None:
        pool = CANDIDATE_POOLS["known-bad-only"] + CANDIDATE_POOLS["default"]
        selection = schedule(pool, "reference-build", load_policy_rules(POLICY))
        self.assertEqual(selection["selected"]["builder_id"], "builder-1")
        winner = next(entry for entry in selection["candidates_considered"] if entry["selected"])
        self.assertEqual(winner["score"], 1.1)  # base 1.0 + prefer bonus
        self.assertEqual(selection["candidates_blocked"][0]["backend"], "vllm")
        self.assertEqual(selection["policy_influence"]["candidates_blocked"],
                         selection["candidates_blocked"])

    def test_dispatch_outcomes_project_normally_through_the_knowledge_loop(self) -> None:
        self._run("policy-experiment", POLICY)
        knowledge_dir = self.temp_dir / "knowledge"
        content = materialize_findings(collect_event_files([self.temp_dir]), knowledge_dir)
        self.assertEqual(content["observation_count"], 1)
        self.assertEqual(content["unresolved_refs"], 0)
        # one successful experiment run = fresh evidence against the known_bad belief
        combos = {(f["subject"]["builder_id"], f["subject"]["backend"]) for f in content["findings"]}
        self.assertIn(("claudefarm1", "vllm"), combos)

    def test_written_decisions_conform_to_extended_contract(self) -> None:
        allowed = set(DECISION_SCHEMA["properties"])
        influence_allowed = set(DECISION_SCHEMA["properties"]["policy_influence"]["properties"])
        influence_required = set(DECISION_SCHEMA["properties"]["policy_influence"]["required"])
        for scenario, policy in (("policy-experiment", POLICY), ("policy-adjusted", POLICY),
                                 ("policy-suspended", POLICY_SUSPENDED)):
            _, run_dir = self._run(scenario, policy)
            decision = self._decision(run_dir)
            self.assertLessEqual(set(decision), allowed)
            influence = decision["policy_influence"]
            self.assertEqual(set(influence) - influence_allowed, set())
            self.assertLessEqual(influence_required, set(influence))

    def test_fixture_policies_conform_to_policy_contract(self) -> None:
        required = set(POLICY_SCHEMA["required"])
        allowed = set(POLICY_SCHEMA["properties"])
        for fixture in (POLICY, POLICY_SUSPENDED):
            for rule in json.loads(fixture.read_text(encoding="utf-8"))["rules"]:
                self.assertLessEqual(required, set(rule))
                self.assertLessEqual(set(rule), allowed)
                self.assertEqual(rule["contract_version"], "policy.v1")
