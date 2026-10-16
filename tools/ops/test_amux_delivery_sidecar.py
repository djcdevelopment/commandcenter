import copy
import hashlib
import json
import subprocess
import sys
import unicodedata
import tempfile
import unittest
from pathlib import Path

from hearth.delivery import contract, render, revision, sourcemap
from tools.ops import amux_delivery_sidecar as sc

RUST = '''fn alpha() {
    flag(true);
}

fn beta() {
    flag(true);
    let s = "}"; // a brace in a string }
}

fn gamma() {
    flag(false);
}

fn delta() {
    flag(true);
    flag(true);
}

fn braces() {
    // } in a comment {
    /* nested /* } */ still comment { */
    let a = "}{";
    let b = r#"} "{" }"#;
    let c = '{';
    let d: &'static str = "x";
    fn inner() {
        flag(true);
    }
    let e = '}';
}

fn after_braces() {
    flag(true);
}
'''


def sha(b):
    return hashlib.sha256(b).hexdigest()


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def output_with(text, quotes, summary="Summary."):
    return {"summary": summary, "sections": [{"heading": "Finding", "paragraphs": [{"text": text, "quotes": quotes}]}]}


class Fixture(unittest.TestCase):
    source = RUST
    words_max = 200
    extra: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "lib.rs").write_text(self.source)
        for name, body in self.extra.items():
            (self.repo / name).write_text(body)
        self.paths = ["lib.rs", *self.extra]
        git(self.repo, "add", *self.paths)
        git(self.repo, "commit", "-qm", "x")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Names things."}],
                      "form": {"words": {"max": self.words_max, "enforce": "fail"}, "citations": "quote"},
                      "sources": [{"path": n, "commit": self.base} for n in self.paths],
                      "aids": ["source_map", "quote_renderer"]}
        self.sm = sourcemap.build(str(self.repo), self.base, self.paths)

    def tearDown(self):
        self.tmp.cleanup()

    def make_run(self, output, name="run"):
        run = self.root / name
        run.mkdir()
        md, man, _ = render.render_with_findings(output, self.brief, self.sm,
                                                 {"model": "m", "backend": "b", "environment": "prod"})
        raw_out = (json.dumps(output, indent=2, sort_keys=True) + "\n").encode()
        raw_man = (json.dumps(man, indent=2) + "\n").encode()
        (run / "delivery-output.json").write_bytes(raw_out)
        (run / "candidate.md").write_text(md)
        (run / "delivery.json").write_bytes(raw_man)
        self.manifest = {
            "work_id": "work_fixture", "repo": str(self.repo), "base_commit": self.base, "declared_paths": self.paths,
            "brief": self.brief, "brief_sha256": man["brief_sha256"],
            "source": {"files": [{"path": f.path, "sha256": f.sha256} for f in self.sm.files]},
            "delivery_artifacts": {"output": {"sha256": sha(raw_out)}, "candidate": {"sha256": sha(md.encode())},
                                   "manifest": {"sha256": sha(raw_man)}}}
        self.write_manifest(run)
        return run

    def write_manifest(self, run):
        (run / "work-manifest.json").write_text(json.dumps(self.manifest))

    def derive(self, text, quotes):
        return sc.derive(output_with(text, quotes), self.brief, self.sm)


