from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_capacity import collect_event_files, extract_observations, extract_scheduler_decisions
from tools.workflow.project_experiments import (
    materialize_experiments,
    synthesize_candidates,
    synthesize_results,
)
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_policy import synthesize_policy
from tools.workflow.project_state import read_events
from tools.workflow.reference_runner import run_reference_workflow
from tools.workflow.validate_events import validate_file


ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
WORK_ITEM = ROOT / "fixtures" / "workflow" / "sample-work-item.md"
POLICY = ROOT / "fixtures" / "workflow" / "policy" / "policy.json"
PLAN_SCHEMA = json.loads((ROOT / "contracts" / "experiment-plan.v1.schema.json").read_text(encoding="utf-8"))
RESULT_SCHEMA = json.loads((ROOT / "contracts" / "experiment-result.v1.schema.json").read_text(encoding="utf-8"))

VLLM_COMBO = "claudefarm1|qwen3-30b-a3b-awq|vllm"


def _fixture_corpus() -> tuple[list[dict], list[dict]]:
    observations: list[dict] = []
    decisions: list[dict] = []
    for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
        events = read_events(events_path)
        extracted_observations, _ = extract_observations(events, events_path)
        extracted_decisions, _ = extract_scheduler_decisions(events, events_path)
        observations.extend(extracted_observations)
        decisions.extend(extracted_decisions)
    return observations, decisions


def _candidates_for(observations: list[dict], decisions: list[dict]) -> list[dict]:
    findings = synthesize_findings(observations, decisions)
    return synthesize_candidates(findings, synthesize_policy(findings))


def _fixture_candidates() -> list[dict]:
    return _candidates_for(*_fixture_corpus())


def _by_id(candidates: list[dict]) -> dict[str, dict]:
    return {candidate["candidate_id"]: candidate for candidate in candidates}


def _observation(observation_id: str, timestamp: str, outcome: str, failure_class: str | None = None,
                 builder_id: str = "builder-x", backend: str = "backend-z") -> dict:
    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": observation_id,
        "decision_id": None,
        "workflow_id": "wf_test",
        "run_id": "run_test",
        "timestamp": timestamp,
        "builder_id": builder_id,
        "model_id": "model-y",
        "backend": backend,
        "workload_shape": {"task_kind": "build"},
        "observed": {"tokens_per_s": 40.0},
        "outcome": outcome,
        "failure_class": failure_class,
        "promotion_status": None,
    }


class ExperimentCandidateTests(TestCase):
    def test_known_bad_block_yields_retest_candidate_with_its_gate(self) -> None:
        candidate = _by_id(_fixture_candidates())[f"known_bad_retest:{VLLM_COMBO}"]
        self.assertEqual(candidate["target_finding_id"], f"known_bad:{VLLM_COMBO}")
        self.assertEqual(candidate["gate"]["policy_id"], f"block:{VLLM_COMBO}|*|*")
        self.assertEqual(candidate["gate"]["flag"], "experiment_flag")
        self.assertIn("known_bad -> uncertain", candidate["evidence_sought"])
        self.assertIn("moe_offload_crash", candidate["risk_accepted"])  # the cost is stated, not implied

    def test_prediction_bias_yields_ungated_calibration_candidate(self) -> None:
        candidates = [c for c in _fixture_candidates() if c["experiment_type"] == "prediction_bias_calibration"]
        ram = next(c for c in candidates if c["subject"]["metric"] == "expected_peak_ram_mb")
        self.assertIsNone(ram["gate"])  # calibration opens nothing; it only needs the observation
        self.assertEqual(ram["subject"]["model_id"], "qwen3-30b-a3b-awq")
        self.assertIn("dissolves", ram["evidence_sought"])

    def test_low_confidence_known_good_proposes_earning_the_preference(self) -> None:
        omen = next(c for c in _fixture_candidates() if c["experiment_type"] == "prefer_validation"
                    and c["subject"]["builder_id"] == "omen-worker-1")
        self.assertIn("2 more clean run(s)", omen["worth"])
        self.assertEqual(omen["confidence"], "low")

    def test_uncertain_resolution_states_the_exact_evidence_needed(self) -> None:
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "success"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "timeout", "slow_load"),
        ]
        candidate = next(c for c in _candidates_for(observations, [])
                         if c["experiment_type"] == "uncertain_resolution")
        # (1+2)/(2+2) = 0.75 crosses the 0.7 known_good bar
        self.assertIn("2 consecutive success(es)", candidate["evidence_sought"])
        self.assertIn("known_bad is unreachable", candidate["evidence_sought"])
        self.assertEqual(candidate["gate"]["effect"], "exploratory_only")

    def test_same_model_on_two_backends_proposes_a_comparison(self) -> None:
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "success", backend="backend-z"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "success", backend="backend-w"),
        ]
        candidate = next(c for c in _candidates_for(observations, [])
                         if c["experiment_type"] == "backend_comparison")
        self.assertEqual(candidate["candidate_id"], "backend_comparison:model-y:backend-w+backend-z")
        self.assertIsNone(candidate["gate"])
        self.assertIsNone(candidate["target_finding_id"])  # comparing, not testing one belief

    def test_candidate_synthesis_is_deterministic(self) -> None:
        self.assertEqual(_fixture_candidates(), _fixture_candidates())

    def test_every_candidate_answers_all_five_questions(self) -> None:
        for candidate in _fixture_candidates():
            for field in ("question", "worth", "evidence_sought", "risk_accepted"):
                self.assertTrue(candidate[field], f"{candidate['candidate_id']} missing {field}")
            self.assertIn("gate", candidate)  # explicitly null when nothing opens


