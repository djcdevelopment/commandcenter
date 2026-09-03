"""P2b / ADR-0045: the advisory scheduler plans over a rotating stateful OMEN.

Offline and deterministic. The omen-catalog.v1 document is a fixture dict written
into a HEARTH_SCOPE sandbox (never gathered); the inventory is pinned. The numbers
mirror knowledge/omen_catalog.json as of 2026-09-03 (30B resident dual 15.44 GB per
card, 27B dual 17.67 GB total, quad side models 8.3/8.4/11.3/13.3 GB, cards 32.5 GB
by BDF) so the arithmetic the tests assert is the arithmetic the live host has.

Invariants pinned here:
  * absent omen catalog -> the pool and the proposal are exactly what load_machines +
    solve_schedule give (the JS7b path), and `rotation_plan` is an empty sibling;
  * the 27B pays its steady load (8.22 s) — first-in-window (19.51 s) when cold —
    and charges BOTH cards (both BDFs);
  * a build job never lands on omen-inference (roles);
  * phi4 + mistral beside the resident 30B land on DIFFERENT cards;
  * over-full -> `rotation_plan.blocked` with the numbers, never INFEASIBLE;
  * kv_state_available -> a kv_restore step of 1.19 s;
  * task_family resolves to a model through routing-families.v1;
  * `rotation_plan` is a sibling of `proposal`, never inside `decision_record`.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.scheduler.decision import validate_decision
from hearth.scheduler.ontology import load_machines
from hearth.scheduler.solve import solve_schedule
from hearth.toolsurface.scheduler import _job_from_dict, propose_schedule

REPO_ROOT = Path(__file__).resolve().parents[3]

BDF_A = "0000:04:00.0"
BDF_B = "0000:09:00.0"
OMEN = "omen-inference"

_PINNED_INVENTORY = """
[[node]]
name = "am4-worker-1"
runner_class = "local"

[[node]]
name = "cc-builder-2"
runner_class = "local"

