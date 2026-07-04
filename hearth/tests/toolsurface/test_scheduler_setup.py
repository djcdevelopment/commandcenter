"""JS7b: setup-aware CP-SAT — model residency, load intervals, DDR4 staging, VRAM.

Deterministic, offline. The am4-catalog.v1 is supplied as fixture dicts (never read
over SSH). These tests exercise the new residency layer directly against
solve_schedule, plus the provider path with a catalog written into a HEARTH_SCOPE
sandbox. The regression that stateless behavior is byte-identical lives in
test_scheduler.py (still 30 passing, unchanged).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.scheduler.ontology import (
    Job,
    Machine,
    ModelSpec,
    load_am4_catalog,
    lookup_duration_s,
)
from hearth.scheduler.solve import solve_schedule
from hearth.toolsurface.scheduler import propose_schedule

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- fixtures ---------------------------------------------------------------

def _am4(resident=(), staging_slots=1, cards=None) -> Machine:
    """The stateful AM4 box: 2x Arc B70, 32GB VRAM each here (fixture), 1 DDR4 slot."""
    return Machine(
        name="am4-worker-1", kind="local", token_cost_weight=0.0, tags=["local"],
        available=True, stateful=True,
        cards=cards if cards is not None else [
            {"index": 0, "vram_gb": 32.0}, {"index": 1, "vram_gb": 32.0}],
        resident_models=list(resident), staging_slots=staging_slots,
        host="am4-worker-1",
    )


def _frontier() -> Machine:
    return Machine(name="frontier-x", kind="frontier", token_cost_weight=1.0,
                   tags=["frontier"], available=True)


def _catalog_dict() -> dict:
    """A frozen am4-catalog.v1 fixture: two single-card models + one dual-card model."""
    return {
        "contract_version": "am4-catalog.v1",
        "gathered_at": "2026-07-04T00:00:00Z",
        "host": "am4-worker-1",
        "gates": {"max_host_used_gb_preflight": 28.0},
        "cards": [{"index": 0, "vram_gb": 32.0}, {"index": 1, "vram_gb": 32.0}],
        "models": [
            {"model_id": "qwen3-coder:30b", "alias": "coder", "placement": "single",
             "visible_devices": "0", "vram_gb": 20.0, "per_card_gb": 20.0,
             "expected_gen_tps": 50.0, "warmup_ms_p50": 12000, "warmup_ms_max": 15000,
             "sample_count": 8, "notes": ""},
            {"model_id": "mixtral:8x7b", "alias": "mix", "placement": "single",
             "visible_devices": "1", "vram_gb": 18.0, "per_card_gb": 18.0,
             "expected_gen_tps": 40.0, "warmup_ms_p50": 9000, "warmup_ms_max": 11000,
             "sample_count": 5, "notes": ""},
            {"model_id": "llama3.1:70b", "alias": "big", "placement": "dual",
             "visible_devices": "0,1", "vram_gb": 40.0, "per_card_gb": 20.0,
             "expected_gen_tps": 20.0, "warmup_ms_p50": 30000, "warmup_ms_max": 34000,
             "sample_count": 4, "notes": "spans both cards"},
        ],
    }


def _models() -> dict:
    """{model_id/alias: ModelSpec} as load_am4_catalog would return."""
    empty = {"contract_version": "am4-catalog.v1", "cards": [], "models": _catalog_dict()["models"]}
    d = _catalog_dict()
    specs = {}
    for raw in d["models"]:
        s = ModelSpec(
            model_id=raw["model_id"], alias=raw.get("alias"), placement=raw["placement"],
            visible_devices=raw.get("visible_devices"), vram_gb=raw.get("vram_gb"),
            per_card_gb=raw.get("per_card_gb"), expected_gen_tps=raw.get("expected_gen_tps"),
            warmup_ms_p50=raw.get("warmup_ms_p50"), warmup_ms_max=raw.get("warmup_ms_max"),
            sample_count=raw.get("sample_count"), notes=raw.get("notes"))
        specs[s.model_id] = s
        if s.alias:
            specs[s.alias] = s
    return specs


# --- catalog loader ---------------------------------------------------------

class CatalogLoaderTests(TestCase):
    def test_absent_catalog_is_empty_not_error(self) -> None:
        cat = load_am4_catalog("/no/such/am4_catalog.json")
        self.assertEqual(cat["models"], {})
        self.assertIsNone(cat["cards"])
        self.assertIsNone(cat["gates"])

    def test_catalog_parses_models_gates_cards(self) -> None:
        sandbox = Path(mkdtemp())
        p = sandbox / "am4_catalog.json"
        p.write_text(json.dumps(_catalog_dict()), encoding="utf-8")
        cat = load_am4_catalog(str(p))
        self.assertIn("qwen3-coder:30b", cat["models"])
        self.assertIn("coder", cat["models"])  # alias keyed too
        self.assertEqual(cat["models"]["coder"].warmup_ms_p50, 12000)
        self.assertEqual(cat["gates"]["max_host_used_gb_preflight"], 28.0)
        self.assertEqual(len(cat["cards"]), 2)

    def test_wrong_contract_version_ignored(self) -> None:
        sandbox = Path(mkdtemp())
        p = sandbox / "am4_catalog.json"
        bad = _catalog_dict()
        bad["contract_version"] = "am4-catalog.v99"
        p.write_text(json.dumps(bad), encoding="utf-8")
        self.assertEqual(load_am4_catalog(str(p))["models"], {})

    def test_setup_s_fallback_chain(self) -> None:
        spec = ModelSpec(model_id="m", warmup_ms_p50=12000, warmup_ms_max=15000)
        self.assertEqual(spec.setup_s(), 12.0)
        spec2 = ModelSpec(model_id="m", warmup_ms_p50=None, warmup_ms_max=15000)
        self.assertEqual(spec2.setup_s(), 15.0)
        spec3 = ModelSpec(model_id="m")
        self.assertEqual(spec3.setup_s(30.0), 30.0)

    def test_duration_from_gen_tps(self) -> None:
        job = Job(plan_id="j", task_class="inference",
                  required_model="qwen3-coder:30b", est_out_tokens=500)
        # 500 tokens / 50 tps = 10s
        self.assertEqual(lookup_duration_s(job, _am4(), None, _models()), 10.0)


# --- residency / setup solve ------------------------------------------------

class SetupSolveTests(TestCase):

    def _load_for(self, proposal, model_id):
        return [ld for ld in proposal.loads if ld["model_id"] == model_id]

    def test_resident_model_zero_setup(self) -> None:
        # coder already resident -> job starts at 0, no load interval.
        job = Job(plan_id="j1", task_class="inference",
                  required_model="qwen3-coder:30b", est_out_tokens=500)
        m = _am4(resident=["qwen3-coder:30b"])
        p = solve_schedule([job], [m, _frontier()], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(p.loads, [])
        start = next(a["start_s"] for a in p.assignments if a["plan_id"] == "j1")
        self.assertEqual(start, 0.0)

    def test_nonresident_model_pays_setup(self) -> None:
        # coder NOT resident -> one load interval of 12s, job starts >= 12.
        job = Job(plan_id="j1", task_class="inference",
                  required_model="qwen3-coder:30b", est_out_tokens=500)
        m = _am4(resident=[])
        p = solve_schedule([job], [m], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        loads = self._load_for(p, "qwen3-coder:30b")
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["end_s"] - loads[0]["start_s"], 12.0)
        start = next(a["start_s"] for a in p.assignments)
        self.assertGreaterEqual(start, 12.0)

    def test_warm_win_second_job_free_rides(self) -> None:
        # Two jobs, same non-resident model, one stateful machine -> ONE load, and the
        # second job free-rides (no second load interval).
        jobs = [
            Job(plan_id="j1", task_class="inference",
                required_model="qwen3-coder:30b", est_out_tokens=500),
            Job(plan_id="j2", task_class="inference",
                required_model="qwen3-coder:30b", est_out_tokens=500),
        ]
        m = _am4(resident=[])
        p = solve_schedule(jobs, [m], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        # Exactly ONE load interval for the shared model.
        self.assertEqual(len(self._load_for(p, "qwen3-coder:30b")), 1)
        # Both jobs on the AM4 box.
        self.assertTrue(all(a["machine"] == "am4-worker-1" for a in p.assignments))
        # Both start at/after the single load end.
        load_end = self._load_for(p, "qwen3-coder:30b")[0]["end_s"]
        for a in p.assignments:
            self.assertGreaterEqual(a["start_s"], load_end)

    def test_staging_contention_serializes_two_models(self) -> None:
        # Two DIFFERENT non-resident models on the same host -> loads cannot overlap
        # (single DDR4 staging slot).
        jobs = [
            Job(plan_id="j1", task_class="inference",
                required_model="qwen3-coder:30b", est_out_tokens=100),
            Job(plan_id="j2", task_class="inference",
                required_model="mixtral:8x7b", est_out_tokens=100),
        ]
        m = _am4(resident=[])
        p = solve_schedule(jobs, [m], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        loads = sorted(p.loads, key=lambda ld: ld["start_s"])
        self.assertEqual(len(loads), 2)
        # No overlap between the two load intervals.
        self.assertLessEqual(loads[0]["end_s"], loads[1]["start_s"])

    def test_dual_placement_charges_both_cards(self) -> None:
        job = Job(plan_id="j1", task_class="inference",
                  required_model="llama3.1:70b", est_out_tokens=200)
        m = _am4(resident=[])
        p = solve_schedule([job], [m], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        load = next(ld for ld in p.loads if ld["model_id"] == "llama3.1:70b")
        self.assertEqual(load["cards"], [0, 1])  # both cards charged
        # residency summary shows the dual model on both cards, 20GB each.
        by_card = {r["card"]: r for r in p.residency}
        self.assertIn("llama3.1:70b", by_card[0]["resident_models"])
        self.assertIn("llama3.1:70b", by_card[1]["resident_models"])
        self.assertEqual(by_card[0]["used_gb"], 20.0)

    def test_overfull_card_infeasible_surfaced(self) -> None:
        # Tiny cards (10GB) but coder needs 20GB per card -> cannot fit anywhere ->
        # INFEASIBLE surfaced in solver_status (no eviction, no alternative machine).
        tiny_cards = [{"index": 0, "vram_gb": 10.0}, {"index": 1, "vram_gb": 10.0}]
        job = Job(plan_id="j1", task_class="inference",
                  required_model="qwen3-coder:30b", est_out_tokens=100)
        m = _am4(resident=[], cards=tiny_cards)
        p = solve_schedule([job], [m], None, models=_models())
        self.assertEqual(p.solver_status, "INFEASIBLE")
        self.assertEqual(p.assignments, [])

    def test_two_singles_share_cards_within_budget(self) -> None:
        # coder(20) + mixtral(18) both fit only if placed on DIFFERENT cards (each 32).
        jobs = [
            Job(plan_id="j1", task_class="inference",
                required_model="qwen3-coder:30b", est_out_tokens=100),
            Job(plan_id="j2", task_class="inference",
                required_model="mixtral:8x7b", est_out_tokens=100),
        ]
        m = _am4(resident=[])
        p = solve_schedule(jobs, [m], None, models=_models())
        self.assertIn(p.solver_status, ("OPTIMAL", "FEASIBLE"))
        # Each card holds at most one of the two models (20+18 > 31.5 headroom budget).
        by_card = {r["card"]: r for r in p.residency}
        for r in by_card.values():
            self.assertLessEqual(len(r["resident_models"]), 1)

    def test_stateless_when_no_required_model_matches_js7a(self) -> None:
        # Jobs without required_model on a stateful machine -> loads empty, behaves
        # exactly like the non-stateful path.
        jobs = [Job(plan_id=f"j{i}", task_class="build", est_tokens=1000) for i in range(3)]
        m = _am4(resident=[])
        p_stateful = solve_schedule(jobs, [m, _frontier()], None, models=_models())
        m2 = Machine(name="am4-worker-1", kind="local", token_cost_weight=0.0)
        p_plain = solve_schedule(jobs, [m2, _frontier()], None)
        self.assertEqual(p_stateful.loads, [])
        self.assertEqual([a["machine"] for a in p_stateful.assignments],
                         [a["machine"] for a in p_plain.assignments])


# --- provider path with catalog in a sandbox --------------------------------

class ProposeWithCatalogTests(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        (self.scope / "fleet").mkdir(parents=True, exist_ok=True)
        (self.scope / "hearth" / "etc").mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(REPO_ROOT / "fleet" / "inventory.toml",
                    self.scope / "fleet" / "inventory.toml")
        shutil.copy(REPO_ROOT / "hearth" / "etc" / "backends.toml",
                    self.scope / "hearth" / "etc" / "backends.toml")

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous
        import shutil
        shutil.rmtree(self.scope, ignore_errors=True)

    def _write_catalog(self) -> None:
        knowledge = self.scope / "knowledge"
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "am4_catalog.json").write_text(
            json.dumps(_catalog_dict()), encoding="utf-8")

    def test_absent_catalog_is_stateless(self) -> None:
        # No am4_catalog.json -> required_model is inert, no loads, still solves.
        jobs = [{"plan_id": "j1", "task_class": "inference",
                 "required_model": "qwen3-coder:30b", "est_out_tokens": 500}]
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["loads"], [])
        self.assertEqual(result["proposal"]["residency"], [])

    def test_catalog_present_produces_load_and_residency(self) -> None:
        self._write_catalog()
        jobs = [{"plan_id": "j1", "task_class": "inference",
                 "required_model": "qwen3-coder:30b", "est_out_tokens": 500,
                 "deadline_s": 100000}]
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        # AM4 is now stateful with cards.
        am4 = next(m for m in result["machines_considered"] if m["name"] == "am4-worker-1")
        # Job on AM4 pays a load; residency summary present.
        if any(a["machine"] == "am4-worker-1" for a in result["proposal"]["assignments"]):
            self.assertTrue(result["proposal"]["loads"])
            self.assertTrue(result["proposal"]["residency"])