class ExperimentLoopTests(TestCase):
    """The first thin experiment: a known_bad/high block, opened only by experiment_flag, run,
    planned on the record, observed, and judged — blocked by default, open by experiment,
    measured by observation, judged by findings, re-gated by policy."""

    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_experiment(self) -> Path:
        run_reference_workflow(WORK_ITEM, self.temp_dir, "policy-experiment", policy_path=POLICY)
        return self.temp_dir / WORK_ITEM.stem

    def _plan(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "artifacts" / "experiments" / "exp_assign_001.json").read_text(encoding="utf-8"))

    def _materialize(self) -> dict:
        knowledge_dir = self.temp_dir / "knowledge"
        return materialize_experiments(collect_event_files([RUNS_FIXTURE_DIR, self.temp_dir]), knowledge_dir)

    def test_gated_dispatch_authors_the_experiment_plan(self) -> None:
        run_dir = self._run_experiment()
        plan = self._plan(run_dir)
        self.assertEqual(plan["experiment_type"], "known_bad_retest")
        self.assertEqual(plan["target_finding_id"], f"known_bad:{VLLM_COMBO}")
        self.assertEqual(plan["gate_opened"]["policy_id"], f"block:{VLLM_COMBO}|*|*")
        self.assertEqual(plan["gate_opened"]["flag"], "experiment_flag")
        self.assertEqual(plan["decision_id"], "dec_assign_001")  # plan -> decision -> observation
        self.assertIn("untested is not impossible", plan["reason"])
        events = read_events(run_dir / "events.jsonl")
        assigned = next(e for e in events if e["event_type"] == "builder.assigned")
        self.assertIn("experiment_plan", {ref["artifact_type"] for ref in assigned["artifact_refs"]})
        self.assertEqual(validate_file(run_dir / "events.jsonl"), [])

    def test_plan_conforms_to_contract(self) -> None:
        plan = self._plan(self._run_experiment())
        self.assertEqual(plan["contract_version"], "experiment-plan.v1")
        self.assertLessEqual(set(PLAN_SCHEMA["required"]), set(plan))
        self.assertLessEqual(set(plan), set(PLAN_SCHEMA["properties"]))
        self.assertIn(plan["experiment_type"], PLAN_SCHEMA["properties"]["experiment_type"]["enum"])
        self.assertLessEqual(set(plan["gate_opened"]), set(PLAN_SCHEMA["properties"]["gate_opened"]["properties"]))

    def test_ungated_dispatch_authors_no_plan(self) -> None:
        for scenario, policy in (("happy", None), ("policy-adjusted", POLICY)):
            run_reference_workflow(WORK_ITEM, self.temp_dir, scenario, policy_path=policy)
            run_dir = self.temp_dir / WORK_ITEM.stem
            self.assertFalse((run_dir / "artifacts" / "experiments").exists(), scenario)

    def test_result_links_decision_and_observation_and_judges_the_belief(self) -> None:
        self._run_experiment()
        results = self._materialize()["experiment_results.json"]["results"]
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["decision_id"], "dec_assign_001")
        self.assertEqual(result["observation_ids"], ["obs_001"])
        self.assertEqual(result["outcome"], "success")
        # the belief moved: 3/3 crashes said known_bad/high; one success says uncertain
        self.assertEqual(result["belief_before"]["finding_type"], "known_bad")
        self.assertEqual(result["belief_before"]["confidence"], "high")
        self.assertEqual(result["belief_after"]["finding_type"], "uncertain")
        self.assertTrue(result["belief_changed"])
        # and policy re-gated: block -> exploratory_only
        self.assertEqual(result["gate_before"]["effect"], "block")
        self.assertEqual(result["gate_after"]["effect"], "exploratory_only")
        self.assertIn("known_bad (high) -> uncertain", result["verdict"])
        self.assertIn("gate: block -> exploratory_only", result["verdict"])

    def test_result_conforms_to_contract(self) -> None:
        self._run_experiment()
        content = self._materialize()["experiment_results.json"]
        self.assertEqual(content["contract_version"], "experiment-results.v1")
        self.assertEqual(content["beliefs_changed"], 1)
        for result in content["results"]:
            self.assertEqual(result["contract_version"], "experiment-result.v1")
            self.assertLessEqual(set(RESULT_SCHEMA["required"]), set(result))
            self.assertLessEqual(set(result), set(RESULT_SCHEMA["properties"]))

    def test_candidates_regate_after_the_experiment(self) -> None:
        self._run_experiment()
        candidates = _by_id(self._materialize()["experiment_candidates.json"]["candidates"])
        # the retest resolved the block; the next proposed experiment is resolution, not retest
        self.assertNotIn(f"known_bad_retest:{VLLM_COMBO}", candidates)
        self.assertIn(f"uncertain_resolution:{VLLM_COMBO}", candidates)

    def test_plan_without_observation_reports_honestly(self) -> None:
        plan = {
            "contract_version": "experiment-plan.v1",
            "experiment_id": "exp_dangling",
            "experiment_type": "known_bad_retest",
            "workflow_id": "wf_x",
            "run_id": "run_x",
            "decision_id": "dec_x",
            "timestamp": "2026-07-02T15:00:00Z",
            "subject": {"builder_id": "b", "model_id": "m", "backend": "k", "task_kind": None, "metric": None},
            "target_finding_id": None,
            "derived_from_candidate": None,
            "gate_opened": None,
            "reason": "r",
            "evidence_sought": "e",
            "risk_accepted": "c",
        }
        result = synthesize_results([plan], [], [])[0]
        self.assertEqual(result["outcome"], "no_observation")
        self.assertEqual(result["observation_ids"], [])
        self.assertFalse(result["belief_changed"])
        self.assertIn("no observation", result["verdict"])
