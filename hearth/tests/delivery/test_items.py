"""Locks for the items procedure (ADR-0058, laps 20 and 21): what the enumerators list and mark, how a row is settled and
what the report says. Each test names the observation that earned it (delivery-plan evidence wave9, rehearsal, docs/rnd-log.md).

Sources are literals written into a temporary git repository and committed there (the enumerator reads `git show
<commit>:<path>`); nothing reads the live repository, its history or runs/. Short real excerpts are copied where the shape
matters and say where from.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.delivery import contract, items


@contextlib.contextmanager
def committed(text: str, path: str = "m.py"):
    """(repo dir, sha): `text` committed as `path` in a fresh git repository under a temp dir."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "HOME": d, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, env=env)  # noqa: E731
        run("init", "-q")
        (Path(d) / path).write_text(text, encoding="utf-8")
        run("add", path)
        run("commit", "-q", "-m", "x")
        yield d, run("rev-parse", "HEAD").stdout.decode().strip()


def enumerate_text(kind: str, text: str, path: str = "m.py") -> list:
    with committed(text, path) as (d, sha):
        return items.enumerate_items(kind, d, sha, [path])


def reading(default: str, controls: str = "c") -> dict:
    return {"fields": {"default": default, "controls": controls}, "lines": [1]}


def by_var(found: list) -> dict:
    return {i["var"]: i for i in found}


