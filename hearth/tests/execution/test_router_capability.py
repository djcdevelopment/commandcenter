"""Tests for router capability awareness and planning-prompt routing (Task 10)."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from hearth.execution.capabilities import get_capability_slice, load_backend_capability
from hearth.execution.defaults import get_execution_service
from hearth.execution.lab_config import (
    get_active_configuration_name,
    get_backend_status,
    is_backend_absent,
    is_backend_live,
)


class RouterCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_backends = os.environ.get("HEARTH_BACKENDS")
        self.old_families = os.environ.get("HEARTH_ROUTING_FAMILIES")
        self.old_config = os.environ.get("HEARTH_LAB_CONFIGURATION")

        repo = Path(__file__).resolve().parents[3]
        prod_backends = Path.home() / "hearth-production" / "backends-linux.toml"
        prod_families = Path.home() / "hearth-production" / "routing-families-linux.toml"

        if prod_backends.is_file():
            os.environ["HEARTH_BACKENDS"] = str(prod_backends)
        if prod_families.is_file():
            os.environ["HEARTH_ROUTING_FAMILIES"] = str(prod_families)
        os.environ["HEARTH_LAB_CONFIGURATION"] = "day"

    def tearDown(self) -> None:
        if self.old_backends is not None:
            os.environ["HEARTH_BACKENDS"] = self.old_backends
        else:
            os.environ.pop("HEARTH_BACKENDS", None)

        if self.old_families is not None:
            os.environ["HEARTH_ROUTING_FAMILIES"] = self.old_families
        else:
            os.environ.pop("HEARTH_ROUTING_FAMILIES", None)

        if self.old_config is not None:
            os.environ["HEARTH_LAB_CONFIGURATION"] = self.old_config
        else:
            os.environ.pop("HEARTH_LAB_CONFIGURATION", None)

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
