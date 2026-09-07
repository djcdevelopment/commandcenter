"""``python -m hearth.backlog`` — promote, list, validate.

The CLI is driven in-process through ``__main__.main(argv)`` with every path
pointed at a temp root, so no test touches the live backlog, the live refine
store, or the repo's knowledge files. ``list`` is asserted to dispatch nothing.
"""
from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase

from hearth.backlog import __main__ as cli
from hearth.backlog import sources
from hearth.toolsurface.task_lane import _ccmeta_header


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path: Path, data) -> Path:
    return _write(path, json.dumps(data))


def _run(argv: list) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class _CliFixture(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.queued = self.tmp / "backlog" / "queued"
        self.refine = self.tmp / "refine"
        self.queued.mkdir(parents=True)
        self.refine.mkdir(parents=True)
        self.worth = _write_json(self.tmp / "worth.json", {
            "contract_version": "candidate-worth.v1",
            "entries": [{"candidate_id": "bbb_high", "worth_points": 10,
                         "reason": "core lab thesis", "author": "derek"}]})
        self.results = _write_json(self.tmp / "results.json", {"results": []})

    def _list_argv(self, scope: str = "all", as_json: bool = False) -> list:
        argv = ["--json"] if as_json else []
        return argv + ["list", "--scope", scope,
                       "--queued-dir", str(self.queued),
                       "--refine-dir", str(self.refine),
                       "--worth-path", str(self.worth),
                       "--results-path", str(self.results)]

    def _add_authored(self, name: str = "0001-authored_thing.md") -> Path:
        return _write(self.queued / name,
                      _ccmeta_header(["cc-builder-2"], task_class="build")
                      + "An authored brief body.")

    def _add_refined(self, intent_id: str = "refine-alpha-11111111") -> str:
        _write_json(self.refine / f"{intent_id}.json", {
            "contract_version": "commander-refine.v1", "intent_id": intent_id,
            "idea": "refined idea", "final": "The refined final text.", "ok": True})
        return intent_id


class PromoteRefineCliTests(_CliFixture):
    def _promote(self, *extra) -> tuple[int, str, str]:
        intent_id = "refine-alpha-11111111"
        return _run(["promote-refine", intent_id, "--task-class", "build",
                     "--requires", "docs/plan.md", "--max-age-s", "3600",
                     "--refine-dir", str(self.refine),
                     "--promoted-by", "derek", *extra])

    def test_writes_the_sidecar_and_the_intent_then_lists(self) -> None:
        self._add_refined()
        code, out, _ = self._promote()
        self.assertEqual(code, 0)
        self.assertIn("written", out)
        sidecar = self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}"
        self.assertTrue(sidecar.is_file())
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(doc["contract"], sources.PROMOTE_CONTRACT)
        self.assertEqual(doc["requires"], ["docs/plan.md"])
        self.assertEqual(doc["max_age_s"], 3600)

    def test_identical_rerun_is_byte_identical_and_says_unchanged(self) -> None:
        self._add_refined()
        self._promote()
        sidecar = self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}"
        before = sidecar.read_bytes()
        code, out, _ = self._promote()
        self.assertEqual(code, 0)
        self.assertIn("unchanged", out)
        self.assertEqual(sidecar.read_bytes(), before)

    def test_conflicting_rerun_exits_nonzero_and_names_replace(self) -> None:
        self._add_refined()
        self._promote()
        code, _out, err = self._promote("--task-class", "research")
        self.assertEqual(code, 1)
        self.assertIn("--replace", err)

    def test_replace_is_honoured(self) -> None:
        self._add_refined()
        self._promote()
        code, out, _ = self._promote("--task-class", "research", "--replace")
        self.assertEqual(code, 0)
        self.assertIn("replaced", out)
        doc = json.loads(
            (self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}")
            .read_text(encoding="utf-8"))
        self.assertEqual(doc["task_class"], "research")

    def test_repeatable_requires_flag_collects_every_glob(self) -> None:
        self._add_refined()
        code, _out, _err = _run(
            ["promote-refine", "refine-alpha-11111111", "--task-class", "build",
             "--requires", "a/one.md", "--requires", "b/two.md",
             "--refine-dir", str(self.refine)])
        self.assertEqual(code, 0)
        doc = json.loads(
            (self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}")
            .read_text(encoding="utf-8"))
        self.assertEqual(doc["requires"], ["a/one.md", "b/two.md"])

    def test_unknown_intent_exits_nonzero_and_writes_nothing(self) -> None:
        code, _out, err = self._promote()
        self.assertEqual(code, 1)
        self.assertIn("no refine result found", err)
        self.assertEqual(list(self.refine.glob("*")), [])


