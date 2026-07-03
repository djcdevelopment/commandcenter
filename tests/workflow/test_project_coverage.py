from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_associations import synthesize_associations, synthesize_capabilities
from tools.workflow.project_capacity import collect_event_files, extract_observations, extract_scheduler_decisions
from tools.workflow.project_coverage import materialize_coverage, synthesize_coverage
from tools.workflow.project_experiments import synthesize_candidates
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_policy import synthesize_policy
from tools.workflow.project_state import read_events

ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
COVERAGE_SCHEMA = json.loads((ROOT / "contracts" / "coverage.v1.schema.json").read_text(encoding="utf-8"))

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


def _gaps(observations: list[dict], decisions: list[dict]) -> list[dict]:
    findings = synthesize_findings(observations, decisions)
    associations = synthesize_associations(observations, findings)
    capabilities = synthesize_capabilities(associations, findings, observations)
    return synthesize_coverage(observations, decisions, capabilities)


def _by_id(gaps: list[dict]) -> dict[str, dict]:
    return {gap["gap_id"]: gap for gap in gaps}


def _stale_corpus() -> list[dict]:
    def obs(observation_id: str, timestamp: str, workflow_id: str, builder_id: str,
            model_id: str, backend: str, task_kind: str = "build") -> dict:
        return {
            "contract_version": "capacity-observation.v1",
            "observation_id": observation_id,
            "decision_id": None,
            "workflow_id": workflow_id,
            "run_id": f"run_{observation_id}",
            "timestamp": timestamp,
            "builder_id": builder_id,
            "model_id": model_id,
            "backend": backend,
            "workload_shape": {"task_kind": task_kind, "requires_gpu": True},
            "observed": {"tokens_per_s": 50.0, "ttft_s": 1.0, "ram_gb_peak": 8.0,
                         "vram_gb_peak": 10.0, "context_tokens": 8192},
            "outcome": "success",
            "failure_class": None,
            "promotion_status": None,
        }
    return [
        obs("obs_a", "2026-06-01T10:00:00Z", "wf_1", "builder-1", "model-1", "ollama"),
        obs("obs_b", "2026-06-02T10:00:00Z", "wf_2", "builder-2", "model-2", "ollama"),
        obs("obs_c", "2026-06-20T10:00:00Z", "wf_3", "builder-9", "model-9", "local",
            task_kind="other"),
    ]


class CoverageTests(TestCase):
    def test_considered_but_never_observed_is_a_gap(self) -> None:
        observations, decisions = _fixture_corpus()
        decisions = decisions + [{
            "decision_id": "dec_ghost",
            "candidates_considered": [
                {"builder_id": "ghost-1", "model_id": "phantom-7b", "backend": "vllm", "selected": False},
            ],
        }]
        gap = _by_id(_gaps(observations, decisions))["unobserved_combo:ghost-1|phantom-7b|vllm"]
        self.assertEqual(gap["evidence"]["samples"], 0)
        self.assertIn("unknown, not bad", gap["statement"])
        self.assertEqual(gap["proposed_experiment_type"], "coverage_probe")

    def test_single_workflow_evidence_is_a_gap_that_names_the_missing_workflow(self) -> None:
        gaps = _by_id(_gaps(*_fixture_corpus()))
        gap = gaps["single_workflow_evidence:task_backend:task_kind=serve|backend=vllm"]
        self.assertEqual(gap["evidence"]["samples"], 3)
        self.assertEqual(gap["evidence"]["workflows"], ["wf_vllm_probe"])
        self.assertIn("second distinct workflow", gap["statement"])

    def test_unmeasured_metrics_are_a_calibration_blind_spot(self) -> None:
        gaps = _by_id(_gaps(*_fixture_corpus()))
        gap = gaps[f"unmeasured_metrics:{VLLM_COMBO}"]
        self.assertEqual(gap["subject"]["missing_metrics"], ["ttft_s", "tokens_per_s", "vram_gb_peak"])
        self.assertIn("never be calibrated", gap["statement"])

    def test_stale_capability_appears_as_a_coverage_view(self) -> None:
        gaps = _gaps(_stale_corpus(), [])
        stale = next(gap for gap in gaps if gap["gap_type"] == "stale_capability")
        self.assertEqual(stale["subject"]["capability_id"], "capability:task_kind=build|backend=ollama")
        self.assertEqual(stale["proposed_experiment_type"], "qualification_run")

    def test_gaps_conform_to_contract_and_are_deterministic(self) -> None:
        observations, decisions = _fixture_corpus()
        gaps = _gaps(observations, decisions)
        self.assertEqual(gaps, _gaps(observations, decisions))
        for gap in gaps:
            self.assertEqual(gap["contract_version"], "coverage-gap.v1")
            self.assertLessEqual(set(COVERAGE_SCHEMA["required"]), set(gap))
            self.assertLessEqual(set(gap), set(COVERAGE_SCHEMA["properties"]))
            self.assertLessEqual(set(gap["subject"]), set(COVERAGE_SCHEMA["properties"]["subject"]["properties"]))

    def test_materialize_writes_coverage_json(self) -> None:
        knowledge_dir = Path(mkdtemp())
        try:
            content = materialize_coverage(collect_event_files([RUNS_FIXTURE_DIR]), knowledge_dir)
            self.assertTrue((knowledge_dir / "coverage.json").is_file())
            self.assertGreaterEqual(content["gap_counts"]["single_workflow_evidence"], 1)
            first = (knowledge_dir / "coverage.json").read_bytes()
            materialize_coverage(collect_event_files([RUNS_FIXTURE_DIR]), knowledge_dir)
            self.assertEqual((knowledge_dir / "coverage.json").read_bytes(), first)
        finally:
            shutil.rmtree(knowledge_dir, ignore_errors=True)


