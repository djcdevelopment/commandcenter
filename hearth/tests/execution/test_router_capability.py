"""Tests for router capability awareness and planning-prompt routing (Task 10)."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from hearth.execution.capabilities import get_capability_slice, load_backend_capability
from hearth.execution.defaults import get_execution_service
from hearth.execution.lab_config import (
    get_active_configuration_name,
    get_backend_status,
    is_backend_absent,
    is_backend_live,
    load_lab_configurations,
)


class RouterCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        # The repository's tracked copies of the host configuration, never ~/hearth-production: the tests mean the
        # same thing on any machine and do not drift with the live host files.
        repo = Path(__file__).resolve().parents[3]
        host = repo / "host" / "omen-linux" / "hearth-production"
        env = {"HEARTH_BACKENDS": str(host / "backends-linux.toml"),
               "HEARTH_ROUTING_FAMILIES": str(host / "routing-families-linux.toml"),
               "HEARTH_LAB_CONFIGURATIONS": str(repo / "host" / "lab-configurations.toml"),
               "HEARTH_LAB_CONFIGURATION": "day"}
        for path in list(env.values())[:3]:
            self.assertTrue(Path(path).is_file(), path)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_planning_prompt_depth_override_routes_to_dense(self) -> None:
        """AC 1: The reproduced planning prompt, unpinned, plans to dense lane; routed_by says why."""
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=48000,
            policy={"max_tokens": 12000},
            task_family="reasoning_planning",
        )
        self.assertEqual(plan["provider"], "omen-dense-27b")
        self.assertEqual(plan["model"], "qwen3.8-27b")
        self.assertEqual(plan["routed_by"], "family:reasoning_planning:depth_override:omen-dense-27b")
        self.assertFalse(plan["dispatch"])

    def test_vision_families_require_vision_configuration(self) -> None:
        svc = get_execution_service()
        for family in ("chart_diagram", "screenshot_grounded"):
            with self.subTest(family=family, configuration="day"):
                os.environ["HEARTH_LAB_CONFIGURATION"] = "day"
                plan = svc.plan(operation_name="inference.generate", prompt_bytes=1000,
                                task_family=family)
                self.assertEqual(plan["error_code"], "policy_refusal")
                self.assertIn("seat0-27b-vision", plan["refusal"])
            with self.subTest(family=family, configuration="seat0-27b-vision"):
                os.environ["HEARTH_LAB_CONFIGURATION"] = "seat0-27b-vision"
                plan = svc.plan(operation_name="inference.generate", prompt_bytes=1000,
                                task_family=family)
                self.assertEqual(plan["provider"], "omen-dense-27b")
                self.assertEqual(plan["routed_by"], f"family:{family}:tag:vision")

    def test_planning_prompt_shallow_routes_to_moe(self) -> None:
        """Shallow reasoning_planning below 8192 tokens preserves the MoE door default route."""
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=1000,
            policy={"max_tokens": 1000},
            task_family="reasoning_planning",
        )
        self.assertEqual(plan["provider"], "omen-vllm")
        self.assertEqual(plan["model"], "qwen3-30b-a3b")
        self.assertEqual(plan["routed_by"], "family:reasoning_planning:tag:reasoning")

    def test_plan_execution_includes_capability_slice(self) -> None:
        """AC 2: plan_execution output includes the capability slice for the chosen backend."""
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=48000,
            policy={"max_tokens": 12000},
            task_family="reasoning_planning",
        )
        cap = plan.get("capability")
        self.assertIsNotNone(cap)
        self.assertEqual(cap["backend"], "omen-dense-27b")
        self.assertEqual(cap["model"], "qwen3.8-27b")
        self.assertEqual(cap["context_tokens"], 65536)
        self.assertEqual(cap["parallel_slots"], 2)
        self.assertEqual(cap["max_tokens"], 16384)
        self.assertGreater(cap["decode_tok_per_s"], 0)
        self.assertGreater(cap["prefill_tok_per_s"], 0)
        self.assertGreater(cap["cold_load_s"], 0)
        self.assertEqual(cap["task_family"], "reasoning_planning")
        self.assertEqual(cap["outcome"], "depth_inversion_faster")
        self.assertEqual(cap["evidence_status"], "useful_supported")

    def test_tool_execution_under_dense_tp2_resolves_to_default_and_reports_not_live(self) -> None:
        """AC 3: memsplice is the dense-tp2 shape (AM4 runs the 27B, tool seats absent): tool_execution resolves per
        documented default and says tool-use lane is not live. `day` is no longer this shape (AM4 on tool-pair)."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "memsplice"
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=1000,
            task_family="tool_execution",
        )
        self.assertEqual(plan["provider"], "omen-vllm")
        self.assertEqual(plan["model"], "qwen3-30b-a3b")
        self.assertIn("lane_not_live", plan["routed_by"])
        self.assertEqual(plan.get("lane_status"), "lane_not_live")
        self.assertIn("tool-use lane is not live", plan.get("note", ""))

    def test_tool_execution_under_day_routes_to_tool_seat(self) -> None:
        """`tool-night` is gone; `day` has AM4 on tool-pair, so tool_execution routes to the live am4-tool-4070ti seat."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "day"
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=1000,
            task_family="tool_execution",
        )
        self.assertEqual(plan["provider"], "am4-tool-4070ti")
        self.assertEqual(plan["model"], "am4-tool-4070ti")
        self.assertEqual(plan["routed_by"], "family:tool_execution:tag:tool-use")
        cap = plan.get("capability")
        self.assertIsNotNone(cap)
        self.assertEqual(cap["outcome"], "shape_accepted")

    def test_absent_backend_admission_refusal_names_configuration(self) -> None:
        """Step 4: Admission refuses an absent backend explicitly naming the configuration. Under `day` the absent
        AM4 seat is am4-vllm (the dense-tp2 server); the tool seats are live."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "day"
        svc = get_execution_service()
        plan = svc.plan(
            operation_name="inference.generate",
            prompt_bytes=1000,
            backend="am4-vllm",
        )
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["routed_by"], "policy_refusal")
        self.assertIn("marked absent under active configuration 'day'", plan["refusal"])

    def test_lab_config_queries(self) -> None:
        """Test lab_config module functions."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "day"
        self.assertEqual(get_active_configuration_name(), "day")
        self.assertTrue(is_backend_live("omen-dense-27b"))
        self.assertTrue(is_backend_live("am4-tool-4070ti"))
        self.assertTrue(is_backend_absent("am4-vllm"))
        status, cfg = get_backend_status("am4-vllm")
        self.assertEqual(status, "absent")
        self.assertEqual(cfg, "day")
        self.assertTrue(is_backend_absent("am4-tool-4070ti", "memsplice"))
        self.assertTrue(is_backend_live("am4-vllm", "memsplice"))
        configs = load_lab_configurations()
        self.assertEqual((configs["day"]["am4_profile"], configs["memsplice"]["am4_profile"]), ("tool-pair", "dense-tp2"))
        self.assertNotIn("tool-night", configs)

    def test_an_unknown_configuration_reads_unknown_not_live(self) -> None:
        """W11 (2026-10-03): the old `tool-night` test still passed on 91687b6 after `tool-night` was deleted from
        host/lab-configurations.toml. lab_config itself keeps the difference: under a name it does not know, no
        backend reads live and none reads absent."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "tool-night"
        for bname in ("am4-tool-4070ti", "am4-vllm", "omen-dense-27b"):
            with self.subTest(backend=bname):
                self.assertEqual(get_backend_status(bname), ("unknown", "tool-night"))
                self.assertFalse(is_backend_live(bname))
                self.assertFalse(is_backend_absent(bname))

    @unittest.expectedFailure
    def test_the_router_does_not_treat_an_unknown_configuration_as_everything_live(self) -> None:
        """W11 (2026-10-03), the same observation one layer up. Desired: a plan under a configuration name the lab
        file does not define says so (refusal or note naming it). Today the router skips the lane check for status
        `unknown` and plans tool_execution onto am4-tool-4070ti as if it were live; no non-test code changes here."""
        os.environ["HEARTH_LAB_CONFIGURATION"] = "tool-night"
        plan = get_execution_service().plan(operation_name="inference.generate", prompt_bytes=1000,
                                            task_family="tool_execution")
        said = " ".join(str(plan.get(k) or "") for k in ("refusal", "note", "routed_by", "lane_status"))
        self.assertIn("tool-night", said)

    def test_capabilities_loader_integrity(self) -> None:
        """Test backend capability loading for all defined backends."""
        for bname in ("omen-dense-27b", "omen-vllm", "am4-tool-4070ti", "am4-tool-5070", "am4-vllm", "fx99-vllm"):
            cap = load_backend_capability(bname)
            self.assertIsNotNone(cap, f"capability record missing for {bname}")
            self.assertEqual(cap["backend"], bname)
            self.assertIn("context_tokens", cap)
            self.assertIn("parallel_slots", cap)
            self.assertIn("max_tokens", cap)
            self.assertIn("decode_tok_per_s", cap)
            self.assertIn("prefill_tok_per_s", cap)
            self.assertIn("cold_load_s", cap)


if __name__ == "__main__":
    unittest.main()
