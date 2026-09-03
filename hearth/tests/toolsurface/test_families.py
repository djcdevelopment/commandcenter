"""Task-family preference, authored with evidence (P5, ADR-0045).

Binds hearth/etc/routing-families.toml to the assay it claims to cover
(campaign/qwen38/assay/tasks.json) and to the pool that must serve its models
(hearth/etc/backends.toml), and pins the depth semantics the scheduler and the
rotation provider will lean on. Recommendation only: nothing here dispatches.
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from hearth.scheduler.families import (
    ASSAY_FAMILIES,
    CONTRACT,
    DEFAULT_PATH,
    Families,
    FamiliesConfigError,
    job_prompt_tokens,
    load_families,
    recommend,
    resolve_required_model,
)
from hearth.scheduler.ontology import Job
from hearth.toolsurface.backends import Backend, Pool, load_pool

REPO = Path(__file__).resolve().parents[3]
ASSAY_TASKS = REPO / "campaign" / "qwen38" / "assay" / "tasks.json"

R10_EVIDENCE = "R10: 27B verbatim recall 11/14 @8k, 2/2 @32k vs 30B-A3B 3/14, 0/2"
ADR0039_EVIDENCE = "ADR-0039: 0.687x jobs/hour @512, 2.63x @8K, 5.49x @32K"
VISION_REASON = "no verified local vision rung (27B text-only, ADR-0039)"
TEXT_FAMILIES = ("summarization", "extraction", "classification", "drafting",
                 "reasoning_planning", "tool_execution")
VISION_FAMILIES = ("document_ocr", "chart_diagram", "screenshot_grounded")


def _assay_families() -> list[str]:
    with open(ASSAY_TASKS, encoding="utf-8") as fh:
        doc = json.load(fh)
    seen: list[str] = []
    for task in doc["tasks"]:
        if task["family"] not in seen:
            seen.append(task["family"])
    return seen


def _fake_pool() -> Pool:
    """A pool shaped like the real one: 27B pin-only, 30B tagged default, gemini tagged."""
    return Pool(
        default="omen-arc",
        backends=(
            Backend(name="omen-arc", endpoint="http://127.0.0.1:8082", api="openai",
                    models=("qwen3-30b-a3b",), tags=("default", "code")),
            Backend(name="omen-arc-27b", endpoint="http://127.0.0.1:8084", api="openai",
                    models=("qwen38-27b",), tags=()),
            Backend(name="omen-swap", endpoint="http://127.0.0.1:8081", api="openai",
                    models=("phi4-vk1", "qwen38-27b-dual"), tags=()),
            Backend(name="dead-rung", endpoint="http://127.0.0.1:1", api="openai",
                    models=("qwen3-30b-a3b",), tags=("default",), retired=True),
            Backend(name="gcp-gemini", endpoint="https://aiplatform.googleapis.com",
                    api="gemini", models=("gemini-3.5-flash",), tags=("frontier",)),
        ),
    )


class DeclarationTests(TestCase):
    """The committed TOML covers the assay and cites evidence everywhere."""

    def setUp(self) -> None:
        self.families = load_families()

    def test_contract_and_source(self) -> None:
        self.assertEqual(self.families.contract, CONTRACT)
        self.assertEqual(self.families.source, DEFAULT_PATH)
        self.assertEqual(DEFAULT_PATH.name, "routing-families.toml")

    def test_every_assay_family_is_declared(self) -> None:
        assay = _assay_families()
        self.assertEqual(len(assay), 9, "the assay is nine equally weighted families")
        self.assertEqual(tuple(assay), ASSAY_FAMILIES)
        for family in assay:
            self.assertIn(family, self.families.names(), family)

    def test_quote_retrieval_and_default_are_declared(self) -> None:
        self.assertIn("quote_retrieval", self.families.names())
        self.assertIn("default", self.families.names())
        self.assertEqual(len(self.families.names()), 11)

    def test_evidence_is_non_empty_everywhere(self) -> None:
        for name, pref in self.families.families.items():
            self.assertTrue(pref.evidence.strip(), f"{name}: evidence")
            self.assertTrue(pref.reason.strip(), f"{name}: reason")
            self.assertTrue(pref.model_id.strip(), f"{name}: model_id")
            if pref.depth_override is not None:
                self.assertTrue(pref.depth_override.evidence.strip(),
                                f"{name}.depth_override: evidence")

    def test_quote_retrieval_cites_r10_with_its_receipt(self) -> None:
        pref = self.families.get("quote_retrieval")
        self.assertEqual(pref.model_id, "qwen38-27b")
        self.assertEqual(pref.evidence, R10_EVIDENCE)
        self.assertEqual(pref.min_prompt_tokens, 4096)
        self.assertEqual(pref.below_threshold_model_id, "qwen3-30b-a3b")
        self.assertEqual(pref.receipt, r"E:\work\battlemage\rotation-phase1\r10-results.jsonl")
        self.assertIn("13/16", pref.receipt_note or "")

    def test_text_families_default_to_30b_with_the_adr0039_depth_override(self) -> None:
        for name in TEXT_FAMILIES:
            pref = self.families.get(name)
            self.assertEqual(pref.model_id, "qwen3-30b-a3b", name)
            self.assertEqual(pref.evidence, ADR0039_EVIDENCE, name)
            self.assertIn("do_not_promote", pref.reason, name)
            self.assertIsNotNone(pref.depth_override, name)
            self.assertEqual(pref.depth_override.min_prompt_tokens, 8192, name)
            self.assertEqual(pref.depth_override.model_id, "qwen38-27b", name)
            self.assertEqual(pref.depth_override.evidence, ADR0039_EVIDENCE, name)

    def test_vision_families_go_to_gemini_with_the_stated_reason(self) -> None:
        for name in VISION_FAMILIES:
            pref = self.families.get(name)
            self.assertEqual(pref.model_id, "gemini-3.5-flash", name)
            self.assertEqual(pref.reason, VISION_REASON, name)
            self.assertIsNone(pref.depth_override, name)

    def test_default_family_is_the_door_default_with_depth_override(self) -> None:
        pref = self.families.get("default")
        self.assertEqual(pref.model_id, "qwen3-30b-a3b")
        self.assertIn("ADR-0034", pref.evidence)
        self.assertEqual(pref.depth_override.model_id, "qwen38-27b")
        self.assertEqual(pref.depth_override.min_prompt_tokens, 8192)

    def test_every_declared_model_is_served_by_a_live_rung_in_backends_toml(self) -> None:
        """A preference for a model nothing serves is a wish, not a recommendation."""
        pool = load_pool()
        wanted: set[str] = set()
        for pref in self.families.families.values():
            wanted.add(pref.model_id)
            if pref.below_threshold_model_id:
                wanted.add(pref.below_threshold_model_id)
            if pref.depth_override is not None:
                wanted.add(pref.depth_override.model_id)
        for model in sorted(wanted):
            live = [b.name for b in pool.by_model(model) if not b.retired]
            self.assertTrue(live, f"{model}: no non-retired backend declares it")

    def test_real_pool_binds_27b_to_omen_arc_27b_and_30b_to_omen_arc(self) -> None:
        pool = load_pool()
        self.assertEqual([b.name for b in pool.by_model("qwen38-27b")], ["omen-arc-27b"])
        self.assertIn("omen-arc", [b.name for b in pool.by_model("qwen3-30b-a3b")])
        self.assertEqual(pool.default, "omen-arc")


class RecommendTests(TestCase):
    """Depth thresholds and the derived backend_hint / pin_required."""

    def setUp(self) -> None:
        self.families = load_families()
        self.pool = _fake_pool()

    def rec(self, family, tokens):
        return recommend(family, tokens, self.families, pool=self.pool)

    def test_quote_retrieval_deep_prompt_recommends_27b_pinned(self) -> None:
        out = self.rec("quote_retrieval", 9000)
        self.assertEqual(out["model_id"], "qwen38-27b")
        self.assertEqual(out["backend_hint"], "omen-arc-27b")
        self.assertTrue(out["pin_required"])
        self.assertFalse(out["depth_rule_applied"])
        self.assertEqual(out["evidence"], R10_EVIDENCE)
        self.assertEqual(out["family"], "quote_retrieval")
        self.assertTrue(out["advisory"])

    def test_quote_retrieval_at_the_floor_is_inclusive(self) -> None:
        self.assertEqual(self.rec("quote_retrieval", 4096)["model_id"], "qwen38-27b")

    def test_quote_retrieval_below_the_floor_falls_to_the_door_default(self) -> None:
        out = self.rec("quote_retrieval", 4095)
        self.assertEqual(out["model_id"], "qwen3-30b-a3b")
        self.assertEqual(out["backend_hint"], "omen-arc")
        self.assertFalse(out["pin_required"])
        self.assertIn("evidence floor is not reached", out["reason"])
        self.assertEqual(out["evidence"], R10_EVIDENCE)

    def test_quote_retrieval_unknown_depth_keeps_27b_with_a_caveat(self) -> None:
        out = self.rec("quote_retrieval", None)
        self.assertEqual(out["model_id"], "qwen38-27b")
        self.assertIn("prompt_tokens unknown", out["reason"])
        self.assertIsNone(out["prompt_tokens"])

    def test_text_family_shallow_prompt_stays_on_omen_arc(self) -> None:
        for name in TEXT_FAMILIES:
            out = self.rec(name, 2000)
            self.assertEqual(out["model_id"], "qwen3-30b-a3b", name)
            self.assertEqual(out["backend_hint"], "omen-arc", name)
            self.assertFalse(out["pin_required"], name)
            self.assertFalse(out["depth_rule_applied"], name)
            self.assertEqual(out["evidence"], ADR0039_EVIDENCE, name)

    def test_text_family_deep_prompt_applies_the_depth_override(self) -> None:
        for name in TEXT_FAMILIES:
            out = self.rec(name, 8192)
            self.assertEqual(out["model_id"], "qwen38-27b", name)
            self.assertEqual(out["backend_hint"], "omen-arc-27b", name)
            self.assertTrue(out["pin_required"], name)
            self.assertTrue(out["depth_rule_applied"], name)
            self.assertIn("depth_override", out["reason"], name)

    def test_depth_override_threshold_is_exact(self) -> None:
        self.assertFalse(self.rec("summarization", 8191)["depth_rule_applied"])
        self.assertTrue(self.rec("summarization", 8192)["depth_rule_applied"])
        self.assertTrue(self.rec("summarization", 32000)["depth_rule_applied"])

    def test_unknown_depth_never_triggers_the_override(self) -> None:
        out = self.rec("summarization", None)
        self.assertEqual(out["model_id"], "qwen3-30b-a3b")
        self.assertFalse(out["depth_rule_applied"])
        self.assertIn("depth_override at 8192 not checked", out["reason"])

    def test_vision_families_recommend_gemini_flash(self) -> None:
        for name in VISION_FAMILIES:
            for tokens in (500, 20000, None):
                out = self.rec(name, tokens)
                self.assertEqual(out["model_id"], "gemini-3.5-flash", name)
                self.assertEqual(out["backend_hint"], "gcp-gemini", name)
                self.assertFalse(out["pin_required"], name)
                self.assertFalse(out["depth_rule_applied"], name)
                self.assertEqual(out["reason"], VISION_REASON, name)

    def test_unknown_family_falls_to_default_and_says_so(self) -> None:
        out = self.rec("banana_peeling", 100)
        self.assertEqual(out["family"], "default")
        self.assertEqual(out["requested_family"], "banana_peeling")
        self.assertEqual(out["model_id"], "qwen3-30b-a3b")
        deep = self.rec(None, 9000)
        self.assertEqual(deep["family"], "default")
        self.assertEqual(deep["model_id"], "qwen38-27b")
        self.assertTrue(deep["depth_rule_applied"])

    def test_backend_hint_skips_retired_rungs(self) -> None:
        out = self.rec("summarization", 100)
        self.assertEqual(out["providers"], ["omen-arc"])
        self.assertNotIn("dead-rung", out["providers"])

    def test_no_provider_yields_none_hint_and_a_loud_reason(self) -> None:
        empty = Pool(default="x", backends=(
            Backend(name="x", endpoint="http://127.0.0.1:1", api="openai", models=("other",)),))
        out = recommend("quote_retrieval", 9000, self.families, pool=empty)
        self.assertEqual(out["model_id"], "qwen38-27b")
        self.assertIsNone(out["backend_hint"])
        self.assertEqual(out["providers"], [])
        self.assertFalse(out["pin_required"])
        self.assertIn("no declared, non-retired backend serves qwen38-27b", out["reason"])

    def test_prompt_tokens_accepts_numeric_strings_and_rejects_junk(self) -> None:
        self.assertTrue(self.rec("summarization", "9000")["depth_rule_applied"])
        out = self.rec("summarization", "lots")
        self.assertIsNone(out["prompt_tokens"])
        self.assertFalse(out["depth_rule_applied"])

    def test_recommend_reads_the_real_pool_when_none_is_injected(self) -> None:
        out = recommend("quote_retrieval", 9000, self.families)
        self.assertEqual(out["backend_hint"], "omen-arc-27b")
        self.assertTrue(out["pin_required"])


class ResolveRequiredModelTests(TestCase):
    """The scheduler hook: explicit wins, family resolves, family-less stays None."""

    def setUp(self) -> None:
        self.families = load_families()

    def test_explicit_required_model_wins_untouched(self) -> None:
        job = Job(plan_id="c1", task_class="inference", required_model="phi4-vk1", est_tokens=50000)
        self.assertEqual(resolve_required_model(job, self.families), "phi4-vk1")
        snap = {"plan_id": "c1", "task_class": "inference", "required_model": "mistral24b-vk1",
                "task_family": "quote_retrieval", "est_tokens": 9000}
        self.assertEqual(resolve_required_model(snap, self.families), "mistral24b-vk1")

    def test_family_less_job_is_left_unconstrained(self) -> None:
        self.assertIsNone(resolve_required_model(
            Job(plan_id="b1", task_class="build", est_tokens=4000), self.families))
        self.assertIsNone(resolve_required_model(
            {"plan_id": "b1", "task_class": "build", "task_family": ""}, self.families))

    def test_snapshot_dict_resolves_by_family_and_depth(self) -> None:
        q1 = {"plan_id": "q1", "task_class": "inference", "task_family": "quote_retrieval",
              "est_tokens": 9000, "est_out_tokens": 300}
        s1 = {"plan_id": "s1", "task_class": "inference", "task_family": "summarization",
              "est_tokens": 2000, "est_out_tokens": 400}
        self.assertEqual(resolve_required_model(q1, self.families), "qwen38-27b")
        self.assertEqual(resolve_required_model(s1, self.families), "qwen3-30b-a3b")

    def test_prompt_tokens_outranks_est_tokens_as_the_depth_proxy(self) -> None:
        deep = {"plan_id": "s2", "task_class": "inference", "task_family": "summarization",
                "prompt_tokens": 9000, "est_tokens": 100}
        self.assertEqual(job_prompt_tokens(deep), 9000)
        self.assertEqual(resolve_required_model(deep, self.families), "qwen38-27b")
        shallow = {"plan_id": "s3", "task_class": "inference", "task_family": "summarization",
                   "prompt_tokens": 100, "est_tokens": 9000}
        self.assertEqual(resolve_required_model(shallow, self.families), "qwen3-30b-a3b")

    def test_job_dataclass_without_a_task_family_attribute_is_safe(self) -> None:
        # Pre-P2 Job has no task_family/prompt_tokens field: attribute access must not raise.
        job = Job(plan_id="i1", task_class="inference", est_tokens=9000)
        self.assertIsNone(resolve_required_model(job, self.families))
        self.assertEqual(job_prompt_tokens(job), 9000)

    def test_unknown_family_resolves_through_default(self) -> None:
        self.assertEqual(resolve_required_model(
            {"plan_id": "u1", "task_class": "inference", "task_family": "mystery",
             "est_tokens": 100}, self.families), "qwen3-30b-a3b")

    def test_loads_the_packaged_file_when_no_families_are_passed(self) -> None:
        self.assertEqual(resolve_required_model(
            {"plan_id": "q", "task_class": "inference", "task_family": "quote_retrieval",
             "est_tokens": 9000}), "qwen38-27b")


class LoaderValidationTests(TestCase):
    """Malformed declarations fail loud; the env override and explicit path work."""

    def _load(self, body: str) -> Families:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "families.toml"
            path.write_text(textwrap.dedent(body), encoding="utf-8")
            return load_families(path)

    def test_missing_file_is_an_error_not_a_fallback(self) -> None:
        with self.assertRaises(FamiliesConfigError):
            load_families(Path("Z:/nowhere/routing-families.toml"))

    def test_wrong_contract_is_refused(self) -> None:
        with self.assertRaisesRegex(FamiliesConfigError, "contract"):
            self._load("""
                contract = "routing-families.v0"
                [family.default]
                model_id = "m"
                evidence = "e"
                reason = "r"
            """)

    def test_default_family_is_required(self) -> None:
        with self.assertRaisesRegex(FamiliesConfigError, "family.default"):
            self._load("""
                contract = "routing-families.v1"
                [family.summarization]
                model_id = "m"
                evidence = "e"
                reason = "r"
            """)

    def test_evidence_is_mandatory_on_families_and_overrides(self) -> None:
        with self.assertRaisesRegex(FamiliesConfigError, "family.default: evidence"):
            self._load("""
                contract = "routing-families.v1"
                [family.default]
                model_id = "m"
                evidence = "  "
                reason = "r"
            """)
        with self.assertRaisesRegex(FamiliesConfigError, "depth_override: evidence"):
            self._load("""
                contract = "routing-families.v1"
                [family.default]
                model_id = "m"
                evidence = "e"
                reason = "r"
                [family.default.depth_override]
                min_prompt_tokens = 8192
                model_id = "big"
            """)

    def test_thresholds_must_be_positive_integers(self) -> None:
        with self.assertRaisesRegex(FamiliesConfigError, "min_prompt_tokens"):
            self._load("""
                contract = "routing-families.v1"
                [family.default]
                model_id = "m"
                evidence = "e"
                reason = "r"
                min_prompt_tokens = 0
                below_threshold_model_id = "n"
            """)
        with self.assertRaisesRegex(FamiliesConfigError, "below_threshold_model_id"):
            self._load("""
                contract = "routing-families.v1"
                [family.default]
                model_id = "m"
                evidence = "e"
                reason = "r"
                min_prompt_tokens = 4096
            """)

    def test_env_var_overrides_the_packaged_path(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "alt.toml"
            path.write_text(textwrap.dedent("""
                contract = "routing-families.v1"
                authored = "test"
                [family.default]
                model_id = "alt-model"
                evidence = "e"
                reason = "r"
            """), encoding="utf-8")
            with patch.dict(os.environ, {"HEARTH_ROUTING_FAMILIES": str(path)}):
                fam = load_families()
            self.assertEqual(fam.source, path)
            self.assertEqual(fam.authored, "test")
            self.assertEqual(fam.get("anything").model_id, "alt-model")
