from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_associations import (
    analyze_buckets,
    materialize_associations,
    synthesize_associations,
    synthesize_capabilities,
)
from tools.workflow.project_capacity import collect_event_files, extract_observations, extract_scheduler_decisions
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_state import read_events

ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
ASSOCIATION_SCHEMA = json.loads((ROOT / "contracts" / "association.v1.schema.json").read_text(encoding="utf-8"))
CAPABILITY_SCHEMA = json.loads((ROOT / "contracts" / "capability.v1.schema.json").read_text(encoding="utf-8"))

BUILD_OLLAMA_ASSOCIATION = "success_invariant:task_backend:task_kind=build|backend=ollama"
BUILD_OLLAMA_CAPABILITY = "capability:task_kind=build|backend=ollama"


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


def _derive(observations: list[dict], decisions: list[dict]) -> tuple[list[dict], list[dict]]:
    findings = synthesize_findings(observations, decisions)
    associations = synthesize_associations(observations, findings)
    return associations, synthesize_capabilities(associations, findings, observations)


def _observation(observation_id: str, timestamp: str, workflow_id: str, builder_id: str,
                 model_id: str, backend: str, task_kind: str = "build", outcome: str = "success",
                 failure_class: str | None = None, context_tokens: int | None = 8192) -> dict:
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
        "observed": {"tokens_per_s": 50.0, "context_tokens": context_tokens},
        "outcome": outcome,
        "failure_class": failure_class,
        "promotion_status": None,
    }


class AssociationEngineTests(TestCase):
    def test_cross_workflow_convergence_forms_the_association(self) -> None:
        associations, _ = _derive(*_fixture_corpus())
        by_id = {a["association_id"]: a for a in associations}
        association = by_id[BUILD_OLLAMA_ASSOCIATION]
        # two workflows, two builders, two models: the invariant is task_kind+backend
        self.assertEqual(association["workflows"], ["wf_omen_debut", "wf_omen_followup"])
        self.assertEqual(association["varied"]["builder_id"], ["omen-worker-1", "omen-worker-2"])
        self.assertEqual(association["varied"]["model_id"], ["qwen3-coder:14b", "qwen3-coder:30b"])
        self.assertEqual(association["confidence"], "medium")  # keyed to workflow count, not samples
        self.assertIn("independent of the particulars", association["statement"])

    def test_no_generalization_from_one_workflow_however_many_samples(self) -> None:
        # the vLLM MoE crash has 3 samples — all from wf_vllm_probe. It stays a finding.
        associations, _ = _derive(*_fixture_corpus())
        self.assertFalse(any(a["invariant"].get("backend") == "vllm" for a in associations))
        gated = [b for b in analyze_buckets(_fixture_corpus()[0])
                 if b["invariant"].get("backend") == "vllm" and not b["generalizes"]]
        self.assertTrue(gated)
        self.assertTrue(any("one workflow" in reason for bucket in gated
                            for reason in bucket["gated_reasons"]))

    def test_more_samples_from_the_same_workflow_never_open_the_gate(self) -> None:
        observations = [
            _observation(f"obs_{i}", f"2026-07-01T1{i}:00:00Z", "wf_only", f"builder-{i}", f"model-{i}", "ollama")
            for i in range(5)
        ]
        associations, _ = _derive(observations, [])
        self.assertEqual(associations, [])

    def test_repeated_particular_is_a_finding_not_an_abstraction(self) -> None:
        # two workflows but nothing varied: same builder, model, backend every time
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "wf_1", "builder-x", "model-y", "ollama"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "wf_2", "builder-x", "model-y", "ollama"),
        ]
        associations, _ = _derive(observations, [])
        self.assertEqual(associations, [])
        bucket = next(b for b in analyze_buckets(observations) if b["pattern"] == "task_backend")
        self.assertIn("repeated particular", bucket["gated_reasons"][0])

    def test_mixed_outcomes_stay_at_the_findings_layer(self) -> None:
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "wf_1", "builder-1", "model-1", "ollama"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "wf_2", "builder-2", "model-2", "ollama",
                         outcome="oom_crash", failure_class="oom"),
        ]
        associations, _ = _derive(observations, [])
        self.assertFalse(any(a["pattern"] == "task_backend" for a in associations))

    def test_failure_invariant_generalizes_a_shared_failure_class(self) -> None:
        observations = [
            _observation("obs_a", "2026-07-01T10:00:00Z", "wf_1", "builder-1", "model-1", "vllm",
                         task_kind="serve", outcome="oom_crash", failure_class="moe_offload_crash"),
            _observation("obs_b", "2026-07-01T11:00:00Z", "wf_2", "builder-2", "model-2", "vllm",
                         task_kind="serve", outcome="oom_crash", failure_class="moe_offload_crash"),
        ]
        associations, capabilities = _derive(observations, [])
        failure = next(a for a in associations if a["association_type"] == "failure_invariant"
                       and a["pattern"] == "failure_signature")
        self.assertEqual(failure["invariant"], {"failure_class": "moe_offload_crash", "backend": "vllm"})
        self.assertEqual(failure["evidence"]["failure_class"], "moe_offload_crash")
        # a failure invariant is organizational knowledge but never a capability
        self.assertFalse(any(c["derived_from_association"] == failure["association_id"] for c in capabilities))

    def test_associations_conform_to_contract_and_carry_provenance(self) -> None:
        observations, decisions = _fixture_corpus()
        findings = synthesize_findings(observations, decisions)
        associations = synthesize_associations(observations, findings)
        finding_ids = {finding["finding_id"] for finding in findings}
        for association in associations:
            self.assertEqual(association["contract_version"], "association.v1")
            self.assertLessEqual(set(ASSOCIATION_SCHEMA["required"]), set(association))
            self.assertLessEqual(set(association), set(ASSOCIATION_SCHEMA["properties"]))
            self.assertGreaterEqual(len(association["workflows"]), 2)
            for finding_id in association["supporting_findings"]:
                self.assertIn(finding_id, finding_ids)  # provenance resolves into the findings layer

    def test_derivation_is_deterministic(self) -> None:
        observations, decisions = _fixture_corpus()
        self.assertEqual(_derive(observations, decisions), _derive(observations, decisions))