class EnvReadEnumeratorTests(unittest.TestCase):
    def test_writes_to_the_environment_are_not_items(self) -> None:
        """wave9 run 1: 16 assignments were counted as reads by the first scorer (rnd-log 06:41Z row); the enumerator lists
        loads of os.environ[...] only."""
        found = enumerate_text("env_reads", 'import os\nos.environ["WRITTEN"] = "1"\nx = os.environ["READ"]\nos.environ["A"] += "b"\n')
        self.assertEqual([i["var"] for i in found], ["READ"])

    def test_an_or_read_is_marked_and_its_prompt_carries_the_note(self) -> None:
        """wave9 run 1: 5 of 7 wrong defaults were `get(X) or <value>` reads that both fast readers gave as "none"; the real
        shape is fleet/experiment_linux.py:117 at 82d40ac."""
        src = ('import os\n'
               'a = os.environ.get("VLLM_API_KEY") or os.environ.get("OMEN_ARC_TOKEN")\n'
               'b = os.environ.get("PLAIN", "d")\n'
               'c = os.environ.get("WITH_TWO", "d") or "z"\n')
        with committed(src) as (d, sha):
            found = items.enumerate_items("env_reads", d, sha, ["m.py"])
            by = by_var(found)
            self.assertTrue(by["VLLM_API_KEY"].get("or"))
            self.assertNotIn("or", by["OMEN_ARC_TOKEN"])                 # the last operand is the value, not a read followed by `or`
            self.assertNotIn("or", by["PLAIN"])
            self.assertNotIn("or", by["WITH_TWO"])                       # it has a second argument already
            note = items.KINDS["env_reads"].or_note
            self.assertIn(note, items.prompt("env_reads", by["VLLM_API_KEY"], d, sha))
            self.assertNotIn(note, items.prompt("env_reads", by["PLAIN"], d, sha))

    def test_line_is_the_reads_own_line_not_the_statements_first_line(self) -> None:
        """wave9 run 1: 13 quotes cited the first line of a multi-line statement, not the line of the read; run 2: 0."""
        found = enumerate_text("env_reads", 'import os\nvalue = dict(\n    a=1,\n    b=os.environ.get("B", "x"),\n)\n')
        (item,) = found
        self.assertEqual((item["start"], item["end"], item["line"]), (2, 5, 4))

    def test_judge_marks_computed_conditional_default_and_later_fallback(self) -> None:
        """wave9 run 2 and rehearsal `work_96772bb2`: both fast readers agreed wrongly on a computed name
        (`os.environ.get(token_env)`, hearth/toolsurface/occupancy.py:133 at 82d40ac, and `os.environ.get(auth_env)`,
        hearth/operator/execute.py:40) and on a conditional default; code marks them so the judge is asked."""
        src = ('import os\n'
               'AM4_TOKEN_ENV = "AM4_TOKEN"\n'
               'def fetch(url, token_env: str = AM4_TOKEN_ENV):\n'
               '    token = os.environ.get(token_env)\n'
               '    return token\n'
               'def resolve(auth_env="OMEN_ARC_TOKEN"):\n'
               '    token = os.environ.get(auth_env)\n'
               '    return token\n'
               'def cond(flag):\n'
               '    return os.environ.get("COND", "a" if flag else "b")\n'
               'def chain():\n'
               '    return os.environ.get("VLLM_API_KEY") or os.environ.get("OMEN_ARC_TOKEN")\n'
               'def later():\n'
               '    port = os.environ.get("LATER_PORT")\n'
               '    if not port:\n'
               '        port = "8710"\n'
               '    return port\n'
               'def plain():\n'
               '    return os.environ.get("PLAIN", "x")\n'
               'def named():\n'
               '    return os.environ.get(AM4_TOKEN_ENV)\n')
        marks = {i["var"] + ("@%d" % i["line"] if i["var"] in ("token_env", "auth_env") else ""): i.get("judge") for i in enumerate_text("env_reads", src)}
        self.assertEqual(marks["token_env@4"], "computed")
        self.assertEqual(marks["auth_env@7"], "computed")
        self.assertEqual(marks["COND"], "conditional_default")
        self.assertEqual(marks["VLLM_API_KEY"], "conditional_default")     # its `or` value is another environment read
        self.assertEqual(marks["LATER_PORT"], "later_fallback")
        self.assertIsNone(marks["PLAIN"])
        self.assertIsNone(marks["AM4_TOKEN"])                                # a module constant names a constant, not a computed name
        self.assertTrue(by_var(enumerate_text("env_reads", src))["token_env"]["computed"])

    def test_a_loop_over_a_literal_tuple_is_expanded_into_one_item_per_name(self) -> None:
        """wave9 run 2 (grader): `k` looping over a fixed tuple hid 14 names that are written in the code; the real shape is
        the comprehension at fleet/bankedfire_linux.py:272 at 82d40ac."""
        src = ('import os\n'
               'env_args = [f"--setenv={k}={os.environ[k]}" for k in\n'
               '            ("HEARTH_ROOT", "PYTHONPATH", "VLLM_API_KEY")\n'
               '            if os.environ.get(k)]\n'
               'for name in ["ALPHA", "BETA"]:\n'
               '    v = os.environ.get(name, "d")\n')
        found = enumerate_text("env_reads", src)
        self.assertEqual(sorted(i["var"] for i in found), ["ALPHA", "BETA", "HEARTH_ROOT", "HEARTH_ROOT", "PYTHONPATH", "PYTHONPATH",
                                                           "VLLM_API_KEY", "VLLM_API_KEY"])
        self.assertFalse(any(i.get("computed") or i.get("judge") == "computed" for i in found))
        self.assertEqual(sorted(i["line"] for i in found if i["var"] == "VLLM_API_KEY"), [2, 4])   # the read in each place, by its own line

    def test_a_loop_over_something_else_stays_a_computed_name(self) -> None:
        """wave9 run 2: only a literal tuple of string constants is expanded; a loop over a variable, or one that rebinds the
        loop variable, is a name computed at run time and goes to the judge."""
        src = ('import os\n'
               'for k in names:\n'
               '    a = os.environ.get(k)\n'
               'for j in ("X", "Y"):\n'
               '    j = j.lower()\n'
               '    b = os.environ.get(j)\n')
        found = enumerate_text("env_reads", src)
        self.assertEqual([(i.get("computed"), i.get("judge")) for i in found], [(True, "computed")] * 2)


