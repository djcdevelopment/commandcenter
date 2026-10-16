"""Suspected-incomplete text (audit 2026-10-16, first increment step 3): signals with evidence, never a repair.

The unit cases are the four cuts the cap-3 night produced and eight legitimate endings; the render cases use valid
delivery-output.v1 documents; the replays render the three stored night outputs (fixtures/incomplete/README.md).
The AM4 candidate work_8693ecbf wrote the unit cases; the frontier wrote the rest against the contract.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from hearth.delivery import contract
from hearth.delivery.render import render_with_findings, suspected_incomplete
from hearth.delivery.sourcemap import SourceMap, file_map

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "incomplete"
COMMIT = "0" * 40
SOURCE = b"def f():\n    return 1\n"


def brief_bytes() -> bytes:
    return json.dumps({"schema": "brief.v2", "builders": ["omen-local-work"], "requires": ["candidate"],
                       "sources": [{"path": "source.txt", "commit": COMMIT}],
                       "form": {"citations": "quote", "words": {"min": 1, "max": 10000, "enforce": "measure"}},
                       "substance": [{"id": "s1", "statement": "says something"}]}).encode("utf-8")


def output(*texts: str) -> dict:
    return {"summary": "A complete summary.",
            "sections": [{"heading": "One", "paragraphs": [{"text": t, "quotes": ["return 1"]} for t in texts]}]}


class SuspectedIncompleteUnitTests(unittest.TestCase):
    def test_named_signals(self) -> None:
        cases = {"It first transmits the rendered brief by calling bash ": "trailing_space",
                 "the code records a failure by setting receipt.finish_row(row, outcome=": "open_token",
                 "comparing expected from fe.get(": "open_token",
                 "keeps a count, as shown by if": "dangling_word"}
        for text, signal in cases.items():
            with self.subTest(text=text):
                row = suspected_incomplete(text)
                self.assertIsNotNone(row)
                self.assertEqual(row["signal"], signal)
                self.assertLessEqual(len(row["tail"]), 40)
                self.assertTrue(text.endswith(row["tail"]))

    def test_complete_and_empty_texts_return_none(self) -> None:
        for text in ("The allowed values are:", "the default is deep=3.", 'writes "row refused" and exits.', "No.",
                     "see the table (lines 12-14)", "the value is `deep=3`", "", "   ",
                     "The function returns (status, configuration).", "Fields: a, b and c."):
            with self.subTest(text=text):
                self.assertIsNone(suspected_incomplete(text))


class RenderIncompleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = SourceMap("fixture", COMMIT, [file_map("source.txt", SOURCE)])

    def test_cut_paragraph_is_a_finding_with_evidence_and_holds(self) -> None:
        out = output("It first transmits the rendered brief by calling bash ", "A complete paragraph.")
        contract.check_output(out)
        _, man, findings = render_with_findings(out, brief_bytes(), self.sm)
        self.assertEqual([r["id"] for r in man["suspected_incomplete"]], ["s0.p0"])
        row = man["suspected_incomplete"][0]
        self.assertEqual(row["signal"], "trailing_space")
        self.assertLessEqual(len(row["tail"]), 40)
        self.assertTrue(man["hold_for_review"])
        self.assertTrue(any(f.startswith("s0.p0: suspected incomplete text") for f in findings))
        self.assertIn("s0.p0: suspected incomplete text", man["verification"]["deterministic"]["note"])
        self.assertEqual(contract.validate_manifest(man), [])

    def test_complete_paragraphs_leave_no_finding(self) -> None:
        out = output("A complete paragraph.", "Another one, with a list: a, b and c.")
        _, man, findings = render_with_findings(out, brief_bytes(), self.sm)
        self.assertEqual(man["suspected_incomplete"], [])
        self.assertFalse(man["hold_for_review"])
        self.assertFalse(any("suspected incomplete" in f for f in findings))
        self.assertEqual(contract.validate_manifest(man), [])

    def test_finding_never_changes_the_state_rule(self) -> None:
        """Resolved quotes plus one cut paragraph: deterministic stays pass; the finding is in the note only."""
        out = output("The function returns 1 by calling ")
        _, man, _ = render_with_findings(out, brief_bytes(), self.sm)
        self.assertEqual(man["verification"]["deterministic"]["state"], "pass")
        self.assertTrue(man["hold_for_review"])
        self.assertNotIn("suspected_incomplete", man["repairs"])

    def test_contract_refuses_inconsistent_hold_flag(self) -> None:
        out = output("A complete paragraph.")
        _, man, _ = render_with_findings(out, brief_bytes(), self.sm)
        man["hold_for_review"] = True
        self.assertTrue(any("hold_for_review" in e for e in contract.validate_manifest(man)))

    def test_quote_only_cut_has_pinned_boundary_evidence(self) -> None:
        sm = SourceMap("fixture", COMMIT, [file_map("source.txt", b'bash "$S/bin/lane.sh" send "$brief"\n')])
        out = output("The lane sends the brief.")
        out["sections"][0]["paragraphs"][0]["quotes"] = ["bash "]
        _, man, _ = render_with_findings(out, brief_bytes(), sm)
        self.assertEqual(man["suspected_incomplete"],
                         [{"id": "s0.p0.q0", "signal": "quote_before_string", "tail": "bash "}])
        self.assertTrue(man["hold_for_review"])
        self.assertEqual(man["verification"]["deterministic"]["state"], "pass")
        self.assertEqual(contract.validate_manifest(man), [])

    def test_valid_partial_quote_is_not_judged_as_incomplete_prose(self) -> None:
        sm = SourceMap("fixture", COMMIT, [file_map("source.txt", b'return (1 + 2)\nbash "$script"\n'
                                                   b'out = {\n "a": "b", "c": "d"\n}\n')])
        for quote in ("return (", "return (1 + 2)\n", 'bash "$script"', '"a": "b",', 'out = {'):
            with self.subTest(quote=quote):
                out = output("A complete description.")
                out["sections"][0]["paragraphs"][0]["quotes"] = [quote]
                _, man, _ = render_with_findings(out, brief_bytes(), sm)
                self.assertEqual(man["suspected_incomplete"], [])

    def test_quote_boundary_follows_resolved_range_not_another_source(self) -> None:
        sm = SourceMap("fixture", COMMIT, [
            file_map("a.py", b'def alpha():\n    result = function_call_with_name(argument)\n'),
            file_map("b.py", b'def beta():\n    result = function_call_with_name("argument")\n')])
        out = output("The alpha function calls the helper.")
        out["sections"][0]["paragraphs"][0]["quotes"] = ["result = function_call_with_name("]
        _, man, _ = render_with_findings(out, brief_bytes(), sm)
        self.assertEqual(man["claims"][0]["resolved"]["path"], "a.py")
        self.assertEqual(man["suspected_incomplete"], [])


class ReplayIncompleteTests(unittest.TestCase):
    EXPECT = {"a83187bb": "trailing_space", "25fd75d0": "open_token", "921d1a59": "open_token"}

    def _replay(self, name: str):
        out_path, brief_path, src_path = (FIXTURES / f"{name}.{ext}" for ext in ("output.json", "brief.json", "source.txt"))
        if not (out_path.exists() and brief_path.exists() and src_path.exists()):
            self.skipTest(f"fixture {name} not present")
        sm = SourceMap("fixture", COMMIT, [file_map("source.txt", src_path.read_bytes())])
        out = json.loads(out_path.read_text(encoding="utf-8"))
        return render_with_findings(out, brief_path.read_bytes(), sm)

    def test_a83187bb_night02_trailing_space(self) -> None:
        _, man, _ = self._replay("a83187bb")
        self.assertIn("trailing_space", [r["signal"] for r in man["suspected_incomplete"]])
        self.assertTrue(man["hold_for_review"])

    def test_25fd75d0_night04_open_token(self) -> None:
        _, man, _ = self._replay("25fd75d0")
        self.assertIn("open_token", [r["signal"] for r in man["suspected_incomplete"]])
        self.assertGreaterEqual(len(man["suspected_incomplete"]), 3)

    def test_921d1a59_night07_open_token(self) -> None:
        _, man, _ = self._replay("921d1a59")
        self.assertIn("open_token", [r["signal"] for r in man["suspected_incomplete"]])


if __name__ == "__main__":
    unittest.main()