class ListCliTests(_CliFixture):
    def test_empty_backlog_reports_nothing_to_dispatch(self) -> None:
        _write_json(self.worth, {"entries": []})
        code, out, _ = _run(self._list_argv())
        self.assertEqual(code, 0)
        self.assertIn("nothing to dispatch", out)

    def test_one_of_each_source_picks_the_authored_one(self) -> None:
        self._add_authored()
        intent_id = self._add_refined()
        sources.promote_refine(intent_id, refine_dir=self.refine, task_class="build",
                               requires=["docs/plan.md"], now="2026-09-06T10:00:00Z")
        code, out, _ = _run(self._list_argv())
        self.assertEqual(code, 0)
        self.assertIn("authored/0001-authored_thing.md", out)
        self.assertIn("dispatched: no", out)

    def test_json_report_carries_counts_and_the_choice(self) -> None:
        self._add_authored()
        intent_id = self._add_refined()
        sources.promote_refine(intent_id, refine_dir=self.refine, task_class="build",
                               requires=["docs/plan.md"], now="2026-09-06T10:00:00Z")
        code, out, _ = _run(self._list_argv(as_json=True))
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["counts"],
                         {"authored": 1, "refined": 1, "candidate": 1})
        self.assertEqual(report["next"]["source"], "authored")
        self.assertEqual(report["scope"], "all")
        self.assertFalse(report["dispatched"])

    def test_scope_changes_the_choice(self) -> None:
        self._add_authored()
        code, out, _ = _run(self._list_argv(scope="candidate", as_json=True))
        report = json.loads(out)
        self.assertEqual(report["next"]["source"], "candidate")
        self.assertEqual(report["next"]["source_ref"], "bbb_high")
        self.assertEqual(report["admitted_sources"], ["candidate"])

    def test_rejected_files_are_reported_with_a_reason(self) -> None:
        _write(self.queued / "broken.md", "no header here")
        code, out, _ = _run(self._list_argv(as_json=True))
        report = json.loads(out)
        self.assertEqual(report["counts"]["authored"], 0)
        self.assertIn("unparseable", report["rejected"]["authored"][0]["reason"])

    def test_list_never_writes_anything(self) -> None:
        self._add_authored()
        before = sorted(p.name for p in self.queued.iterdir())
        _run(self._list_argv())
        self.assertEqual(sorted(p.name for p in self.queued.iterdir()), before)
        self.assertFalse((self.tmp / "backlog" / "dispatched").exists())

    def test_an_unknown_scope_is_refused_by_the_parser(self) -> None:
        with self.assertRaises(SystemExit):
            _run(self._list_argv(scope="everything"))


class ValidateCliTests(_CliFixture):
    def test_a_good_brief_validates(self) -> None:
        path = self._add_authored()
        code, out, _ = _run(["--json", "validate", str(path)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["valid"])
        self.assertEqual(report["task_class"], "build")
        self.assertEqual(report["source"], "authored")

    def test_a_broken_brief_exits_one_and_names_the_fault(self) -> None:
        path = _write(self.queued / "broken.md", "no header here")
        code, _out, err = _run(["validate", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("no CCMETA header", err)

    def test_a_missing_file_exits_one(self) -> None:
        code, _out, err = _run(["validate", str(self.tmp / "nope.md")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)


class CliDefaultsTests(TestCase):
    def test_default_paths_name_hearth_var_but_the_cli_creates_nothing(self) -> None:
        """The DEFAULT_* constants may name hearth/var; merely building the
        parser (which reads them for --help text) must not bring it into being."""
        parser = cli.build_parser()
        self.assertIsNotNone(parser)
        self.assertIn("var", str(sources.DEFAULT_QUEUED_DIR).replace("\\", "/"))
        self.assertFalse(sources.DEFAULT_BACKLOG_ROOT.exists(),
                         "building the CLI must not create hearth/var/backlog")