class ParamDefaultEnumeratorTests(unittest.TestCase):
    def test_keyword_only_methods_and_nested_functions_are_listed_with_qualified_names(self) -> None:
        """rnd-log 06:41Z row (ADR-0058): the second item kind lists every parameter with a default, in source order, named
        by qualified function name; the keyword-only parameter without a default is not listed."""
        src = ('def top(a, b=1, *, c="x", d, e=None):\n'
               '    def inner(z=(), /, y=2):\n'
               '        return z\n'
               '    return inner\n'
               'class K:\n'
               '    def method(self, n=0o644):\n'
               '        return n\n'
               '    class Deep:\n'
               '        def go(self, w=False):\n'
               '            return w\n')
        found = enumerate_text("param_defaults", src)
        self.assertEqual([(i["func"], i["param"]) for i in found],
                         [("top", "b"), ("top", "c"), ("top", "e"), ("top.inner", "z"), ("top.inner", "y"), ("K.method", "n"), ("K.Deep.go", "w")])
        self.assertEqual(found[0]["name"], "top(b) @ m.py:1")

    def test_judge_marks_for_expressions_multiline_defaults_and_unusual_literal_forms(self) -> None:
        """rnd-log 06:41Z row: a default that is an expression, runs over lines, or is written as 0o644 / 1e-3 / 200_000 is
        marked for the judge; a constant, a name and a negative number are not."""
        src = ('def f(a=1, b=-1, c=NAME, d=os.getcwd(), e=0o644, f=1e-3, g=200_000, h=(\n'
               '    1,\n'
               '    2), i="s", j=1.5):\n'
               '    pass\n')
        marks = {i["param"]: i.get("judge") for i in enumerate_text("param_defaults", src)}
        self.assertEqual(marks, {"a": None, "b": None, "c": None, "d": "expression_default", "e": "literal_form", "f": "literal_form",
                                 "g": "literal_form", "h": "expression_default", "i": None, "j": None})
        multi = enumerate_text("param_defaults", 'def f(x="a"\n      "b"):\n    pass\n')
        self.assertEqual(multi[0].get("judge"), "multiline")


class ParamDefaultComparisonTests(unittest.TestCase):
    def same(self, a: str, b: str) -> bool:
        wrap = lambda s: {"fields": {"parameter": "p", "default": s}}  # noqa: E731
        return items.same("param_defaults", wrap(a), wrap(b))

    def test_quote_style_and_layout_are_one_default(self) -> None:
        """rnd-log 06:41Z row: defaults are compared as Python tokens; quote style, string prefix, backticks and layout do not
        count (`'x'` equals `"x"`)."""
        for a, b in (("'x'", '"x"'), ('r"x"', "'x'"), ("`'x'`", '"x"'), ("(1,\n 2)", "(1, 2)"), ("f( a )  # c", "f(a)"), ("-1", "- 1")):
            with self.subTest(a=a, b=b):
                self.assertTrue(self.same(a, b))

    def test_values_that_python_tells_apart_stay_apart(self) -> None:
        """rnd-log 06:41Z row: None, 0, False, (), the name x and the string 'x' all differ, and 0o644 differs from 420; an
        answer that gives the value in place of the written form is a different reading."""
        values = ["None", "0", "False", "()", "[]", "x", "'x'", "''", "0o644", "420", "X", "1e-3", "0.001"]
        for i, a in enumerate(values):
            for b in values[i + 1:]:
                with self.subTest(a=a, b=b):
                    self.assertFalse(self.same(a, b))
        self.assertTrue(self.same("0o644", "0o644"))

    def test_env_reads_none_spellings_are_one_value(self) -> None:
        """wave9 run 2 (grader): agreement treats "" and none as one value; `same` reads r"x", "x" and x as one default."""
        w = lambda s: {"fields": {"default": s}}  # noqa: E731
        self.assertTrue(items.same("env_reads", w("none"), w("")))
        self.assertTrue(items.same("env_reads", w('r"C:\\x"'), w("C:\\x")))
        self.assertFalse(items.same("env_reads", w("none"), w("0")))