class Scope(Fixture):
    def test_reviewed_quote_repair_is_counted_and_preserves_original(self):
        out = output_with("The alpha function sets the flag.", ["flag(true);"])
        original = copy.deepcopy(out)
        patch = {"claim_id": "s0.p0.q0", "old_quote": "flag(true);",
                 "new_quote": "fn alpha() {\n    flag(true);"}
        result = sc.derive(out, self.brief, self.sm, reviewed_repairs=[patch])
        self.assertEqual(out, original)
        self.assertEqual(result["after"]["unsupported"], [])
        self.assertEqual(result["manifest"]["repairs"][sc.REVIEWED_KEY], 1)
        self.assertTrue(result["prose_unchanged"])
        for bad in ({**patch, "old_quote": "wrong"}, {**patch, "new_quote": "flag(false!!!);"},
                    {**patch, "new_quote": "flag(true);\n}"}, {**patch, "repair_prose": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(sc.SidecarError):
                sc.derive(out, self.brief, self.sm, reviewed_repairs=[bad])

    def test_reviewed_prose_edit_is_only_the_same_erroneous_quotation(self):
        out = output_with("It calls 'fn gama()' to proceed.", ["fn gama()"])
        patch = {"claim_id": "s0.p0.q0", "old_quote": "fn gama()", "new_quote": "fn gamma()",
                 "repair_prose": True}
        result = sc.derive(out, self.brief, self.sm, reviewed_repairs=[patch])
        self.assertEqual(result["output"]["sections"][0]["paragraphs"][0]["text"],
                         "It calls 'fn gamma()' to proceed.")
        self.assertEqual(result["manifest"]["repairs"][sc.PROSE_KEY], 1)
        self.assertFalse(result["prose_unchanged"])
        self.assertTrue(result["headings_unchanged"] and result["paragraph_order_unchanged"])
        out["sections"][0]["paragraphs"][0]["text"] = "It calls the function."
        with self.assertRaises(sc.SidecarError):
            sc.derive(out, self.brief, self.sm, reviewed_repairs=[patch])
        out["sections"][0]["paragraphs"][0]["text"] = "It calls fn gama() to proceed."
        with self.assertRaises(sc.SidecarError):
            sc.derive(out, self.brief, self.sm, reviewed_repairs=[patch])

    def test_reviewed_file_requires_original_work_id_and_raw_hash(self):
        run = self.make_run(output_with("It calls fn gama().", ["fn gama()"]))
        patch_file = self.root / "repairs.json"
        patch_file.write_text(json.dumps({"schema": "amux-quote-repairs.v1", "work_id": "work_fixture",
                                         "output_sha256": "wrong", "repairs": []}))
        dest = self.root / "derivative"
        with self.assertRaises(sc.SidecarError):
            sc.run(run, dest, quote_repairs=patch_file)
        self.assertFalse(dest.exists())

    def test_unique_named_owner_resolves_and_counts(self):
        r = self.derive("The alpha function sets the flag.", ["flag(true);"])
        self.assertEqual(len(r["changes"]), 1)
        ch = r["changes"][0]
        self.assertEqual((ch["symbol"], [o["line"] for o in ch["original_occurrences"]], ch["resolved_range"][0]), ("alpha", [2, 6, 15, 16, 27, 33], 1))
        self.assertEqual({o["path"] for o in ch["original_occurrences"]}, {"lib.rs"})
        self.assertEqual(ch["new_quote"], "alpha() {\n    flag(true);")
        self.assertEqual(ch["resolved_range"], [1, 2])
        self.assertEqual(r["manifest"]["repairs"][sc.REPAIR_KEY], 1)
        self.assertEqual(r["after"]["unsupported"], [])
        self.assertEqual(r["before"]["unsupported"], ["s0.p0.q0"])
        self.assertEqual(r["after"]["deterministic"]["state"], "pass")
        self.assertEqual(ch["source_sha256"], self.sm.files[0].sha256)

    def test_other_named_function_without_a_hit_is_not_a_tie(self):
        r = self.derive("Both alpha and gamma are mentioned.", ["flag(true);"])
        self.assertEqual(r["changes"][0]["symbol"], "alpha")

    def test_two_named_owners_stay_unresolved(self):
        r = self.derive("Both alpha and beta set it.", ["flag(true);"])
        self.assertEqual(r["changes"], [])
        self.assertIn("several named functions", r["unresolved"][0]["reason"])
        self.assertEqual(r["after"]["deterministic"]["state"], "fail")

    def test_no_named_owner_stays_unresolved(self):
        for text in ("Only gamma is named.", "Nothing is named here."):
            r = self.derive(text, ["flag(true);"])
            self.assertEqual(r["changes"], [])
            self.assertIn("no function named", r["unresolved"][0]["reason"])

    def test_duplicate_inside_one_function_stays_unresolved(self):
        r = self.derive("The delta function repeats it.", ["flag(true);"])
        self.assertEqual(r["changes"], [])
        self.assertIn("duplicate occurrences inside delta", r["unresolved"][0]["reason"])

    def test_nested_function_hit_belongs_to_the_inner_function_only(self):
        r = self.derive("The braces function mentions it.", ["flag(true);"])
        self.assertEqual(r["changes"], [])
        r = self.derive("The inner function sets it.", ["flag(true);"])
        self.assertEqual(r["changes"][0]["symbol"], "inner")

    def test_fuzzy_typo_quote_is_untouched(self):
        out = output_with("The alpha function sets the flag.", ["flag(ture);"])
        r = sc.derive(out, self.brief, self.sm)
        self.assertEqual((r["changes"], r["unresolved"], r["output"]), ([], [], out))
        self.assertEqual(r["after"]["unsupported"], ["s0.p0.q0"])

    def test_derivative_is_idempotent_and_prose_is_identical(self):
        out = output_with("The alpha function sets the flag.", ["flag(true);"])
        first = sc.derive(out, self.brief, self.sm)
        self.assertTrue(first["prose_unchanged"] and first["headings_unchanged"] and first["paragraph_order_unchanged"])
        self.assertTrue(revision.same_prose(out, first["output"]))
        second = sc.derive(first["output"], self.brief, self.sm)
        self.assertEqual(second["changes"], [])
        self.assertEqual(second["output"], first["output"])
        self.assertEqual(second["manifest"]["claims"], first["manifest"]["claims"])

    def test_input_output_object_is_not_mutated(self):
        out = output_with("The alpha function sets the flag.", ["flag(true);"])
        before = copy.deepcopy(out)
        sc.derive(out, self.brief, self.sm)
        self.assertEqual(out, before)

    def test_over_budget_derivative_remains_failed(self):
        self.brief["form"]["words"]["max"] = 5
        r = self.derive("The alpha function sets the flag and says many more words than five.", ["flag(true);"])
        self.assertEqual(len(r["changes"]), 1)
        self.assertEqual(r["after"]["unsupported"], [])
        self.assertEqual(r["after"]["deterministic"]["state"], "fail")
        self.assertGreater(r["after"]["words_over_max"], 0)
        self.assertEqual(r["output"]["summary"], "Summary.")


    def test_blank_quotes_keep_original_indices_and_prose(self):
        quotes = ["", "flag(false);", " \n", "flag(true);"]
        out = output_with("The alpha function sets the flag.", quotes)
        r = sc.derive(out, self.brief, self.sm)
        ch = r["changes"]
        self.assertEqual([(c["claim_id"], c["claim_quote_index"], c["quote_index"], c["quote"]) for c in ch],
                         [("s0.p0.q1", 1, 3, "flag(true);")])
        self.assertEqual(r["output"]["sections"][0]["paragraphs"][0]["quotes"],
                         ["", "flag(false);", " \n", ch[0]["new_quote"]])
        self.assertTrue(r["prose_unchanged"] and r["paragraph_order_unchanged"])
        lead = sc.derive(output_with("The alpha function sets the flag.", ["", "flag(true);"]), self.brief, self.sm)
        self.assertEqual((lead["changes"][0]["quote_index"], lead["changes"][0]["claim_quote_index"]), (1, 0))
        self.assertEqual(lead["after"]["unsupported"], [])


class OneLine(Fixture):
    source = "fn alpha() { finish(); }\nfn beta() { finish(); }\nfn gamma() { other(); }\n"

    def test_one_line_function_is_repaired_with_same_line_context(self):
        r = self.derive("The alpha function finishes.", ["finish();"])
        ch = r["changes"][0]
        self.assertEqual(ch["symbol"], "alpha")
        self.assertEqual(ch["new_quote"], "alpha() { finish();")
        self.assertEqual(ch["resolved_range"], [1, 1])
        self.assertEqual(r["after"]["unsupported"], [])
        self.assertEqual(self.derive("The beta function finishes.", ["finish();"])["changes"][0]["new_quote"],
                         "beta() { finish();")

    def test_one_line_ambiguity_is_not_guessed(self):
        r = self.derive("Both alpha and beta finish.", ["finish();"])
        self.assertEqual(r["changes"], [])


class TwoSources(Fixture):
    source = "fn one() {\n    a();\n    flag(true);\n}\n"
    extra = {"b.rs": "// pad\n" * 10 + "fn two() {\n    b();\n    flag(true);\n}\n\n" + "fn pad() {}\n" * 8 + "fn three() {\n    flag(true);\n}\n"}

    def test_occurrences_name_their_own_path_line_and_hash(self):
        r = self.derive("The one function sets it.", ["flag(true);"])
        ch = r["changes"][0]
        self.assertEqual(ch["path"], "lib.rs")
        self.assertEqual([(o["path"], o["line"]) for o in ch["original_occurrences"]],
                         [("b.rs", 13), ("b.rs", 25), ("lib.rs", 3)])
        hashes = {f.path: f.sha256 for f in self.sm.files}
        self.assertTrue(all(o["source_sha256"] == hashes[o["path"]] for o in ch["original_occurrences"]))
        self.assertEqual(ch["source_sha256"], hashes["lib.rs"])
        self.assertEqual(r["after"]["unsupported"], [])
        other = self.derive("The two function sets it.", ["flag(true);"])["changes"][0]
        self.assertEqual((other["path"], other["symbol"]), ("b.rs", "two"))


class DecomposedIdentifier(Fixture):
    source = "fn cafe\u0301() {\n    flag(true);\n}\nfn other() {\n    flag(true);\n}\n"

    def test_decomposed_identifier_stays_unresolved(self):
        for name in ("cafe", "cafe\u0301", "caf\u00e9"):
            r = self.derive(f"The {name} function sets it.", ["flag(true);"])
            self.assertEqual(r["changes"], [])
            self.assertIn("uncertain scope parse", r["unresolved"][0]["reason"])
            self.assertIn("non-ASCII", r["unresolved"][0]["reason"])
            self.assertEqual(r["output"]["sections"][0]["paragraphs"][0]["quotes"], ["flag(true);"])


class CombiningMarkInComment(Fixture):
    source = "fn alpha() {\n    // e\u0301\n    flag(true);\n}\nfn beta() {\n    // x\n    flag(true);\n}\n"

    def test_excerpt_never_starts_with_a_detached_mark(self):
        r = self.derive("The alpha function sets it.", ["flag(true);"])
        quote = r["changes"][0]["new_quote"]
        self.assertNotEqual(unicodedata.category(quote[0])[0], "M")
        self.assertIn(quote, self.source)
        self.assertEqual(r["after"]["unsupported"], [])


class Scanner(unittest.TestCase):
    def test_non_ascii_code_tokens_are_refused_and_never_hang(self):
        code = ("from tools.ops import amux_delivery_sidecar as sc\n"
                "for src in ('fn caf\\u00e9() {}', 'fn cafe\\u0301() {}', 'fn a() { let \\u00fc = 1; }', \"fn a() { 'é }\"):\n"
                "    try:\n        print('parsed', [s['name'] for s in sc.function_spans(src)])\n"
                "    except sc.ScopeParseError as e:\n        print('refused', e)\n"
                "ok = 'fn ok() { let s = \"caf\\u00e9 }\"; /* \\u00fc } */ }  // cafe\\u0301 {'\n"
                "print('ok', [s['name'] for s in sc.function_spans(ok)])\n")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
                              cwd=Path(__file__).resolve().parents[2])
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual([ln.split()[0] for ln in lines], ["refused"] * 4 + ["ok"])
        self.assertEqual(lines[-1], "ok ['ok']")

    def test_owner_matching_does_not_split_combining_marks(self):
        self.assertTrue(sc._named("The alpha function", "alpha"))
        self.assertFalse(sc._named("The alpha\u0301 function", "alpha"))
        self.assertFalse(sc._named("The \u00e9alpha function", "alpha"))
        self.assertFalse(sc._named("alpha_2", "alpha"))

    def test_uncertain_shapes_fail_closed(self):
        for text in ('fn a() { let s = "open; }', "fn a() { /* open }", 'fn a() { let s = r#"x"; }',
                     "fn a() { let r#fn = 1; }", "fn a() { { }", "fn a() }", "fn a<{N}>() {}", "fn a("):
            with self.subTest(text=text), self.assertRaises(sc.ScopeParseError):
                sc.function_spans(text)

    def test_bodiless_declarations_are_skipped(self):
        self.assertEqual([s["name"] for s in sc.function_spans("trait T { fn a(&self); fn b(&self) { } }")], ["b"])


class UncertainParse(Fixture):
    source = 'fn alpha() {\n    flag(true);\n}\nfn broken() {\n    let s = "oops;\n    flag(true);\n}\n'

    def test_unparseable_file_leaves_quote_unresolved(self):
        r = self.derive("The alpha function sets the flag.", ["flag(true);"])
        self.assertEqual(r["changes"], [])
        self.assertIn("uncertain scope parse", r["unresolved"][0]["reason"])


class RunDirectory(Fixture):
    def run_ok(self, name="run"):
        run = self.make_run(output_with("The alpha function sets the flag.", ["flag(true);"]), name)
        out = self.root / "out" / name
        return run, out, sc.run(run, out)

    def snapshot(self, run):
        return {p.name: p.read_bytes() for p in sorted(run.iterdir())}

    def test_originals_unchanged_and_report_carries_evidence(self):
        run = self.make_run(output_with("The alpha function sets the flag.", ["flag(true);"]))
        before = self.snapshot(run)
        out = self.root / "out" / "x"
        rep = sc.run(run, out)
        self.assertEqual(before, self.snapshot(run))
        self.assertEqual((out / "original" / "delivery-output.json").read_bytes(), before["delivery-output.json"])
        self.assertTrue(rep["semantic_review_required"])
        self.assertIsNone(rep["official_verdict"])
        i = rep["input"]
        self.assertEqual(i["original_output_sha256"], sha(before["delivery-output.json"]))
        self.assertEqual(i["brief_sha256"], self.manifest["brief_sha256"])
        self.assertEqual(i["base_commit"], self.base)
        self.assertEqual(i["sources"][0]["sha256"], self.sm.files[0].sha256)
        for name, digest in rep["output"]["files"].items():
            self.assertEqual(sha((out / name).read_bytes()), digest)
        self.assertEqual(rep["repairs"], {sc.REPAIR_KEY: 1})
        self.assertIn('-            "flag(true);"', rep["output_diff"])
        self.assertEqual(rep["before"]["deterministic"]["state"], "fail")
        self.assertEqual(rep["after"]["deterministic"]["state"], "pass")
        contract.check_manifest(json.loads((out / "delivery.derivative.json").read_text()))
        self.assertEqual(json.loads((out / "sidecar-report.json").read_text())["schema"], sc.REPORT_SCHEMA)

    def test_destination_reuse_overlap_and_immutable_locations_refuse(self):
        run, out, _ = self.run_ok()
        for bad in (out, run, run / "inside", self.root, self.root / "runs" / "x", self.root / "jepa-x" / "y"):
            with self.subTest(bad=str(bad)), self.assertRaises(sc.SidecarError):
                sc.run(run, bad)
        self.assertEqual(sc.main([str(run), str(out)]), 2)

    def test_hash_mismatches_refuse(self):
        run = self.make_run(output_with("The alpha function sets the flag.", ["flag(true);"]))
        original = self.snapshot(run)
        cases = {}
        for name in ("delivery-output.json", "candidate.md", "delivery.json"):
            cases[name] = lambda n=name: (run / n).write_bytes(original[n] + b" ")
        good = copy.deepcopy(self.manifest)
        cases["brief"] = lambda: self.manifest.update(brief_sha256="0" * 64)
        cases["source"] = lambda: self.manifest["source"]["files"][0].update(sha256="1" * 64)
        cases["brief-content"] = lambda: self.manifest["brief"]["form"]["words"].update(max=999)
        for label, mutate in cases.items():
            with self.subTest(label=label):
                for n, b in original.items():
                    (run / n).write_bytes(b)
                self.manifest = copy.deepcopy(good)
                mutate()
                self.write_manifest(run)
                with self.assertRaises(sc.SidecarError):
                    sc.run(run, self.root / "out" / f"m-{label}")
                self.assertFalse((self.root / "out" / f"m-{label}").exists())

    def test_cli_exit_codes(self):
        run = self.make_run(output_with("The alpha function sets the flag.", ["flag(true);"]))
        self.assertEqual(sc.main([str(run), str(self.root / "out" / "cli")]), 0)
        self.assertEqual(sc.main([str(run), str(self.root / "out" / "cli")]), 2)


if __name__ == "__main__":
    unittest.main()