class ExperimentDemandTests(TestCase):
    """Capabilities and coverage feed the same candidate funnel the findings do: decay and blind
    spots become proposals, never silent state."""

    def _candidates(self, observations: list[dict], decisions: list[dict]) -> dict[str, dict]:
        findings = synthesize_findings(observations, decisions)
        rules = synthesize_policy(findings)
        associations = synthesize_associations(observations, findings)
        capabilities = synthesize_capabilities(associations, findings, observations)
        gaps = synthesize_coverage(observations, decisions, capabilities)
        return {c["candidate_id"]: c for c in synthesize_candidates(findings, rules, capabilities, gaps)}

    def test_stale_capability_proposes_its_own_qualification_run(self) -> None:
        candidates = self._candidates(_stale_corpus(), [])
        candidate = candidates["qualification_run:capability:task_kind=build|backend=ollama"]
        self.assertEqual(candidate["target_capability_id"], "capability:task_kind=build|backend=ollama")
        self.assertIsNone(candidate["subject"]["builder_id"])  # any qualified resource may run it
        self.assertEqual(candidate["subject"]["task_kind"], "build")
        self.assertIn("18.0 evidence-day(s)", candidate["question"])
        self.assertIn("renews last_validated", candidate["evidence_sought"])

    def test_fresh_capability_proposes_no_qualification_run(self) -> None:
        candidates = self._candidates(*_fixture_corpus())
        self.assertFalse(any(c.startswith("qualification_run:") for c in candidates))

    def test_qualification_renewal_withdraws_the_demand(self) -> None:
        renewed = _stale_corpus() + [{
            **_stale_corpus()[0],
            "observation_id": "obs_d",
            "run_id": "run_obs_d",
            "timestamp": "2026-06-19T10:00:00Z",
            "workflow_id": "wf_4",
        }]
        candidates = self._candidates(renewed, [])
        self.assertFalse(any(c.startswith("qualification_run:") for c in candidates))

    def test_coverage_gaps_become_probe_candidates_but_stale_capabilities_do_not_duplicate(self) -> None:
        candidates = self._candidates(_stale_corpus(), [])
        probes = [c for c in candidates.values() if c["experiment_type"] == "coverage_probe"]
        self.assertTrue(probes)
        # the stale capability already produced a qualification_run; no coverage_probe doubles it
        self.assertFalse(any("stale_capability" in candidate_id for candidate_id in candidates))

    def test_single_workflow_gap_probe_asks_for_the_second_workflow(self) -> None:
        candidates = self._candidates(*_fixture_corpus())
        probe = candidates["coverage_probe:single_workflow_evidence:task_backend:task_kind=serve|backend=vllm"]
        self.assertIn("hold outside wf_vllm_probe", probe["question"])
        self.assertIn("will not generalize from one workflow", probe["worth"])
        self.assertIn("second distinct workflow", probe["evidence_sought"])