class SettleTests(unittest.TestCase):
    MARKED = {"judge": "computed"}

    def test_two_agreeing_readers_are_agreed_and_a_dispute_needs_a_judge_who_matches_one(self) -> None:
        """wave9: 73 of 91 rows agreed by the two readers, 16 settled by the judge (every settled row right), 2 NOT VERIFIED
        where no two answers agreed."""
        a, b = reading("8710"), reading("8711")
        self.assertEqual(items.settle("env_reads", [a, reading("8710")]), {"state": "agreed", "fields": a["fields"], "by": [0, 1]})
        row = items.settle("env_reads", [a, b], {"default": "8711", "verdict": "B", "lines": [1]})
        self.assertEqual((row["state"], row["fields"], row["by"]), ("settled", b["fields"], [1, "judge"]))
        row = items.settle("env_reads", [a, b], {"default": "8710", "verdict": "A", "lines": [1]})
        self.assertEqual((row["state"], row["by"]), ("settled", [0, "judge"]))
        for judgment in ({"default": "9999", "verdict": "neither", "lines": [1]}, None):
            self.assertEqual(items.settle("env_reads", [a, b], judgment), {"state": "unverified", "fields": None, "by": []})
        self.assertEqual(items.settle("env_reads", [a, None])["state"], "unverified")
        self.assertEqual(items.settle("env_reads", [None, None], {"default": "1", "verdict": "A", "lines": [1]})["state"], "unverified")

    def test_a_marked_row_is_agreed_only_when_the_judges_own_default_is_theirs(self) -> None:
        """wave9 run 2 and rehearsal `work_96772bb2`: `os.environ.get(token_env)` was delivered with the parameter's default
        because both readers gave it and the judge was never asked (rnd-log 06:41Z row); a marked row whose judge differs or
        gave no answer is NOT VERIFIED."""
        wrong = [reading("AM4_TOKEN_ENV"), reading("AM4_TOKEN_ENV")]
        row = items.settle("env_reads", wrong, {"default": "none", "verdict": "neither", "lines": [1]}, self.MARKED)
        self.assertEqual(row, {"state": "unverified", "fields": None, "by": []})
        self.assertEqual(items.settle("env_reads", wrong, None, self.MARKED)["state"], "unverified")
        row = items.settle("env_reads", wrong, {"default": "AM4_TOKEN_ENV", "verdict": "A", "lines": [1]}, self.MARKED)
        self.assertEqual((row["state"], row["by"]), ("agreed", [0, 1, "judge"]))
        row = items.settle("env_reads", wrong, {"default": "AM4_TOKEN_ENV", "verdict": "A", "lines": [1]}, {})        # no mark: as before
        self.assertEqual((row["state"], row["by"]), ("agreed", [0, 1]))

    def test_a_mark_sends_the_item_to_the_judge_and_the_agreed_prompt_says_both_readers_agree(self) -> None:
        """rehearsal `work_96772bb2`: 20 judge calls; a computed name and a conditional default were wrong where both readers
        agreed. The prompt for an agreed pair names one answer, not an A and a B."""
        a = reading("x")
        self.assertFalse(items.needs_judge("env_reads", [a, reading("x")]))
        self.assertFalse(items.needs_judge("env_reads", [a, reading("x")], {"name": "n"}))
        self.assertTrue(items.needs_judge("env_reads", [a, reading("x")], self.MARKED))
        self.assertTrue(items.needs_judge("env_reads", [a, reading("y")]))
        self.assertTrue(items.needs_judge("env_reads", [a, None]))
        src = 'import os\nv = os.environ.get(name)\n'
        with committed(src) as (d, sha):
            (item,) = items.enumerate_items("env_reads", d, sha, ["m.py"])
            agreed = items.judge_prompt("env_reads", item, d, sha, [a, reading("x")])
            self.assertIn("Both readers say the default is: x. Check it against the code.", agreed)
            self.assertIn(items.KINDS["env_reads"].judge_notes["computed"], agreed)
            self.assertNotIn("Reading A says", agreed)
            dispute = items.judge_prompt("env_reads", item, d, sha, [a, reading("y")])
            self.assertIn("Reading A says the default is: x", dispute)
            self.assertIn("Reading B says the default is: y", dispute)
            self.assertNotIn("Both readers say", dispute)


