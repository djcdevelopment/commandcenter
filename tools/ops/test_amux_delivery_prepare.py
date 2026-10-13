import copy
import io
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr
from pathlib import Path

from hearth.delivery import contract
from tools.ops.amux_delivery_prepare import PrepareError, main, prepare

SPECS = Path(__file__).resolve().parents[2] / "docs" / "amux-delivery" / "specs"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class Prepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.rs").write_text("fn a() {}\nfn b() {}\n")
        git(self.repo, "add", "a.rs")
        git(self.repo, "commit", "-qm", "one")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.spec = {
            "schema": "amux-delivery-spec.v1", "name": "t", "intent": "[goal:G-test] Report on a.rs.", "repo": str(self.repo),
            "base_commit": self.base, "files": ["a.rs"], "task_family": "code_review",
            "brief": {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Names fn a."}],
                      "form": {"words": {"max": 240}, "citations": "quote"}, "generation": {"max_tokens": 2048}}}

    def tearDown(self):
        self.tmp.cleanup()

    def refused(self, spec, text, **kw):
        with self.assertRaises(PrepareError) as cm:
            prepare(spec, **kw)
        self.assertIn(text, str(cm.exception))

    def test_prepares_valid_door_arguments_without_pins(self):
        args, _ = prepare(self.spec)
        self.assertEqual(args["artifact_kind"], "markdown")
        self.assertEqual(args["acceptance_criteria"], ["Names fn a."])
        self.assertEqual(args["brief"]["sources"], [{"path": "a.rs", "commit": self.base}])
        contract.check_brief(args["brief"])
        for pinned in ("lane", "backend", "model", "provider", "max_tokens", "procedure", "revise", "temperature"):
            self.assertNotIn(pinned, args)

    def test_pin_fields_are_not_spec_fields(self):
        for key in ("backend", "model", "lane", "procedure", "max_tokens"):
            self.refused({**self.spec, key: "x"}, "unknown")

    def test_invalid_brief_refused(self):
        bad = copy.deepcopy(self.spec)
        bad["brief"]["substance"] = []
        self.refused(bad, "brief refused")
        bad = copy.deepcopy(self.spec)
        bad["brief"]["items"] = {"kind": "env_reads"}
        self.refused(bad, "items")

    def test_output_must_be_bounded_and_have_headroom(self):
        for edit, text in ((lambda b: b.pop("generation"), "bounded"),
                           (lambda b: b.pop("form"), "bounded"),
                           (lambda b: b["generation"].update(max_tokens=1000), "headroom"),
                           (lambda b: b["generation"].update(max_tokens=20000), "max_tokens")):
            bad = copy.deepcopy(self.spec)
            edit(bad["brief"])
            self.refused(bad, text)

    def test_large_budget_is_the_doors_decision_and_pins_nothing(self):
        big = copy.deepcopy(self.spec)
        big["brief"]["generation"]["max_tokens"] = 8192
        args, _ = prepare(big)
        self.assertEqual(args["brief"]["generation"]["max_tokens"], 8192)
        for pinned in ("lane", "backend", "model", "max_tokens", "procedure"):
            self.assertNotIn(pinned, args)

    def test_goal_prefix_required_and_never_rewritten(self):
        for intent in ("Report on a.rs.", "[goal:] x", "[goal:G-] x", "goal:G-x y", " [goal:G-x] y", "[goal:G-x]", 5):
            with self.subTest(intent=intent):
                self.refused({**self.spec, "intent": intent}, "[goal:G-")
        self.assertEqual(prepare(self.spec)[0]["intent"], self.spec["intent"])

    def test_malformed_types_are_refusals_not_crashes(self):
        for family in ([], {}, "", None, 3):
            self.refused({**self.spec, "task_family": family}, "task_family")
        for files in ([{}], [["a.rs"]], [None], [3], "a.rs", [{}, {}], ["a.rs", {}]):
            with self.assertRaises(PrepareError):
                prepare({**self.spec, "files": files})
        self.refused({**self.spec, "files": ["a.rs", "a.rs"]}, "distinct")

    def test_git_timeout_is_a_clear_refusal(self):
        with mock.patch("tools.ops.amux_delivery_prepare.subprocess.run",
                        side_effect=subprocess.TimeoutExpired(["git"], 60)):
            self.refused(self.spec, "timed out")
            path = Path(self.tmp.name) / "spec.json"
            path.write_text(json.dumps(self.spec))
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(path)]), 2)
            self.assertIn("REFUSED", err.getvalue())

    def test_sourcemap_git_timeout_is_a_clear_refusal(self):
        with mock.patch("tools.ops.amux_delivery_prepare.sourcemap.build",
                        side_effect=subprocess.TimeoutExpired(["git"], 120)):
            self.refused(self.spec, "git timed out")
            path = Path(self.tmp.name) / "spec.json"
            path.write_text(json.dumps(self.spec))
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(path)]), 2)
            self.assertIn("REFUSED: source refused", err.getvalue())

    def test_file_and_base_failures_are_not_repaired(self):
        self.refused({**self.spec, "files": ["missing.rs"]}, "missing.rs")
        self.refused({**self.spec, "files": ["../a.rs"]}, "normalized")
        self.refused({**self.spec, "files": ["/etc/passwd"]}, "normalized")
        self.refused({**self.spec, "files": []}, "files")
        self.refused({**self.spec, "base_commit": "HEAD"}, "40-hex")
        self.refused({**self.spec, "base_commit": "0" * 40}, "rev-parse")
        self.refused({**self.spec, "repo": str(self.repo / "nope")}, "repo")

    def test_pinned_source_consistency(self):
        ok = copy.deepcopy(self.spec)
        ok["brief"]["sources"] = [{"path": "a.rs", "commit": self.base}]
        self.assertEqual(prepare(ok)[0]["brief"]["sources"], [{"path": "a.rs", "commit": self.base}])
        bad = copy.deepcopy(self.spec)
        bad["brief"]["sources"] = [{"path": "a.rs", "commit": "1" * 40}]
        self.refused(bad, "sources")

    def test_reads_pinned_commit_not_worktree(self):
        (self.repo / "a.rs").write_text("changed\n")
        (self.repo / "b.rs").write_text("new\n")
        _, summary = prepare(self.spec)
        self.assertEqual(summary["files"][0]["lines"], 2)
        self.refused({**self.spec, "files": ["b.rs"]}, "b.rs")

    def test_replay_is_deterministic_and_key_tracks_content(self):
        first, _ = prepare(self.spec)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(prepare(copy.deepcopy(self.spec))[0], sort_keys=True))
        changed = copy.deepcopy(self.spec)
        changed["brief"]["substance"][0]["statement"] = "Names fn b."
        self.assertNotEqual(first["idempotency_key"], prepare(changed)[0]["idempotency_key"])
        self.assertNotEqual(first["idempotency_key"], prepare({**self.spec, "attempt": "r2"})[0]["idempotency_key"])
        self.assertNotEqual(first["idempotency_key"], prepare(self.spec, one_call_qualification=True)[0]["idempotency_key"])

    def test_one_call_only_when_asked(self):
        self.assertNotIn("procedure", prepare(self.spec)[0])
        self.assertEqual(prepare(self.spec, one_call_qualification=True)[0]["procedure"], "one_call")

    def test_cli_prints_arguments_only_and_refusal_exits_nonzero(self):
        path = Path(self.tmp.name) / "spec.json"
        path.write_text(json.dumps(self.spec))
        out = Path(self.tmp.name) / "args.json"
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(path), "--out", str(out)]), 0)
        self.assertIn("not submitted", err.getvalue())
        self.assertEqual(json.loads(out.read_text())["files"], ["a.rs"])
        path.write_text(json.dumps({**self.spec, "base_commit": "HEAD"}))
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(path)]), 2)
        self.assertIn("REFUSED", err.getvalue())


class ShippedSpecs(unittest.TestCase):
    def test_every_example_prepares_when_source_is_present(self):
        specs = sorted(SPECS.glob("*.spec.json"))
        self.assertEqual(len(specs), 3)
        for path in specs:
            spec = json.loads(path.read_text())
            if not Path(spec["repo"]).exists():
                self.skipTest("read-only source worktree absent")
            args, _ = prepare(spec)
            self.assertLessEqual(args["brief"]["form"]["words"]["max"], 240)
            self.assertEqual(args["brief"]["generation"]["max_tokens"], 2048)
            self.assertEqual(args["brief"]["form"]["words"]["enforce"], "fail")
            self.assertTrue(args["intent"].startswith("[goal:G-"))
            self.assertEqual(args["base_commit"], "4f3c67f8888a8ed6698af1182d2e94d9ecf0f4d4")


if __name__ == "__main__":
    unittest.main()
