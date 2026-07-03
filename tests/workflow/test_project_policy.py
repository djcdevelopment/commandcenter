from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_capacity import collect_event_files
from tools.workflow.project_findings import materialize_findings
from tools.workflow.project_policy import evaluate, materialize_policy, synthesize_policy


ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
POLICY_SCHEMA = json.loads((ROOT / "contracts" / "policy.v1.schema.json").read_text(encoding="utf-8"))


def _fixture_findings() -> list[dict]:
    temp_dir = Path(mkdtemp())
    try:
        content = materialize_findings(collect_event_files([RUNS_FIXTURE_DIR]), temp_dir)
        return content["findings"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _finding(finding_type: str, confidence: str, subject: dict | None = None, **evidence) -> dict:
    subject = {"builder_id": "builder-x", "model_id": "model-y", "backend": "backend-z",
               "task_kind": None, "metric": None, **(subject or {})}
    return {
        "contract_version": "finding.v1",
        "finding_id": f"{finding_type}:test",
        "finding_type": finding_type,
        "statement": "test finding",
        "subject": subject,
        "confidence": confidence,
        "confidence_score": 0.5,
        "evidence": {"samples": 2, "summary": "test evidence", **evidence},
        "recommendation": None,
        "first_observed": "2026-07-01T10:00:00Z",
        "last_observed": "2026-07-02T10:00:00Z",
        "derived_from": ["capacity-estimates.v1"],
    }


class SynthesizePolicyTests(TestCase):
    def test_high_confidence_known_bad_becomes_block_with_experiment_override(self) -> None:
        rules = synthesize_policy(_fixture_findings())
        block = next(rule for rule in rules if rule["effect"] == "block")
        self.assertEqual(block["subject"]["backend"], "vllm")
        self.assertEqual(block["override"]["flag"], "experiment_flag")
        self.assertEqual(block["derived_from_finding"], "known_bad:claudefarm1|qwen3-30b-a3b-awq|vllm")
        self.assertIn("3/3 moe_offload_crash", block["evidence_summary"])

    def test_prediction_bias_becomes_additive_adjustment(self) -> None:
        rules = synthesize_policy(_fixture_findings())
        adjust = next(rule for rule in rules if rule["effect"] == "adjust_prediction")
        self.assertEqual(adjust["adjustment"]["metric"], "expected_peak_ram_mb")
        self.assertGreater(adjust["adjustment"]["additive_correction"], 0)

    def test_low_confidence_known_good_earns_no_preference(self) -> None:
        rules = synthesize_policy(_fixture_findings())
        self.assertEqual([rule for rule in rules if rule["effect"] == "prefer"], [])

    def test_high_confidence_known_good_becomes_prefer(self) -> None:
        rules = synthesize_policy([_finding("known_good", "high")])
        self.assertEqual(rules[0]["effect"], "prefer")
        self.assertIsNone(rules[0]["override"])

    def test_sub_high_known_bad_gates_softly_as_exploratory(self) -> None:
        rules = synthesize_policy([_finding("known_bad", "medium")])
        self.assertEqual(rules[0]["effect"], "exploratory_only")

    def test_uncertain_becomes_exploratory_and_regression_becomes_quarantine(self) -> None:
        rules = synthesize_policy([_finding("uncertain", "low"), _finding("regression", "low")])
        self.assertEqual({rule["effect"] for rule in rules}, {"exploratory_only", "quarantine"})

    def test_recommendation_findings_stay_advisory(self) -> None:
        self.assertEqual(synthesize_policy([_finding("recommendation", "high")]), [])

    def test_rules_conform_to_contract(self) -> None:
        required = set(POLICY_SCHEMA["required"])
        allowed = set(POLICY_SCHEMA["properties"])
        effects = set(POLICY_SCHEMA["properties"]["effect"]["enum"])
        rules = synthesize_policy(_fixture_findings() + [
            _finding("known_good", "high"), _finding("regression", "low"),
            _finding("uncertain", "low"), _finding("known_bad", "medium"),
        ])
        self.assertGreater(len(rules), 3)
        for rule in rules:
            keys = set(rule)
            self.assertLessEqual(required, keys)
            self.assertLessEqual(keys, allowed)
            self.assertEqual(rule["contract_version"], "policy.v1")
            self.assertIn(rule["effect"], effects)
            if rule["effect"] == "adjust_prediction":
                self.assertIsNotNone(rule["adjustment"])

    def test_projection_is_deterministic(self) -> None:
        findings = _fixture_findings()
        self.assertEqual(synthesize_policy(findings), synthesize_policy(findings))


class EvaluateTests(TestCase):
    def setUp(self) -> None:
        self.rules = synthesize_policy(_fixture_findings())

    def test_known_bad_combo_is_blocked_without_experiment_flag(self) -> None:
        verdict = evaluate(self.rules, builder_id="claudefarm1", model_id="qwen3-30b-a3b-awq", backend="vllm")
        self.assertFalse(verdict["allowed"])
        self.assertTrue(verdict["requires_experiment_flag"])

    def test_experiment_flag_opens_the_gate(self) -> None:
        verdict = evaluate(self.rules, builder_id="claudefarm1", model_id="qwen3-30b-a3b-awq",
                           backend="vllm", experiment_flag=True)
        self.assertTrue(verdict["allowed"])
        self.assertTrue(verdict["requires_experiment_flag"])  # still reported: the dispatch is an experiment

    def test_unrelated_combo_is_untouched(self) -> None:
        verdict = evaluate(self.rules, builder_id="omen-worker-1", model_id="qwen3-coder:30b", backend="ollama")
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["matched_rules"], [])

    def test_bias_adjustment_reaches_the_scheduler(self) -> None:
        verdict = evaluate(self.rules, builder_id="claudefarm1", model_id="qwen3-30b-a3b-awq",
                           backend="vllm", experiment_flag=True)
        adjustments = {adj["metric"] for adj in verdict["prediction_adjustments"]}
        self.assertIn("expected_peak_ram_mb", adjustments)

    def test_every_dispatch_influence_is_explained(self) -> None:
        verdict = evaluate(self.rules, builder_id="claudefarm1", model_id="qwen3-30b-a3b-awq", backend="vllm")
        self.assertGreater(len(verdict["matched_rules"]), 0)
        for match in verdict["matched_rules"]:
            self.assertIn("statement", match)
            self.assertIn("policy_id", match)


class MaterializePolicyTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())
        self.knowledge_dir = self.temp_dir / "knowledge"
        materialize_findings(collect_event_files([RUNS_FIXTURE_DIR]), self.knowledge_dir)
        self.findings_path = self.knowledge_dir / "findings.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _audit_lines(self) -> list[dict]:
        audit_path = self.knowledge_dir / "policy_audit.ndjson"
        if not audit_path.is_file():
            return []
        return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]

    def test_first_materialization_writes_policy_and_audits_additions(self) -> None:
        result = materialize_policy(self.findings_path, self.knowledge_dir)
        content = json.loads((self.knowledge_dir / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(content["contract_version"], "policies.v1")
        self.assertEqual(content["rule_counts"]["block"], 1)
        audit = self._audit_lines()
        self.assertEqual(len(audit), len(result["content"]["rules"]))
        self.assertTrue(all(entry["action"] == "added" for entry in audit))
        self.assertTrue(all(entry["evidence_watermark"] for entry in audit))

    def test_reprojection_of_same_corpus_appends_no_audit(self) -> None:
        materialize_policy(self.findings_path, self.knowledge_dir)
        before = self._audit_lines()
        result = materialize_policy(self.findings_path, self.knowledge_dir)
        self.assertEqual(result["changes"], [])
        self.assertEqual(self._audit_lines(), before)

    def test_operator_override_suspends_but_keeps_rule_visible_and_audited(self) -> None:
        materialize_policy(self.findings_path, self.knowledge_dir)
        block_id = "block:claudefarm1|qwen3-30b-a3b-awq|vllm|*|*"
        (self.knowledge_dir / "policy_overrides.json").write_text(json.dumps({
            "contract_version": "policy-overrides.v1",
            "overrides": [{"policy_id": block_id, "action": "suspend",
                           "author": "derek", "reason": "triton rung experiment window"}],
        }), encoding="utf-8")
        result = materialize_policy(self.findings_path, self.knowledge_dir)

        block = next(rule for rule in result["content"]["rules"] if rule["policy_id"] == block_id)
        self.assertEqual(block["status"], "suspended")
        self.assertIn("derek", block["suspended_reason"])
        self.assertEqual([change["action"] for change in result["changes"]], ["changed"])

        verdict = evaluate(result["content"]["rules"], builder_id="claudefarm1",
                           model_id="qwen3-30b-a3b-awq", backend="vllm")
        self.assertTrue(verdict["allowed"])  # suspended rules stop gating...
        statuses = {match["policy_id"]: match["status"] for match in verdict["matched_rules"]}
        self.assertEqual(statuses[block_id], "suspended")  # ...but stay visible