class CapabilityTests(TestCase):
    def test_capability_is_the_associations_output_schema(self) -> None:
        _, capabilities = _derive(*_fixture_corpus())
        capability = next(c for c in capabilities if c["capability_id"] == BUILD_OLLAMA_CAPABILITY)
        self.assertEqual(capability["derived_from_association"], BUILD_OLLAMA_ASSOCIATION)
        self.assertEqual(
            [f"{r['builder_id']}|{r['model_id']}" for r in capability["qualified_resources"]],
            ["omen-worker-1|qwen3-coder:30b", "omen-worker-2|qwen3-coder:14b"],
        )
        self.assertEqual(capability["supporting_findings"], [
            "known_good:omen-worker-1|qwen3-coder:30b|ollama",
            "known_good:omen-worker-2|qwen3-coder:14b|ollama",
        ])

    def test_operating_envelope_is_what_was_measured(self) -> None:
        _, capabilities = _derive(*_fixture_corpus())
        envelope = next(c for c in capabilities
                        if c["capability_id"] == BUILD_OLLAMA_CAPABILITY)["operating_envelope"]
        self.assertEqual(envelope["max_context_tokens"], 16384)
        self.assertEqual(envelope["tokens_per_s_range"], [54.6, 61.0])
        self.assertEqual(envelope["max_vram_gb_peak"], 11.2)
        self.assertTrue(envelope["requires_gpu"])

    def test_qualification_is_computed_against_the_evidence_watermark(self) -> None:
        _, capabilities = _derive(*_fixture_corpus())
        capability = next(c for c in capabilities if c["capability_id"] == BUILD_OLLAMA_CAPABILITY)
        # newest corpus fact is obs_401 (2026-07-02); ollama evidence ends 2026-07-01 — fresh
        self.assertEqual(capability["evidence_watermark"], "2026-07-02T13:03:20Z")
        self.assertEqual(capability["last_validated"], "2026-07-01T21:15:30Z")
        self.assertEqual(capability["staleness_days"], 0.66)
        self.assertEqual(capability["qualification_status"], "qualified")

    def test_qualification_decays_and_renews_with_evidence_only(self) -> None:
        stale = [
            _observation("obs_a", "2026-06-01T10:00:00Z", "wf_1", "builder-1", "model-1", "ollama"),
            _observation("obs_b", "2026-06-02T10:00:00Z", "wf_2", "builder-2", "model-2", "ollama"),
            # unrelated newer fact pushes the watermark 18 days past the capability's evidence
            _observation("obs_c", "2026-06-20T10:00:00Z", "wf_3", "builder-9", "model-9", "local",
                         task_kind="other"),
        ]
        _, capabilities = _derive(stale, [])
        capability = next(c for c in capabilities if c["invariant"].get("backend") == "ollama")
        self.assertEqual(capability["qualification_status"], "requalification_due")
        self.assertEqual(capability["staleness_days"], 18.0)

        # renewal is evidence, not an edit: one fresh success re-derives the capability as qualified
        renewed = stale + [
            _observation("obs_d", "2026-06-19T10:00:00Z", "wf_4", "builder-1", "model-1", "ollama"),
        ]
        _, capabilities = _derive(renewed, [])
        capability = next(c for c in capabilities if c["invariant"].get("backend") == "ollama")
        self.assertEqual(capability["qualification_status"], "qualified")
        self.assertEqual(capability["staleness_days"], 1.0)

    def test_capabilities_conform_to_contract(self) -> None:
        _, capabilities = _derive(*_fixture_corpus())
        self.assertTrue(capabilities)
        for capability in capabilities:
            self.assertEqual(capability["contract_version"], "capability.v1")
            self.assertLessEqual(set(CAPABILITY_SCHEMA["required"]), set(capability))
            self.assertLessEqual(set(capability), set(CAPABILITY_SCHEMA["properties"]))
            self.assertIn("task_kind", capability["invariant"])


