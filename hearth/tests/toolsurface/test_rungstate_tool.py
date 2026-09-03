"""hearth.toolsurface.rungstate (P7b): the query_rung_state provider — contract,
kernel-freedom, passthrough of the ADR-0044 verdict, never raises."""
from __future__ import annotations

import ast
import inspect
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface import rungstate as R
from hearth.tests.toolsurface.test_provider_contract import PROVIDERS

# Built by concatenation so this test file never carries the dotted name itself.
_KERNEL = "hearth" + ".kernel"


def _rung(verdict, **over):
    st = {"rung": "omen-arc", "port": 8082, "verdict": verdict,
          "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
          "envelope": {"fail_below": 0.8, "warn_below": 0.9},
          "observed_tok_s": 107.5, "observed_at": "2026-09-03T02:28:20-07:00",
          "observed_age_s": 100.0, "frac_of_baseline": 1.0142,
          "prefill_stall_recent": False, "last_ping_ok": True, "deep_samples": 3,
          "excluded_windows": [], "note": "envelope is of THIS baseline epoch, not of capacity"}
    st.update(over)
    return st


class ProviderContractTests(TestCase):
    def test_get_tools_exposes_query_rung_state(self) -> None:
        tools = R.get_tools()
        self.assertIsInstance(tools, list)
        self.assertEqual([t.__name__ for t in tools], ["query_rung_state"])

    def test_tool_is_typed_and_documented(self) -> None:
        for tool in R.get_tools():
            self.assertTrue((tool.__doc__ or "").strip())
            sig = inspect.signature(tool)
            for name, p in sig.parameters.items():
                self.assertIsNot(p.annotation, inspect.Parameter.empty, name)
            self.assertIsNot(sig.return_annotation, inspect.Signature.empty)
        sig = inspect.signature(R.query_rung_state)
        self.assertEqual(sig.parameters["rung"].default, "omen-arc")
        self.assertIn(sig.parameters["rung"].annotation, (str, "str"))  # postponed annotations are strings

    def test_module_is_kernel_free_by_source_and_by_import_graph(self) -> None:
        source = inspect.getsource(R)
        self.assertNotIn(_KERNEL, source)
        imported = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertEqual([m for m in imported if m.startswith(_KERNEL)], [])
        # The health module it leans on is kernel-free too (the contract's
        # laundering-route check, applied one hop down).
        from hearth.health import rungstate as H
        self.assertNotIn(_KERNEL + " import", inspect.getsource(H))

    def test_tool_name_does_not_collide_with_the_registered_surface(self) -> None:
        # P10 registered the provider: the name must appear exactly once across the surface.
        names = [t.__name__ for m in PROVIDERS for t in m.get_tools()]
        self.assertEqual(names.count("query_rung_state"), 1)


class QueryRungStateTests(TestCase):
    def test_passes_the_verdict_through_with_ok_and_summary(self) -> None:
        degraded = _rung("degraded", observed_tok_s=65.0, frac_of_baseline=0.6132)
        with patch("hearth.toolsurface.rungstate.live_rung_state", return_value=degraded) as reader:
            out = R.query_rung_state()
        reader.assert_called_once_with("omen-arc")
        self.assertTrue(out["ok"])
        self.assertEqual(out["verdict"], "degraded")
        self.assertEqual(out["observed_tok_s"], 65.0)
        self.assertEqual(out["frac_of_baseline"], 0.6132)
        self.assertLessEqual(len(out["summary"]), 96)
        self.assertNotIn(";", out["summary"])
        self.assertIn("omen-arc degraded 65.0/106.0 tok/s", out["summary"])
        self.assertIn("not of capacity", out["note"])

    def test_rung_argument_is_forwarded(self) -> None:
        with patch("hearth.toolsurface.rungstate.live_rung_state",
                   return_value=_rung("no_baseline", rung="omen-swap", port=8081,
                                      baseline_tok_s=None)) as reader:
            out = R.query_rung_state(rung="omen-swap")
        reader.assert_called_once_with("omen-swap")
        self.assertTrue(out["ok"])
        self.assertEqual(out["verdict"], "no_baseline")
        self.assertEqual(out["rung"], "omen-swap")

    def test_unreachable_is_ok_true_with_the_verdict_saying_down(self) -> None:
        # ok = the read succeeded; the verdict carries the bad news.
        with patch("hearth.toolsurface.rungstate.live_rung_state",
                   return_value=_rung("unreachable", last_ping_ok=False)):
            out = R.query_rung_state()
        self.assertTrue(out["ok"])
        self.assertEqual(out["verdict"], "unreachable")

    def test_reader_error_shape_is_ok_false(self) -> None:
        with patch("hearth.toolsurface.rungstate.live_rung_state",
                   return_value={"rung": "omen-arc", "port": None, "verdict": "unknown",
                                 "error": "ValueError: bad json", "note": "n"}):
            out = R.query_rung_state()
        self.assertFalse(out["ok"])
        self.assertEqual(out["verdict"], "unknown")
        self.assertIn("ValueError", out["error"])

    def test_reader_raising_never_escapes(self) -> None:
        with patch("hearth.toolsurface.rungstate.live_rung_state", side_effect=OSError("boom")):
            out = R.query_rung_state()
        self.assertFalse(out["ok"])
        self.assertEqual(out["verdict"], "unknown")
        self.assertIn("OSError", out["error"])
        self.assertIn("unknown", out["summary"])

    def test_live_read_returns_a_known_verdict_and_never_raises(self) -> None:
        # Against the real files: whatever the rung is doing right now, the
        # answer is one of the eight verdicts and the shape is complete.
        out = R.query_rung_state()
        self.assertIn(out["verdict"], R.VERDICTS)
        for key in ("ok", "rung", "verdict", "note", "summary"):
            self.assertIn(key, out)
        self.assertEqual(out["rung"], "omen-arc")
