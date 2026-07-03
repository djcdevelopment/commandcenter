from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_capacity import collect_event_files, extract_observations, extract_scheduler_decisions
from tools.workflow.project_findings import materialize_findings, synthesize_findings
from tools.workflow.project_state import read_events


ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
FINDING_SCHEMA = json.loads((ROOT / "contracts" / "finding.v1.schema.json").read_text(encoding="utf-8"))


def _fixture_corpus() -> tuple[list[dict], list[dict]]:
    observations: list[dict] = []
    decisions: list[dict] = []
    for events_path in collect_event_files([RUNS_FIXTURE_DIR]):
        events = read_events(events_path)
        extracted_observations, unresolved_observations = extract_observations(events, events_path)
        extracted_decisions, unresolved_decisions = extract_scheduler_decisions(events, events_path)
        assert unresolved_observations == 0 and unresolved_decisions == 0
        observations.extend(extracted_observations)
        decisions.extend(extracted_decisions)
    return observations, decisions


def _fixture_findings() -> list[dict]:
    observations, decisions = _fixture_corpus()
    return synthesize_findings(observations, decisions)


def _by_type(findings: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        grouped.setdefault(finding["finding_type"], []).append(finding)
    return grouped


def _observation(observation_id: str, timestamp: str, outcome: str, failure_class: str | None = None) -> dict:
    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": observation_id,
        "decision_id": None,
        "workflow_id": "wf_test",
        "run_id": "run_test",
        "timestamp": timestamp,
        "builder_id": "builder-x",
        "model_id": "model-y",
        "backend": "backend-z",
        "workload_shape": {"task_kind": "build"},
        "observed": {"tokens_per_s": 40.0},
        "outcome": outcome,
        "failure_class": failure_class,
        "promotion_status": None,
    }


class ProjectFindingsTests(TestCase):
    def test_vllm_moe_combo_yields_high_confidence_known_bad(self) -> None:
        known_bad = _by_type(_fixture_findings())["known_bad"]
        vllm = next(f for f in known_bad if f["subject"]["backend"] == "vllm")
        self.assertEqual(vllm["subject"]["model_id"], "qwen3-30b-a3b-awq")
        self.assertEqual(vllm["confidence"], "high")
        self.assertEqual(vllm["evidence"]["summary"], "3/3 moe_offload_crash")
        self.assertEqual(vllm["recommendation"], "do not schedule without explicit experiment flag")
        self.assertEqual(vllm["evidence"]["observation_ids"], ["obs_301", "obs_302", "obs_303"])

    def test_omen_debut_yields_known_good_with_honest_low_confidence(self) -> None:
        known_good = _by_type(_fixture_findings())["known_good"]
        omen = next(f for f in known_good if f["subject"]["builder_id"] == "omen-worker-1")
        self.assertEqual(omen["confidence"], "low")  # one sample is one sample, however good it looked
        self.assertEqual(omen["evidence"]["successes"], 1)

    def test_vllm_ram_predictions_read_as_biased_low(self) -> None:
        bias = _by_type(_fixture_findings())["prediction_bias"]
        ram = next(f for f in bias if f["subject"]["model_id"] == "qwen3-30b-a3b-awq"
                   and f["subject"]["metric"] == "expected_peak_ram_mb")
        self.assertGreater(ram["evidence"]["mean_signed_error"], 0)  # observed > predicted: predictions run low
        self.assertIn("run low", ram["statement"])
        self.assertGreaterEqual(ram["evidence"]["samples"], 2)  # one vllm fixture decision carries no RAM prediction

    def test_recommendation_emitted_per_task_kind_with_known_good(self) -> None:
        recommendations = _by_type(_fixture_findings())["recommendation"]
        build = next(f for f in recommendations if f["subject"]["task_kind"] == "build")
        self.assertIn(build["subject"]["builder_id"], {"omen-worker-1", "omen-worker-2"})
        self.assertIsNotNone(build["recommendation"])

    def test_regression_detected_from_success_then_failure(self) -> None:
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "success"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "success"),
            _observation("obs_c", "2026-07-02T10:00:00Z", "oom_crash", "driver_update_crash"),
        ]
        findings = _by_type(synthesize_findings(observations, []))
        regression = findings["regression"][0]
        self.assertEqual(regression["subject"]["builder_id"], "builder-x")
        self.assertEqual(regression["evidence"]["observation_ids"], ["obs_c"])
        self.assertIn("driver_update_crash", regression["statement"])
        # 2/3 success (0.67) misses the known_good bar, so the combo reads uncertain AND regressed.
        self.assertIn("uncertain", findings)

    def test_single_failure_is_uncertain_not_known_bad(self) -> None:
        observations = [_observation("obs_a", "2026-07-01T10:00:00Z", "timeout", "slow_load")]
        findings = _by_type(synthesize_findings(observations, []))
        self.assertNotIn("known_bad", findings)
        uncertain = findings["uncertain"][0]
        self.assertEqual(uncertain["confidence"], "low")
        self.assertEqual(uncertain["recommendation"], "schedule only low-stakes work; gather more evidence")

    def test_findings_conform_to_contract(self) -> None:
        required = set(FINDING_SCHEMA["required"])
        allowed = set(FINDING_SCHEMA["properties"])
        finding_types = set(FINDING_SCHEMA["properties"]["finding_type"]["enum"])
        confidences = set(FINDING_SCHEMA["properties"]["confidence"]["enum"])
        subject_allowed = set(FINDING_SCHEMA["properties"]["subject"]["properties"])
        evidence_allowed = set(FINDING_SCHEMA["properties"]["evidence"]["properties"])
        evidence_required = set(FINDING_SCHEMA["properties"]["evidence"]["required"])

        findings = _fixture_findings()
        self.assertGreater(len(findings), 0)
        for finding in findings:
            keys = set(finding)
            self.assertLessEqual(required, keys)
            self.assertLessEqual(keys, allowed)
            self.assertEqual(finding["contract_version"], "finding.v1")
            self.assertIn(finding["finding_type"], finding_types)
            self.assertIn(finding["confidence"], confidences)
            self.assertTrue(0 <= finding["confidence_score"] < 1)
            self.assertLessEqual(set(finding["subject"]), subject_allowed)
            self.assertLessEqual(evidence_required, set(finding["evidence"]))
            self.assertLessEqual(set(finding["evidence"]), evidence_allowed)

    def test_projection_is_deterministic(self) -> None:
        observations, decisions = _fixture_corpus()
        self.assertEqual(synthesize_findings(observations, decisions), synthesize_findings(observations, decisions))

    def test_materialize_writes_findings_file(self) -> None:
        temp_dir = Path(mkdtemp())
        try:
            knowledge_dir = temp_dir / "knowledge"
            materialize_findings(collect_event_files([RUNS_FIXTURE_DIR]), knowledge_dir)
            content = json.loads((knowledge_dir / "findings.json").read_text(encoding="utf-8"))
            self.assertEqual(content["contract_version"], "findings.v1")
            self.assertEqual(content["observation_count"], 6)
            self.assertEqual(content["unresolved_refs"], 0)
            self.assertEqual(content["finding_counts"]["known_bad"], 1)
            self.assertEqual(len(content["findings"]), sum(content["finding_counts"].values()))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_empty_corpus_yields_empty_findings_not_error(self) -> None:
        self.assertEqual(synthesize_findings([], []), [])