class MaterializeTests(TestCase):
    def setUp(self) -> None:
        self.knowledge_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.knowledge_dir, ignore_errors=True)

    def _materialize(self) -> dict:
        return materialize_associations(collect_event_files([RUNS_FIXTURE_DIR]), self.knowledge_dir)

    def test_materialize_writes_both_projections(self) -> None:
        outputs = self._materialize()
        self.assertEqual(outputs["associations.json"]["association_count"], 1)
        self.assertGreaterEqual(outputs["associations.json"]["gated_bucket_count"], 1)
        self.assertEqual(outputs["capabilities.json"]["capability_count"], 1)
        for file_name in ("associations.json", "capabilities.json"):
            self.assertTrue((self.knowledge_dir / file_name).is_file())

    def test_gated_buckets_are_reported_not_discarded(self) -> None:
        gated = self._materialize()["associations.json"]["gated_buckets"]
        vllm = next(bucket for bucket in gated if bucket["invariant"].get("backend") == "vllm"
                    and bucket["pattern"] == "task_backend")
        self.assertEqual(vllm["samples"], 3)
        self.assertEqual(vllm["workflows"], ["wf_vllm_probe"])

    def test_re_projection_is_diff_clean(self) -> None:
        self._materialize()
        first = {name: (self.knowledge_dir / name).read_bytes()
                 for name in ("associations.json", "capabilities.json")}
        self._materialize()
        for name, content in first.items():
            self.assertEqual((self.knowledge_dir / name).read_bytes(), content)

    def test_hand_authored_qualification_cannot_survive_projection(self) -> None:
        """The rot test: an operator hand-edits qualification_status; the next projection
        overwrites it. No organizational truth may be authored if it can be derived."""
        self._materialize()
        capabilities_path = self.knowledge_dir / "capabilities.json"
        content = json.loads(capabilities_path.read_text(encoding="utf-8"))
        content["capabilities"][0]["qualification_status"] = "requalification_due"
        content["capabilities"][0]["confidence"] = "high"
        capabilities_path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")

        self._materialize()
        rederived = json.loads(capabilities_path.read_text(encoding="utf-8"))["capabilities"][0]
        self.assertEqual(rederived["qualification_status"], "qualified")
        self.assertEqual(rederived["confidence"], "medium")