[[node]]
name = "cc-builder-1"
runner_class = "frontier"
"""


def _omen_catalog() -> dict:
    """omen-catalog.v1 fixture: the production 30B resident (dual), the 27B (dual),
    and the four R6 quad side models (single). Receipts abbreviated."""
    def model(model_id, placement, vram_gb, per_card_gb, tps, **extra):
        row = {
            "model_id": model_id, "alias": model_id, "placement": placement,
            "visible_devices": None, "vram_gb": vram_gb, "per_card_gb": per_card_gb,
            "expected_gen_tps": tps, "warmup_ms_p50": None, "warmup_ms_max": None,
            "sample_count": 1, "load_s_steady": None, "load_s_first_in_window": None,
            "kv_save_s": None, "kv_restore_s": None, "unload_drain_s_max": None,
            "swap_entry": None, "exclusive_group": None, "receipts": {},
        }
        row.update(extra)
        return row

    return {
        "contract_version": "omen-catalog.v1",
        "gathered_at": "2026-09-03T09:51:09Z",
        "host": "omen",
        "resident_models": ["qwen3-30b-a3b"],
        "staging_slots": 1,
        "gates": {"commit_min_free_gb": 6.0, "vram_headroom_gb": 0.5,
                  "vram_temperature_abort_c": 95, "temperature_resume_below_c": 80,
                  "shared_growth_abort_gb": 2.0},
        "cards": [{"index": 0, "bdf": BDF_A, "vram_gb": 32.5},
                  {"index": 1, "bdf": BDF_B, "vram_gb": 32.5}],
        "models": [
            model("qwen3-30b-a3b", "dual", 29.96, 15.44, 106.0,
                  load_s_steady=8.175, load_s_first_in_window=26.58,
                  kv_save_s=1.74, kv_restore_s=1.19, unload_drain_s_max=27,
                  receipts={"load_s_steady": "r2-receipts.jsonl#dio",
                            "kv_restore_s": "ADR-0040 P3"}),
            model("qwen38-27b", "dual", 17.67, None, 23.4,
                  load_s_steady=8.22, load_s_first_in_window=19.51,
                  receipts={"load_s_steady": "r2-receipts.jsonl#27b"}),
            model("phi4", "single", 8.3, 8.3, 48.7, swap_entry="phi4-card1"),
            model("qwen14b", "single", 8.4, 8.4, 45.6),
            model("gptoss20b", "single", 11.3, 11.3, 98.3),
            model("mistral24b", "single", 13.3, 13.3, 29.8),
        ],
    }


def _am4_catalog() -> dict:
    return {
        "contract_version": "am4-catalog.v1",
        "gathered_at": "2026-07-04T00:00:00Z",
        "host": "am4-worker-1",
        "gates": {"max_host_used_gb_preflight": 28.0},
        "cards": [{"index": 0, "vram_gb": 32.0}, {"index": 1, "vram_gb": 32.0}],
        "models": [
            {"model_id": "qwen3-coder:30b", "alias": "coder", "placement": "single",
             "visible_devices": "0", "vram_gb": 20.0, "per_card_gb": 20.0,
             "expected_gen_tps": 30.0, "warmup_ms_p50": 5000, "warmup_ms_max": 9000,
             "sample_count": 3},
        ],
    }


def _load_steps(result: dict) -> list[dict]:
    return [s for s in result["rotation_plan"]["steps"] if s["action"] == "load"]


class _Sandbox(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        (self.scope / "fleet").mkdir(parents=True, exist_ok=True)
        (self.scope / "hearth" / "etc").mkdir(parents=True, exist_ok=True)
        (self.scope / "knowledge").mkdir(parents=True, exist_ok=True)
        (self.scope / "fleet" / "inventory.toml").write_text(_PINNED_INVENTORY, encoding="utf-8")

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous
        shutil.rmtree(self.scope, ignore_errors=True)

    def _write_omen(self, catalog: dict | None = None) -> None:
        (self.scope / "knowledge" / "omen_catalog.json").write_text(
            json.dumps(catalog if catalog is not None else _omen_catalog()), encoding="utf-8")

    def _write_am4(self) -> None:
        (self.scope / "knowledge" / "am4_catalog.json").write_text(
            json.dumps(_am4_catalog()), encoding="utf-8")


class AbsentCatalogTests(_Sandbox):
    def test_absent_catalog_proposals_are_byte_identical(self) -> None:
        jobs = [{"plan_id": "j1", "task_class": "build", "est_tokens": 1000},
                {"plan_id": "j2", "task_class": "inference", "est_tokens": 200,
                 "required_model": "phi4-vk1", "est_out_tokens": 50},
                {"plan_id": "j3", "task_class": "test", "est_tokens": 300, "precedence": ["j1"]}]
        result = propose_schedule(jobs)
        # The JS7b path: the same pool, the same solve, nothing appended.
        machines = load_machines(str(self.scope / "fleet" / "inventory.toml"),
                                 str(self.scope / "hearth" / "etc" / "backends.toml"))
        expected = solve_schedule([_job_from_dict(j) for j in jobs], machines, None, models={})
        self.assertEqual(result["proposal"]["assignments"], expected.assignments)
        self.assertEqual(result["proposal"]["makespan_s"], expected.makespan_s)
        self.assertEqual(result["proposal"]["solver_status"], expected.solver_status)
        self.assertEqual(result["proposal"]["loads"], [])
        self.assertEqual(result["proposal"]["residency"], [])
        self.assertNotIn(OMEN, [m["name"] for m in result["machines_considered"]])
        self.assertEqual([m["name"] for m in result["machines_considered"]],
                         [m.name for m in machines])
        # A second call with an explicitly missing path is the same proposal.
        again = propose_schedule(jobs, omen_catalog_path="knowledge/nope.json")
        self.assertEqual(again["proposal"], result["proposal"])
        self.assertEqual(again["machines_considered"], result["machines_considered"])
        # The sibling is present but empty, and never inside the decision record.
        plan = result["rotation_plan"]
        self.assertIsNone(plan["machine"])
        self.assertEqual(plan["steps"], [])
        self.assertEqual(plan["blocked"], [])
        self.assertNotIn("rotation_plan", result["decision_record"])
        validate_decision(result["decision_record"])

    def test_family_less_jobs_never_touch_the_families_file(self) -> None:
        os.environ["HEARTH_ROUTING_FAMILIES"] = str(self.scope / "missing-families.toml")
        try:
            result = propose_schedule([{"plan_id": "j1", "task_class": "build", "est_tokens": 10}])
        finally:
            os.environ.pop("HEARTH_ROUTING_FAMILIES", None)
        self.assertTrue(result["ok"])


class OmenCatalogTests(_Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self._write_omen()

    def test_omen_inference_is_appended_as_a_stateful_inference_host(self) -> None:
        result = propose_schedule([{"plan_id": "j1", "task_class": "build", "est_tokens": 10}])
        names = [m["name"] for m in result["machines_considered"]]
        self.assertIn(OMEN, names)
        omen = next(m for m in result["machines_considered"] if m["name"] == OMEN)
        self.assertEqual(omen["kind"], "local")
        self.assertEqual(omen["token_cost_weight"], 0.0)
        detail = result["rotation_plan"]["machine_detail"]
        self.assertEqual(detail["host"], "omen")
        self.assertEqual(detail["roles"], ["inference"])
        self.assertEqual(detail["staging_slots"], 1)
        self.assertEqual(detail["resident_models"], ["qwen3-30b-a3b"])
        self.assertEqual([c["bdf"] for c in detail["cards"]], [BDF_A, BDF_B])

    def test_hindsight_pool_never_contains_omen(self) -> None:
        # The machine is appended by propose_schedule, NOT by load_machines: replaying
        # historical builder runs must never re-plan them onto the B70s.
        machines = load_machines(str(self.scope / "fleet" / "inventory.toml"),
                                 str(self.scope / "hearth" / "etc" / "backends.toml"))
        self.assertNotIn(OMEN, [m.name for m in machines])

    def test_27b_pays_steady_load_and_charges_both_cards(self) -> None:
        result = propose_schedule([{"plan_id": "q1", "task_class": "inference",
                                    "required_model": "qwen38-27b", "est_out_tokens": 300}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["assignments"][0]["machine"], OMEN)
        loads = result["proposal"]["loads"]
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["model_id"], "qwen38-27b")
        self.assertEqual(loads[0]["setup_s"], 8.22)
        self.assertEqual(loads[0]["cards"], [0, 1])
        self.assertEqual(loads[0]["bdfs"], [BDF_A, BDF_B])
        self.assertFalse(loads[0]["cold"])
        steps = _load_steps(result)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["est_s"], 8.22)
        self.assertEqual(step["est_s_first_in_window"], 19.51)
        self.assertEqual(step["placement"], "dual")
        self.assertEqual(step["cards"], [BDF_A, BDF_B])
        self.assertEqual(step["serves"], ["q1"])
        self.assertEqual(step["swap_entry_candidates"], ["qwen38-27b-dual"])
        self.assertEqual(step["evidence"]["load_s_steady"]["receipt"], "r2-receipts.jsonl#27b")
        # Both cards carry the 27B beside the resident 30B. The solver's
        # integer deci-GiB representation quantizes 15.44 + 17.67/2 to 24.2.
        for row in result["proposal"]["residency"]:
            self.assertIn("qwen38-27b", row["resident_models"])
            self.assertIn("qwen3-30b-a3b", row["resident_models"])
            self.assertEqual(row["used_gb"], 24.2)
        # The job starts after the load and runs est_out_tokens / expected_gen_tps.
        job = result["proposal"]["assignments"][0]
        self.assertGreaterEqual(job["start_s"], loads[0]["end_s"])
        self.assertEqual(job["end_s"] - job["start_s"], 13.0)  # ceil(300 / 23.4)

    def test_cold_host_pays_first_in_window(self) -> None:
        result = propose_schedule([{"plan_id": "q1", "task_class": "inference",
                                    "required_model": "qwen38-27b", "est_out_tokens": 300}],
                                  omen_cold=True)
        self.assertEqual(result["proposal"]["loads"][0]["setup_s"], 19.51)
        self.assertTrue(result["proposal"]["loads"][0]["cold"])
        self.assertEqual(_load_steps(result)[0]["est_s"], 19.51)
        self.assertTrue(result["rotation_plan"]["machine_detail"]["cold"])

    def test_build_job_never_on_omen_inference(self) -> None:
        jobs = [{"plan_id": f"b{i}", "task_class": "build", "est_tokens": 100,
                 "est_duration_s": 60} for i in range(4)]
        jobs.append({"plan_id": "i1", "task_class": "inference", "est_tokens": 100,
                     "est_duration_s": 5})
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        by_plan = {a["plan_id"]: a["machine"] for a in result["proposal"]["assignments"]}
        for i in range(4):
            self.assertNotEqual(by_plan[f"b{i}"], OMEN)

    def test_phi4_and_mistral_beside_resident_30b_land_on_different_cards(self) -> None:
        # Free per card beside the 30B: 32.5 - 0.5 - 15.44 = 16.56 GB. phi4 (8.3) +
        # mistral24b (13.3) = 21.6 does not fit one card; each fits alone.
        result = propose_schedule([
            {"plan_id": "c1", "task_class": "inference", "required_model": "phi4-vk1",
             "est_out_tokens": 200},
            {"plan_id": "c2", "task_class": "inference", "required_model": "mistral24b-vk1",
             "est_out_tokens": 200}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rotation_plan"]["blocked"], [])
        steps = {s["model_id"]: s for s in _load_steps(result)}
        self.assertEqual(set(steps), {"phi4", "mistral24b"})
        self.assertEqual(len(steps["phi4"]["cards"]), 1)
        self.assertEqual(len(steps["mistral24b"]["cards"]), 1)
        self.assertNotEqual(steps["phi4"]["cards"], steps["mistral24b"]["cards"])
        self.assertEqual({BDF_A, BDF_B},
                         {steps["phi4"]["cards"][0], steps["mistral24b"]["cards"][0]})
        # The entry the job used is the entry the plan names; both siblings offered.
        self.assertEqual(steps["phi4"]["swap_entry"], "phi4-vk1")
        self.assertEqual(steps["phi4"]["swap_entry_candidates"], ["phi4-vk1", "phi4-vk2"])
        self.assertEqual(steps["mistral24b"]["swap_entry"], "mistral24b-vk1")
        # Loads are accounted once per physical model, keyed by catalog id.
        loads = {row["model_id"]: row for row in result["proposal"]["loads"]}
        self.assertEqual(loads["phi4"]["requested_as"], ["phi4-vk1"])
        # No card is over budget.
        for row in result["proposal"]["residency"]:
            self.assertLessEqual(row["used_gb"], row["budget_gb"] - 0.5)

    def test_single_model_that_can_never_fit_is_blocked_pre_solve(self) -> None:
        catalog = _omen_catalog()
        catalog["models"].append({
            "model_id": "bigsingle", "alias": "bigsingle", "placement": "single",
            "vram_gb": 20.0, "per_card_gb": 20.0, "expected_gen_tps": 10.0})
        self._write_omen(catalog)
        result = propose_schedule([
            {"plan_id": "big", "task_class": "inference", "required_model": "bigsingle",
             "est_out_tokens": 100},
            {"plan_id": "c1", "task_class": "inference", "required_model": "phi4-vk1",
             "est_out_tokens": 200}])
        self.assertTrue(result["ok"])  # NOT INFEASIBLE: the rest still solves
        self.assertEqual(result["proposal"]["solver_status"], "OPTIMAL")
        blocked = result["rotation_plan"]["blocked"]
        self.assertEqual([b["plan_id"] for b in blocked], ["big"])
        row = blocked[0]
        self.assertTrue(row["reason"].startswith("over_full"))
        self.assertEqual(row["per_card_gb"], 20.0)
        self.assertEqual(row["free_gb_by_card"], {BDF_A: 16.56, BDF_B: 16.56})
        self.assertEqual(row["budget_gb_by_card"], {BDF_A: 32.0, BDF_B: 32.0})
        self.assertEqual(row["resident_models"], ["qwen3-30b-a3b"])
        self.assertEqual([a["plan_id"] for a in result["proposal"]["assignments"]], ["c1"])
        self.assertNotIn("big", [a["plan_id"] for a in result["proposal"]["assignments"]])

    def test_cumulative_over_full_names_the_overflow_and_resolves_the_rest(self) -> None:
        # 8.3 + 8.4 + 11.3 + 13.3 = 41.3 GB > 2 x 16.56 free: no packing exists.
        jobs = [{"plan_id": f"c{i}", "task_class": "inference", "required_model": m,
                 "est_out_tokens": 100}
                for i, m in enumerate(["phi4-vk1", "qwen14b-vk1", "gptoss20b-vk1",
                                       "mistral24b-vk1"])]
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        blocked = result["rotation_plan"]["blocked"]
        self.assertTrue(blocked)
        for row in blocked:
            self.assertTrue(row["reason"].startswith("cumulative_over_full"))
            self.assertIn(BDF_A, row["free_gb_by_card"])
        placed = {a["plan_id"] for a in result["proposal"]["assignments"]}
        self.assertEqual(placed | {b["plan_id"] for b in blocked}, {"c0", "c1", "c2", "c3"})
        self.assertTrue(placed)
        self.assertTrue(any("moved to blocked" in a for a in result["rotation_plan"]["assumptions"]))
        for row in result["proposal"]["residency"]:
            self.assertLessEqual(row["used_gb"], row["budget_gb"] - 0.5)

    def test_kv_state_available_adds_a_restore_step(self) -> None:
        result = propose_schedule([{"plan_id": "k1", "task_class": "inference",
                                    "required_model": "qwen3-30b-a3b", "est_out_tokens": 106,
                                    "kv_state_available": True}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["loads"], [])  # resident: no load
        job = result["proposal"]["assignments"][0]
        self.assertEqual(job["machine"], OMEN)
        self.assertEqual(job["kv_hydrate_s"], 1.19)
        self.assertEqual(job["end_s"] - job["start_s"], 3.0)  # ceil(1 + 1.19)
        steps = result["rotation_plan"]["steps"]
        self.assertEqual([s["action"] for s in steps], ["kv_restore"])
        self.assertEqual(steps[0]["est_s"], 1.19)
        self.assertEqual(steps[0]["model_id"], "qwen3-30b-a3b")
        self.assertEqual(steps[0]["serves"], ["k1"])
        self.assertEqual(steps[0]["cards"], [BDF_A, BDF_B])
        self.assertEqual(steps[0]["evidence"]["kv_hydrate_s"]["receipt"], "ADR-0040 P3")

    def test_task_family_resolves_required_model(self) -> None:
        result = propose_schedule([
            {"plan_id": "q1", "task_class": "inference", "task_family": "quote_retrieval",
             "est_tokens": 9000, "est_out_tokens": 300},
            {"plan_id": "s1", "task_class": "inference", "task_family": "summarization",
             "est_tokens": 2000, "est_out_tokens": 400},
            {"plan_id": "v1", "task_class": "inference", "task_family": "document_ocr",
             "est_tokens": 500, "est_out_tokens": 100}])
        self.assertTrue(result["ok"])
        by_plan = {a["plan_id"]: a for a in result["proposal"]["assignments"]}
        self.assertEqual(set(by_plan), {"q1", "s1", "v1"})
        steps = _load_steps(result)
        self.assertEqual([s["model_id"] for s in steps], ["qwen38-27b"])
        self.assertEqual(steps[0]["serves"], ["q1"])
        self.assertEqual(steps[0]["est_s"], 8.22)
        # summarization at 2000 tokens -> the resident 30B: no load, runs on OMEN.
        self.assertEqual(by_plan["s1"]["machine"], OMEN)
        # document_ocr -> gemini: not a stateful host in this pool; scheduled
        # without a residency constraint and said so, not INFEASIBLE.
        self.assertTrue(any(a.startswith("v1:") and "gemini-3.5-flash" in a
                            for a in result["rotation_plan"]["assumptions"]))

    def test_job_from_dict_reads_the_p2_fields(self) -> None:
        job = _job_from_dict({"plan_id": "x", "task_class": "inference",
                              "task_family": "summarization", "prompt_tokens": 9000,
                              "kv_state_available": True})
        self.assertEqual(job.task_family, "summarization")
        self.assertEqual(job.prompt_tokens, 9000)
        self.assertTrue(job.kv_state_available)
        # depth override: >= 8192 prompt tokens -> the 27B (ADR-0039)
        self.assertEqual(job.required_model, "qwen38-27b")
        explicit = _job_from_dict({"plan_id": "y", "task_class": "inference",
                                   "task_family": "summarization", "prompt_tokens": 9000,
                                   "required_model": "phi4-vk2"})
        self.assertEqual(explicit.required_model, "phi4-vk2")
        plain = _job_from_dict({"plan_id": "z", "task_class": "build"})
        self.assertIsNone(plain.required_model)
        self.assertFalse(plain.kv_state_available)

    def test_omen_resident_override_from_live_running(self) -> None:
        # llama-swap /running lists entries; the sibling entry of a resident model
        # needs no load, and the residency accounts the model once.
        result = propose_schedule(
            [{"plan_id": "c1", "task_class": "inference", "required_model": "phi4-vk2",
              "est_out_tokens": 100}],
            omen_resident=["qwen3-30b-a3b", "phi4-vk1"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["loads"], [])
        self.assertEqual(result["rotation_plan"]["steps"], [])
        self.assertEqual(result["rotation_plan"]["machine_detail"]["resident_models"],
                         ["qwen3-30b-a3b", "phi4"])
        residents = [m for row in result["proposal"]["residency"] for m in row["resident_models"]]
        self.assertEqual(residents.count("phi4"), 1)
        self.assertEqual(residents.count("qwen3-30b-a3b"), 2)

    def test_rotation_plan_is_a_sibling_never_in_the_decision_record(self) -> None:
        result = propose_schedule([{"plan_id": "q1", "task_class": "inference",
                                    "required_model": "qwen38-27b", "est_out_tokens": 300}])
        self.assertIn("rotation_plan", result)
        self.assertNotIn("rotation_plan", result["decision_record"])
        self.assertNotIn("rotation_plan", result["proposal"])
        validate_decision(result["decision_record"])
        self.assertTrue(any("ADR-0008" in a for a in result["rotation_plan"]["assumptions"]))
        json.dumps(result)  # the whole result is JSON-serializable (door-safe)


class TwoCatalogTests(_Sandbox):
    def test_each_stateful_host_loads_only_its_own_catalog(self) -> None:
        self._write_omen()
        self._write_am4()
        result = propose_schedule([
            {"plan_id": "a1", "task_class": "inference", "required_model": "qwen3-coder:30b",
             "est_out_tokens": 300},
            {"plan_id": "c1", "task_class": "inference", "required_model": "phi4-vk1",
             "est_out_tokens": 200}])
        self.assertTrue(result["ok"])
        by_plan = {a["plan_id"]: a["machine"] for a in result["proposal"]["assignments"]}
        self.assertEqual(by_plan["a1"], "am4-worker-1")
        self.assertEqual(by_plan["c1"], OMEN)
        loads = {(row["machine"], row["model_id"]) for row in result["proposal"]["loads"]}
        self.assertEqual(loads, {("am4-worker-1", "qwen3-coder:30b"), (OMEN, "phi4")})
        # The AM4 model is exempt from OMEN's fit check and absent from its plan.
        self.assertEqual(result["rotation_plan"]["blocked"], [])
        self.assertEqual([s["model_id"] for s in _load_steps(result)], ["phi4"])


class RealCatalogDemoTests(_Sandbox):
    """The plan's Verification command against the committed knowledge/omen_catalog.json
    (P1) and the real fleet files, copied into the sandbox. Structural invariants
    only — the catalog's numbers belong to P1's receipts, not to this test."""

    def test_verification_command_shape(self) -> None:
        real = REPO_ROOT / "knowledge" / "omen_catalog.json"
        if not real.is_file():
            self.skipTest("knowledge/omen_catalog.json not present")
        shutil.copy(real, self.scope / "knowledge" / "omen_catalog.json")
        shutil.copy(REPO_ROOT / "fleet" / "inventory.toml", self.scope / "fleet" / "inventory.toml")
        shutil.copy(REPO_ROOT / "hearth" / "etc" / "backends.toml",
                    self.scope / "hearth" / "etc" / "backends.toml")
        am4 = REPO_ROOT / "knowledge" / "am4_catalog.json"
        if am4.is_file():
            shutil.copy(am4, self.scope / "knowledge" / "am4_catalog.json")
        jobs = [
            {"plan_id": "q1", "task_class": "inference", "task_family": "quote_retrieval",
             "est_tokens": 9000, "est_out_tokens": 300},
            {"plan_id": "s1", "task_class": "inference", "task_family": "summarization",
             "est_tokens": 2000, "est_out_tokens": 400},
            {"plan_id": "c1", "task_class": "inference", "required_model": "phi4-vk1",
             "est_out_tokens": 200},
            {"plan_id": "c2", "task_class": "inference", "required_model": "mistral24b-vk1",
             "est_out_tokens": 200},
            {"plan_id": "b1", "task_class": "build", "est_tokens": 4000},
        ]
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        plan = result["rotation_plan"]
        self.assertEqual(plan["machine"], OMEN)
        placed = {a["plan_id"]: a["machine"] for a in result["proposal"]["assignments"]}
        blocked = {b["plan_id"] for b in plan["blocked"]}
        self.assertEqual(set(placed) | blocked, {"q1", "s1", "c1", "c2", "b1"})
        self.assertNotEqual(placed.get("b1"), OMEN)
        steps = {s["model_id"]: s for s in _load_steps(result)}
        self.assertNotIn("qwen3-30b-a3b", steps)  # s1 rides the resident 30B
        if "phi4" in steps and "mistral24b" in steps:
            self.assertNotEqual(steps["phi4"]["cards"], steps["mistral24b"]["cards"])
        for step in steps.values():
            for card in step["cards"]:
                self.assertRegex(card, r"^0000:[0-9a-f]{2}:00\.0$")
        for row in plan["blocked"]:
            self.assertIn("free_gb_by_card", row)
            self.assertIn("per_card_gb", row)
        json.dumps(plan)
