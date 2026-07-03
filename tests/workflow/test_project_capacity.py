from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_capacity import (
    classify_known_bad,
    classify_known_good,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
    materialize_knowledge,
    reduce_prediction_comparisons,
    reduce_capacity,
    summarize_prediction_accuracy,
)
from tools.workflow.project_state import read_events


ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"


def _all_observations() -> list[dict]:
    observations: list[dict] = []
    for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
        extracted, unresolved = extract_observations(read_events(events_path), events_path)
        assert unresolved == 0, f"unresolved observation refs in fixture {events_path}"
        observations.extend(extracted)
    return observations


class ProjectCapacityTests(TestCase):
    def test_extracts_observation_artifacts_from_run_fixtures(self) -> None:
        observations = _all_observations()
        self.assertEqual(len(observations), 6)

    def test_extracts_scheduler_decision_artifacts_from_run_fixtures(self) -> None:
        decisions = []
        for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
            extracted, unresolved = extract_scheduler_decisions(read_events(events_path), events_path)
            self.assertEqual(unresolved, 0)
            decisions.extend(extracted)
        self.assertEqual(len(decisions), 6)

    def test_omen_ollama_combo_is_known_good(self) -> None:
        estimates = reduce_capacity(_all_observations())
        known_good = classify_known_good(estimates)
        combos = {(entry["builder_id"], entry["model_id"], entry["backend"]) for entry in known_good}
        self.assertIn(("omen-worker-1", "qwen3-coder:30b", "ollama"), combos)
        omen = next(entry for entry in known_good if entry["builder_id"] == "omen-worker-1")
        self.assertEqual(omen["mean_tokens_per_s"], 54.6)
        self.assertEqual(omen["success_rate"], 1.0)

    def test_vllm_awq_moe_combo_is_known_bad(self) -> None:
        estimates = reduce_capacity(_all_observations())
        known_bad = classify_known_bad(estimates)
        combos = {(entry["builder_id"], entry["model_id"], entry["backend"]) for entry in known_bad}
        self.assertIn(("claudefarm1", "qwen3-30b-a3b-awq", "vllm"), combos)
        vllm = next(entry for entry in known_bad if entry["backend"] == "vllm")
        self.assertEqual(vllm["failures"], 3)
        self.assertEqual(vllm["dominant_failure_class"], "moe_offload_crash")

    def test_promotion_hold_is_counted_not_a_failure(self) -> None:
        estimates = reduce_capacity(_all_observations())
        held_combo = estimates["builder-2|claude-opus-4.8|local"]
        self.assertEqual(held_combo["promotion_holds"], 1)
        self.assertEqual(held_combo["failures"], 0)

    def test_confidence_grows_with_samples(self) -> None:
        estimates = reduce_capacity(_all_observations())
        single_sample = estimates["omen-worker-1|qwen3-coder:30b|ollama"]
        triple_sample = estimates["claudefarm1|qwen3-30b-a3b-awq|vllm"]
        self.assertLess(single_sample["confidence"], triple_sample["confidence"])

    def test_unresolved_refs_are_counted_not_silent(self) -> None:
        events_path = ROOT / "fixtures" / "workflow" / "happy-path.events.jsonl"
        observations, unresolved = extract_observations(read_events(events_path), events_path)
        self.assertEqual(observations, [])
        self.assertEqual(unresolved, 1)

    def test_materialize_knowledge_writes_three_files(self) -> None:
        temp_dir = Path(mkdtemp())
        try:
            knowledge_dir = temp_dir / "knowledge"
            materialize_knowledge(collect_event_files([RUNS_FIXTURE_DIR]), knowledge_dir)
            estimates = json.loads((knowledge_dir / "capacity_estimates.json").read_text(encoding="utf-8"))
            known_good = json.loads((knowledge_dir / "known_good_models.json").read_text(encoding="utf-8"))
            known_bad = json.loads((knowledge_dir / "known_bad_models.json").read_text(encoding="utf-8"))
            prediction_accuracy = json.loads((knowledge_dir / "prediction_accuracy.json").read_text(encoding="utf-8"))
            self.assertEqual(estimates["contract_version"], "capacity-estimates.v1")
            self.assertEqual(estimates["observation_count"], 6)
            self.assertEqual(estimates["unresolved_observation_refs"], 0)
            self.assertGreaterEqual(len(known_good["entries"]), 1)
            self.assertEqual(len(known_bad["entries"]), 1)
            self.assertEqual(prediction_accuracy["contract_version"], "prediction-accuracy.v1")
            self.assertEqual(prediction_accuracy["comparison_count"], 6)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prediction_comparisons_cover_expected_fixture_cases(self) -> None:
        decisions = []
        observations = []
        for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
            extracted_decisions, unresolved_decisions = extract_scheduler_decisions(read_events(events_path), events_path)
            extracted_observations, unresolved_observations = extract_observations(read_events(events_path), events_path)
            self.assertEqual(unresolved_decisions, 0)
            self.assertEqual(unresolved_observations, 0)
            decisions.extend(extracted_decisions)
            observations.extend(extracted_observations)

        comparisons = reduce_prediction_comparisons(decisions, observations)
        by_run = {comparison["run_id"]: comparison for comparison in comparisons}

        self.assertTrue(by_run["run_omen_001"]["outcomes"]["promotion"]["matched"])
        self.assertFalse(by_run["run_hold_001"]["outcomes"]["promotion"]["matched"])
        self.assertIsNone(by_run["run_vllm_003"]["metrics"]["expected_generation_tokens_per_second"])
        self.assertGreater(by_run["run_omen_002"]["metrics"]["expected_generation_tokens_per_second"]["error"], 0)
        self.assertGreater(by_run["run_vllm_001"]["metrics"]["expected_peak_ram_mb"]["error"], 0)

    def test_prediction_summary_is_deterministic(self) -> None:
        decisions = []
        observations = []
        for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
            extracted_decisions, _ = extract_scheduler_decisions(read_events(events_path), events_path)
            extracted_observations, _ = extract_observations(read_events(events_path), events_path)
            decisions.extend(extracted_decisions)
            observations.extend(extracted_observations)

        comparisons = reduce_prediction_comparisons(decisions, observations)
        summary1 = summarize_prediction_accuracy(comparisons)
        summary2 = summarize_prediction_accuracy(comparisons)
        self.assertEqual(summary1, summary2)

    def test_prediction_reducer_tolerates_missing_observations(self) -> None:
        decisions = []
        for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
            extracted_decisions, unresolved = extract_scheduler_decisions(read_events(events_path), events_path)
            self.assertEqual(unresolved, 0)
            decisions.extend(extracted_decisions)

        comparisons = reduce_prediction_comparisons(decisions, [])
        summary = summarize_prediction_accuracy(comparisons)

        self.assertEqual(comparisons, [])
        self.assertEqual(summary["overall"]["overall"]["sample_count"], 0)
