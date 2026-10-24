"""Unit tests for Tier-0 NPU/AST context slicing and line-number prefix quote hygiene in sourcemap."""
from __future__ import annotations

import unittest

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
    # Add filler methods so the class exceeds 100 lines.
    for idx in range(1, 10):
        lines.append(f"    def unrelated_helper_{idx}(self, value: int) -> int:")
        for step in range(1, 10):
            lines.append(f"        value = (value + {step}) * {idx}")
        lines.append("        return value")
        lines.append("")

    # Target method around line 119+
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


class SourceMapJepaSlicingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = _build_monolithic_class_source()
        self.fmap = sourcemap.file_map("hearth/coordinator.py", self.raw)
        self.smap = sourcemap.SourceMap(
            repo="/tmp/mock-repo",
            commit="a" * 40,
            files=[self.fmap],
        )

    def test_monolithic_class_retains_target_method_and_1based_line_numbers(self) -> None:
        # Ensure class is >100 lines so P0 monolithic class decomposition triggers.
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
        # Class header and target method must both be retained.
        self.assertIn("class MonolithicCoordinator:", rendered)
        self.assertIn("def reconcile_quantum_lease(", rendered)
        self.assertIn("HEARTH_QUANTUM_LEASE_MODE", rendered)
        # Module-level constant table referenced in intent/brief must be retained.
        self.assertIn("DISPATCH_ROUTES = {", rendered)
        # Unrelated helpers must be pruned with elision markers.
        self.assertNotIn("def unrelated_helper_5(", rendered)
        self.assertIn("JEPA-AST-SLICE omitted lines", rendered)

        # Verify 1-based line numbers in rendered output match exact source lines.
        source_lines = self.raw.decode("utf-8").splitlines()
        expected_line_no = next(
            i + 1 for i, line in enumerate(source_lines) if "def reconcile_quantum_lease(" in line
        )
        width = max(3, len(str(len(self.fmap.lines))))
        self.assertIn(f"{expected_line_no:0{width}d}|     def reconcile_quantum_lease(", rendered)

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

        # Single-line quote with `0042| ` prefix from numbered source map.
        prefixed_quote = f"{line_no:04d}| {raw_line}"
        loc = sourcemap.locate(self.smap, prefixed_quote)
        self.assertTrue(loc.resolved)
        self.assertEqual(loc.path, "hearth/coordinator.py")
        self.assertEqual(loc.match, "normalized")
        self.assertEqual(loc.start, line_no)
        self.assertEqual(loc.end, line_no)
        self.assertTrue(loc.repaired)

        # Multi-line quote with `0042| ` prefixes on each line.
        next_line = source_lines[target_idx + 1]
        multiline_prefixed = f"{line_no:04d}| {raw_line}\n{line_no + 1:04d}| {next_line}"
        loc_multi = sourcemap.locate(self.smap, multiline_prefixed)
        self.assertTrue(loc_multi.resolved)
        self.assertEqual(loc_multi.path, "hearth/coordinator.py")
        self.assertEqual(loc_multi.match, "normalized")
        self.assertEqual(loc_multi.start, line_no)
        self.assertEqual(loc_multi.end, line_no + 1)


if __name__ == "__main__":
    unittest.main()
