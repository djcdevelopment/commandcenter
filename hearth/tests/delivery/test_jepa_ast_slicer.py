"""Unit tests for Tier-0 NPU/AST context slicing, /dev/shm arena caching, and line-prefix quote hygiene."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.delivery import sourcemap


def _build_monolithic_class_source() -> bytes:
    lines = [
        '"""Module docstring for monolithic service test."""',
        "from __future__ import annotations",
        "import os",
        "",
        "DISPATCH_ROUTES = {",
        '    "alpha": "handle_alpha",',
        '    "beta": "handle_beta",',
        '    "gamma": "handle_gamma",',
        '    "delta": "handle_delta",',
        '    "epsilon": "handle_epsilon",',
        "}",
        "",
        "class MonolithicCoordinator:",
        '    """A >100-line class exercising P0 method-level AST slicing."""',
        "",
        "    def __init__(self, root: str) -> None:",
        "        self.root = root",
        "",
    ]
    for idx in range(1, 10):
        lines.append(f"    def unrelated_helper_{idx}(self, value: int) -> int:")
        for step in range(1, 10):
            lines.append(f"        value = (value + {step}) * {idx}")
        lines.append("        return value")
        lines.append("")

    lines.extend([
        "    def reconcile_quantum_lease(self, lease_id: str, timeout_s: int = 30) -> dict:",
        '        """Reconcile an active quantum lease against the coordinator ledger."""',
        '        env_flag = os.environ.get("HEARTH_QUANTUM_LEASE_MODE", "strict")',
        "        if timeout_s <= 0:",
        '            raise ValueError("timeout_s must be positive")',
        '        return {"lease_id": lease_id, "mode": env_flag, "timeout_s": timeout_s}',
        "",
    ])

    for idx in range(10, 14):
        lines.append(f"    def unrelated_helper_{idx}(self, value: int) -> int:")
        for step in range(1, 8):
            lines.append(f"        value = (value - {step}) * {idx}")
        lines.append("        return value")
        lines.append("")

    return ("\n".join(lines) + "\n").encode("utf-8")


class JepaAstSlicerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = _build_monolithic_class_source()
        self.fmap = sourcemap.file_map("hearth/coordinator.py", self.raw)
        self.smap = sourcemap.SourceMap(
            repo="/tmp/mock-repo",
            commit="a" * 40,
            files=[self.fmap],
        )

    def test_monolithic_class_retains_target_method_and_1based_line_numbers(self) -> None:
        self.assertGreater(len(self.fmap.lines), 120)

        rendered, stats = sourcemap.render_jepa_ast_sliced_for_model(
            self.smap,
            intent="Inspect reconcile_quantum_lease and DISPATCH_ROUTES in MonolithicCoordinator",
            brief={
                "substance": [
                    {
                        "id": "s1",
                        "statement": "Explain how reconcile_quantum_lease reads HEARTH_QUANTUM_LEASE_MODE and how DISPATCH_ROUTES maps handlers.",
                    }
                ]
            },
            numbered=True,
            symbols=False,
        )

        self.assertLess(stats["sliced_chars"], stats["full_chars"])
        self.assertGreater(stats["token_reduction_pct"], 25.0)
        self.assertIn("class MonolithicCoordinator:", rendered)
        self.assertIn("def reconcile_quantum_lease(", rendered)
        self.assertIn("HEARTH_QUANTUM_LEASE_MODE", rendered)
        self.assertIn("DISPATCH_ROUTES = {", rendered)
        self.assertNotIn("def unrelated_helper_5(", rendered)
        self.assertIn("JEPA-AST-SLICE omitted lines", rendered)

        source_lines = self.raw.decode("utf-8").splitlines()
        expected_line_no = next(
            i + 1 for i, line in enumerate(source_lines) if "def reconcile_quantum_lease(" in line
        )
        width = max(3, len(str(len(self.fmap.lines))))
        self.assertIn(f"{expected_line_no:0{width}d}|     def reconcile_quantum_lease(", rendered)

    def test_shm_ast_arena_cold_populate_and_warm_bypass(self) -> None:
        with tempfile.TemporaryDirectory(dir="/dev/shm" if Path("/dev/shm").is_dir() else None) as tmp_arena:
            with mock.patch.dict(os.environ, {"MECHSUIT_AST_ARENA_DIR": tmp_arena}):
                rendered_cold, stats_cold = sourcemap.render_jepa_ast_sliced_for_model(
                    self.smap,
                    intent="Inspect reconcile_quantum_lease and DISPATCH_ROUTES in MonolithicCoordinator",
                    numbered=True,
                )
                self.assertEqual(stats_cold["shm_arena_misses"], 1)
                self.assertEqual(stats_cold["shm_arena_hits"], 0)
                meta_file = Path(tmp_arena) / f"sourcemap_sha256_{self.fmap.sha256[:24]}.meta.json"
                self.assertTrue(meta_file.is_file())

                with mock.patch.object(
                    sourcemap,
                    "_jepa_slice_symbols",
                    side_effect=AssertionError("Warm /dev/shm hit should bypass _jepa_slice_symbols"),
                ):
                    rendered_warm, stats_warm = sourcemap.render_jepa_ast_sliced_for_model(
                        self.smap,
                        intent="Inspect reconcile_quantum_lease and DISPATCH_ROUTES in MonolithicCoordinator",
                        numbered=True,
                    )
                self.assertEqual(stats_warm["shm_arena_hits"], 1)
                self.assertEqual(stats_warm["shm_arena_misses"], 0)
                self.assertEqual(rendered_cold, rendered_warm)

    def test_route_boost_and_free_constants_retain_numbered_handler(self):
        source = ["from fastapi import FastAPI", "app = FastAPI()"]
        source += [f"FLAG_{i} = True" for i in range(8)]
        for i in range(9):
            source += [f"def filler_{i}():", "    return 0"] + [""] * 9
        source += ['@app.post("/api/work-orders")', "def create_work_order():", "    return 42"]
        fm = sourcemap.file_map("app/main.py", ("\n".join(source) + "\n").encode())
        sm = sourcemap.SourceMap(repo="/tmp/offline", commit="a" * 40, files=[fm])
        constants_first = lambda state, intent, texts, labels, **kwargs: (
            {label: (10.0 if label.startswith("FLAG_") else 0.0) for label in labels}, "symbolic_ast_fallback", 0.0)
        with tempfile.TemporaryDirectory() as arena, mock.patch.dict(os.environ, {"MECHSUIT_AST_ARENA_DIR": arena}), \
             mock.patch.object(sourcemap, "_classify_regime_via_jepa", return_value=("SLICE_SAFE", 0.0)), \
             mock.patch.object(sourcemap, "_rank_ast_blocks_via_jepa", side_effect=constants_first):
            old, _ = sourcemap.render_jepa_ast_sliced_for_model(sm, intent="POST /api/work-orders", max_symbols_per_file=1)
            self.assertNotIn("def create_work_order", old)
            rendered, stats = sourcemap.render_jepa_ast_sliced_for_model(sm, intent="POST /api/work-orders",
                max_symbols_per_file=1, route_boost=2.0, count_consts_against_cap=False)
        self.assertIn("def create_work_order", rendered)
        self.assertIn("FLAG_7 = True", rendered)
        line = source.index("def create_work_order():") + 1
        self.assertIn(f"{line:03d}| def create_work_order():", rendered)
        self.assertEqual(stats["route_boost"], 2.0)
        self.assertFalse(stats["count_consts_against_cap"])

    def test_whole_file_guard_bypasses_aggressive_slicing(self) -> None:
        rendered, stats = sourcemap.render_jepa_ast_sliced_for_model(
            self.smap,
            intent="Perform a complete whole-file audit of every line in hearth/coordinator.py",
            numbered=True,
            symbols=False,
        )
        self.assertEqual(stats["jepa_mode"], "whole_file_guard_bypass")
        self.assertEqual(stats["sliced_chars"], stats["full_chars"])
        self.assertIn("def unrelated_helper_5(", rendered)

    def test_locate_strips_line_number_prefix_as_normalized_match(self) -> None:
        source_lines = self.raw.decode("utf-8").splitlines()
        target_idx = next(
            i for i, line in enumerate(source_lines) if "HEARTH_QUANTUM_LEASE_MODE" in line
        )
        line_no = target_idx + 1
        raw_line = source_lines[target_idx]

        prefixed_quote = f"{line_no:04d}| {raw_line}"
        loc = sourcemap.locate(self.smap, prefixed_quote)
        self.assertTrue(loc.resolved)
        self.assertEqual(loc.path, "hearth/coordinator.py")
        self.assertEqual(loc.match, "normalized")
        self.assertEqual(loc.start, line_no)
        self.assertEqual(loc.end, line_no)
        self.assertTrue(loc.repaired)

    def test_csharp_harmony_patch_symbol_decomposition(self):
        cs_code = "\n".join([
            "using BepInEx;",
            "using HarmonyLib;",
            "",
            "[BepInPlugin(\"com.test.plugin\", \"Test\", \"1.0.0\")]",
            "public sealed class TestPlugin : BaseUnityPlugin",
            "{",
            "    private void Awake()",
            "    {",
            *[f"        int x{i} = {i};" for i in range(65)],
            "    }",
            "}",
            "",
            "[HarmonyPatch(typeof(Player), nameof(Player.GetMaxCarryWeight))]",
            "public static class CarryWeightPatch",
            "{",
            "    [HarmonyPostfix]",
            "    public static void Postfix(ref float __result)",
            "    {",
            "        __result += 150f;",
            "    }",
            "}",
        ]).encode("utf-8")
        fm = sourcemap.file_map("TestPlugin.cs", cs_code)
        self.assertEqual(fm.symbols_from, "ast")
        slice_syms = sourcemap._jepa_slice_symbols(fm)
        names_and_kinds = [(s["name"], s["kind"]) for s in slice_syms]
        self.assertIn(("TestPlugin", "class_header"), names_and_kinds)
        self.assertIn(("TestPlugin.Awake", "method"), names_and_kinds)
        self.assertIn(("CarryWeightPatch", "class"), names_and_kinds)
        cw = next(s for s in slice_syms if s["name"] == "CarryWeightPatch")
        self.assertIn("[HarmonyPatch", fm.lines[cw["start"] - 1])


if __name__ == "__main__":
    unittest.main()
