"""Three-host advisory placement and the shared model-backend bottleneck."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from hearth.scheduler.ontology import Job, Machine, ModelSpec, load_gpu_inventory, lookup_duration_s
from hearth.scheduler.solve import solve_schedule
from hearth.toolsurface.scheduler import propose_schedule


ROOT = Path(__file__).resolve().parents[3]


def _snapshot(age_s: int = 0) -> dict:
    observed_at = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return {
        "omen-arc": {"ready": True, "observed_at": observed_at, "parallel_slots": 8},
        "fx99-ollama": {"ready": True, "gpu_placed": True,
                         "loaded_models": ["qwen2.5-coder:7b"], "observed_at": observed_at},
        "am4-ollama": {"ready": True, "gpu_placed": True,
                        "loaded_models": ["qwen2.5:14b"], "observed_at": observed_at},
    }


class ThreeHostPlacementTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for rel in ("fleet/inventory.toml", "hearth/etc/backends.toml",
                    "knowledge/omen_catalog.json", "knowledge/fx99_gpu_catalog.json",
                    "knowledge/am4_gpu_catalog.json"):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, target)
        am4_path = self.root / "knowledge/am4_gpu_catalog.json"
        am4 = json.loads(am4_path.read_text(encoding="utf-8"))
        am4["models"] = [{
            "model_id": "qwen2.5:14b", "alias": None, "placement": "single",
            "visible_devices": None, "vram_gb": 9.5, "per_card_gb": 9.5,
            "expected_gen_tps": None, "warmup_ms_p50": None,
            "warmup_ms_max": None, "sample_count": 1, "notes": "test fixture"
        }]
        am4_path.write_text(json.dumps(am4), encoding="utf-8")
        scope = mock.patch.dict(os.environ, {"HEARTH_SCOPE": str(self.root)})
        scope.start()
        self.addCleanup(scope.stop)

    def test_declared_gpu_identities_are_on_correct_hosts(self) -> None:
        inventory = load_gpu_inventory(str(self.root / "fleet/inventory.toml"))
        self.assertEqual(len(inventory["omen"]), 2)
        self.assertEqual(inventory["am4"][0]["type"], "nvidia-rtx-5070")
        self.assertEqual(inventory["fx99"][0]["type"], "nvidia-rtx-2070-super")

    def test_each_host_serves_a_real_model_choice(self) -> None:
        jobs = [
            {"plan_id": "plan", "task_class": "inference", "est_duration_s": 10,
             "eligible_models": [{"backend": "omen-arc", "model_id": "qwen3-30b-a3b"}]},
            {"plan_id": "critic", "task_class": "inference", "est_duration_s": 10,
             "eligible_models": [{"backend": "fx99-ollama", "model_id": "qwen2.5-coder:7b"}]},
            {"plan_id": "analysis", "task_class": "inference", "est_duration_s": 10,
             "eligible_models": [{"backend": "am4-ollama", "model_id": "qwen2.5:14b"}]},
        ]
        result = propose_schedule(jobs, resource_snapshot=_snapshot())
        self.assertTrue(result["ok"])
        assignments = {row["backend"]: row["machine"]
                       for row in result["proposal"]["assignments"]}
        self.assertEqual(assignments, {"omen-arc": "omen-inference",
                                       "fx99-ollama": "fx99-inference",
                                       "am4-ollama": "am4-inference"})
        self.assertEqual(result["proposal"]["makespan_s"], 10)

    def test_stale_readiness_cannot_place_work_on_am4(self) -> None:
        result = propose_schedule([
            {"plan_id": "a", "task_class": "inference",
             "eligible_models": [{"backend": "am4-ollama", "model_id": "qwen2.5:14b"}]}
        ], resource_snapshot=_snapshot(age_s=90))
        self.assertFalse(result["ok"])
        self.assertFalse(next(m for m in result["machines_considered"]
                              if m["name"] == "am4-inference")["available"])

    def test_night_epoch_uses_ready_b70_upstream_instead_of_day_model(self) -> None:
        snapshot = _snapshot()
        snapshot["omen-arc"]["ready"] = False
        snapshot["omen-swap"] = {"ready": True,
                                 "loaded_models": ["qwen14b-night", "qwen38-27b-mtp"],
                                 "observed_at": snapshot["omen-arc"]["observed_at"]}
        result = propose_schedule([
            {"plan_id": "night", "task_class": "inference", "est_duration_s": 10,
             "eligible_models": [{"backend": "omen-swap", "model_id": "qwen38-27b-mtp"}]}
        ], resource_snapshot=snapshot)
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["assignments"][0]["backend"], "omen-swap")
        self.assertFalse(result["proposal"]["loads"])
        self.assertEqual(next(m for m in result["machines_considered"]
                              if m["name"] == "omen-inference")["parallel_slots"], 1)

    def test_fx99_builder_with_unqualified_14b_is_not_a_second_gpu_seat(self) -> None:
        inventory_path = self.root / "fleet/inventory.toml"
        inventory = inventory_path.read_text(encoding="utf-8")
        inventory = re.sub(
            r'(name = "cc-builder-3"\nkind = "vm"\nrunner_backend = ")[^"]+("[^\n]*)',
            r'\1fx99-ollama\2', inventory)
        inventory_path.write_text(inventory, encoding="utf-8")
        result = propose_schedule([], resource_snapshot=_snapshot())
        machines = {m["name"]: m for m in result["machines_considered"]}
        self.assertTrue(machines["cc-builder-2"]["available"])
        self.assertFalse(machines["cc-builder-3"]["available"])
        self.assertEqual(machines["cc-builder-3"]["runner_model"], "qwen2.5:14b")
        build = propose_schedule([{"plan_id": "build", "task_class": "build",
                                   "est_duration_s": 10, "est_tokens": 500}],
                                 resource_snapshot=_snapshot())
        self.assertTrue(build["ok"])
        self.assertEqual(build["proposal"]["assignments"][0]["machine"], "cc-builder-2")

    def test_verified_am4_runner_mapping_adds_builder_three_to_the_pool(self) -> None:
        inventory_path = self.root / "fleet/inventory.toml"
        inventory = inventory_path.read_text(encoding="utf-8")
        inventory = re.sub(
            r'(name = "cc-builder-3"\nkind = "vm"\nrunner_backend = ")[^"]+("[^\n]*)',
            r'\1am4-ollama\2', inventory)
        inventory_path.write_text(inventory, encoding="utf-8")
        result = propose_schedule([], resource_snapshot=_snapshot())
        machine = next(m for m in result["machines_considered"]
                       if m["name"] == "cc-builder-3")
        self.assertTrue(machine["available"])
        self.assertEqual(machine["decode_host"], "am4")

    def test_one_gpu_placed_model_does_not_qualify_a_spilled_second_model(self) -> None:
        inventory_path = self.root / "fleet/inventory.toml"
        inventory = inventory_path.read_text(encoding="utf-8")
        inventory_path.write_text(re.sub(
            r'(name = "cc-builder-3"\nkind = "vm"\nrunner_backend = ")[^"]+("[^\n]*)',
            r'\1fx99-ollama\2', inventory), encoding="utf-8")
        catalog_path = self.root / "knowledge/fx99_gpu_catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"].append({
            "model_id": "qwen2.5:14b", "placement": "single", "vram_gb": 9.2,
            "per_card_gb": 9.2, "expected_gen_tps": None, "sample_count": 0,
            "qualified_task_classes": ["research"]})
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        snapshot = _snapshot()
        snapshot["fx99-ollama"]["loaded_models"] = ["qwen2.5-coder:7b", "qwen2.5:14b"]
        snapshot["fx99-ollama"]["gpu_qualified_models"] = ["qwen2.5-coder:7b"]
        result = propose_schedule([
            {"plan_id": "spill", "task_class": "research", "est_duration_s": 10,
             "eligible_models": [{"backend": "fx99-ollama", "model_id": "qwen2.5:14b"}]}
        ], resource_snapshot=snapshot)
        self.assertFalse(result["ok"])
        self.assertFalse(next(m for m in result["machines_considered"]
                              if m["name"] == "cc-builder-3")["available"])

    def test_historical_am4_model_cannot_exempt_an_overfull_omen_load(self) -> None:
        stale = self.root / "knowledge/am4_catalog.json"
        stale.write_text(json.dumps({"contract_version": "am4-catalog.v1",
                                     "models": [{"model_id": "qwen38-27b-mtp"}]}),
                         encoding="utf-8")
        result = propose_schedule([
            {"plan_id": "old", "task_class": "inference",
             "required_model": "qwen38-27b-mtp", "est_duration_s": 10}
        ], am4_catalog_path="knowledge/am4_catalog.json", resource_snapshot=_snapshot())
        self.assertTrue(any(row["plan_id"] == "old" for row in
                            result["rotation_plan"]["blocked"]))


class SharedBackendTests(TestCase):
    def test_two_builder_shells_share_one_fx99_model_slot(self) -> None:
        jobs = [Job("b1", "build", est_duration_s=10),
                Job("b2", "build", est_duration_s=10)]
        machines = [Machine("cc-builder-2", "local", 0.0, backend="fx99-ollama"),
                    Machine("cc-builder-3", "local", 0.0, backend="fx99-ollama")]
        proposal = solve_schedule(jobs, machines, None)
        self.assertEqual(proposal.solver_status, "OPTIMAL")
        self.assertEqual(proposal.makespan_s, 20)

    def test_two_api_names_on_same_gpu_host_share_a_slot(self) -> None:
        jobs = [Job("j1", "build", est_duration_s=10),
                Job("j2", "build", est_duration_s=10)]
        machines = [Machine("vm1", "local", 0.0, backend="ollama-a", decode_host="fx99"),
                    Machine("vm2", "local", 0.0, backend="ollama-b", decode_host="fx99")]
        proposal = solve_schedule(jobs, machines, None)
        self.assertEqual(proposal.solver_status, "OPTIMAL")
        self.assertEqual(proposal.makespan_s, 20)

    def test_measured_omen_slots_allow_parallel_inference(self) -> None:
        jobs = [Job("i1", "inference", est_duration_s=10),
                Job("i2", "inference", est_duration_s=10)]
        machine = Machine("omen-inference", "local", 0.0,
                          backend="omen-arc", parallel_slots=8, roles=["inference"])
        proposal = solve_schedule(jobs, [machine], None)
        self.assertEqual(proposal.solver_status, "OPTIMAL")
        self.assertEqual(proposal.makespan_s, 10)

    def test_parallel_jobs_use_measured_per_request_rate(self) -> None:
        job = Job("i", "inference", required_model="m", est_out_tokens=100)
        spec = ModelSpec("m", expected_gen_tps=100.0, parallel_gen_tps=25.0)
        machine = Machine("omen-inference", "local", 0.0, parallel_slots=8)
        self.assertEqual(lookup_duration_s(job, machine, None, {"m": spec}), 4.0)
        machine.parallel_slots = 1
        self.assertEqual(lookup_duration_s(job, machine, None, {"m": spec}), 1.0)

    def test_quality_evidence_limits_the_flexible_pool_by_task(self) -> None:
        model = ModelSpec("m", qualified_task_classes=["research"])
        machine = Machine("gpu", "local", 0.0, roles=["research", "planning"],
                          stateful=True, resident_models=["m"], loadable_models=["m"],
                          backend="gpu-backend")
        choice = [{"backend": "gpu-backend", "model_id": "m"}]
        rejected = solve_schedule([Job("plan", "planning", eligible_models=choice)],
                                  [machine], None, models={"m": model})
        accepted = solve_schedule([Job("research", "research", eligible_models=choice)],
                                  [machine], None, models={"m": model})
        self.assertEqual(rejected.solver_status, "INFEASIBLE")
        self.assertEqual(accepted.solver_status, "OPTIMAL")

    def test_tool_using_research_uses_a_builder_shell(self) -> None:
        job = Job("source-change", "research", est_duration_s=10, requires_tools=True)
        machines = [Machine("direct", "local", 0.0, roles=["inference", "research"]),
                    Machine("builder", "local", 0.0)]
        proposal = solve_schedule([job], machines, None)
        self.assertEqual(proposal.solver_status, "OPTIMAL")
        self.assertEqual(proposal.assignments[0]["machine"], "builder")