class AssembleTests(unittest.TestCase):
    def rows(self) -> tuple:
        its = [{"id": f"i{n:04d}", "name": f"V{n} @ m.py:{n}", "path": "m.py", "start": n, "end": n, "show": [[n, n]], "var": f"V{n}", "line": n}
               for n in range(1, 8)]
        its[3]["judge"] = "computed"
        its[3]["computed"] = True
        agreed = lambda i: {"state": "agreed", "readings": [reading(f"d{i}", "SECRET SENTENCE"), reading(f"d{i}", "SECRET SENTENCE")],
                            "by": [0, 1], "fields": reading(f"d{i}", "SECRET SENTENCE")["fields"], "judgment": None}   # noqa: E731
        rows = [agreed(0), agreed(1), agreed(2)]
        jg = {"default": "d3", "verdict": "A", "lines": [3]}
        rows.append({"state": "agreed", "readings": [reading("d3"), reading("d3")], "by": [0, 1, "judge"], "fields": reading("d3")["fields"], "judgment": jg})
        rows.append({"state": "settled", "readings": [reading("d4"), reading("zz")], "by": [0, "judge"], "fields": reading("d4")["fields"],
                     "judgment": {"default": "d4", "verdict": "A", "lines": [5]}})
        rows.append({"state": "unverified", "readings": [reading("p"), reading("q")], "by": [], "fields": None,
                     "judgment": {"default": "r", "verdict": "neither", "lines": [6]}})
        rows.append({"state": "unverified", "readings": [reading("p"), None], "by": [], "fields": None, "judgment": None})
        return its, rows

    def test_a_verified_row_carries_no_model_sentence_and_cites_the_reads_own_line(self) -> None:
        """wave9 run 1: of 89 one-line "what it controls" sentences 64 were right, 22 vague, 3 false and nothing gated them; the
        sentence stays in items-readings.json and the row holds the default only, quoted by path:line."""
        its, rows = self.rows()
        out, _ = items.assemble("env_reads", its, rows, [])
        paras = [p for s in out["sections"] for p in s["paragraphs"]]
        self.assertNotIn("SECRET SENTENCE", json.dumps(out))
        self.assertEqual(paras[0], {"text": "`V1` at m.py: default d0.", "quotes": ["m.py:1"]})
        self.assertEqual(paras[3]["text"], "`V4` (a name computed at run time) at m.py: default d3.")
        self.assertEqual(paras[3]["quotes"], ["m.py:4"])

    def test_an_unverified_row_shows_the_readings_and_the_judges_value(self) -> None:
        """wave9 run 1: the 2 NOT VERIFIED rows were honest; rehearsal: a marked row whose judge differs is shown the same way."""
        its, rows = self.rows()
        out, _ = items.assemble("env_reads", its, rows, [])
        paras = [p["text"] for s in out["sections"] for p in s["paragraphs"]]
        self.assertEqual(paras[5], "`V6` at m.py: NOT VERIFIED. Readings of the default: p | q; judge: r.")
        self.assertEqual(paras[6], "`V7` at m.py: NOT VERIFIED. Readings of the default: p | no answer; judge: no answer.")

    def test_the_summarys_counts_equal_the_rows(self) -> None:
        """wave9 / rnd-log 06:41Z row: seven wrong rows became one by counting honestly; the report's counts, the summary's
        sentence and the manifest's items block all derive from the rows and agree."""
        its, rows = self.rows()
        out, rep = items.assemble("env_reads", its, rows, [])
        states = [r["state"] for r in rows]
        self.assertEqual((rep["items"], rep["agreed"], rep["settled"], rep["unverified"]), (7, states.count("agreed"), 1, 2))
        self.assertEqual(rep["agreed"] + rep["settled"] + rep["unverified"], rep["items"])
        self.assertEqual(sum(len(s["paragraphs"]) for s in out["sections"]), rep["items"])
        self.assertEqual((rep["reader_failures"], rep["judge_failures"], rep["judged_by_mark"], rep["files"]), (1, 1, 1, 1))
        s = out["summary"]
        for text in ("Delivery of 7 environment-variable reads in 1 files.", "4 were agreed by the first two readers",
                     "1 were settled by a judge", "2 are marked NOT VERIFIED",
                     "1 rows were sent to the judge because of a mark", "1 reader calls failed and 1 judge calls gave no answer"):
            self.assertIn(text, s)
        self.assertIn("os.environ.copy()", s)                           # what the enumerator cannot see
        block = {"kind": "env_reads", **{k: rep[k] for k in contract.ITEM_COUNTS if k != "readers"},
                 "readers": [{"backend": "b", "model": "m", "role": "reader", "calls": 14}]}
        block["judged_by_mark"], block["judge_failures"] = rep["judged_by_mark"], rep["judge_failures"]
        self.assertEqual(contract._validate_manifest_items(block), [])
        block["agreed"] += 1
        self.assertTrue(contract._validate_manifest_items(block))

    def test_item_kinds_of_the_contract_and_the_registry_are_the_same(self) -> None:
        """rnd-log 06:41Z row: a brief names its kind in `items.kind`; a kind the registry lacks is refused by name."""
        self.assertEqual(tuple(items.KINDS), contract.ITEM_KINDS)
        with self.assertRaisesRegex(items.ItemsError, "unknown item kind"):
            items.enumerate_items("nope", "/nonexistent", "0" * 40, ["m.py"])


if __name__ == "__main__":
    unittest.main()
