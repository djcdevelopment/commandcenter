from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface.testing import lint_digest, run_tests

FAILING_SUITE = textwrap.dedent(
    """
    import unittest


    class SampleTests(unittest.TestCase):
        def test_passes(self):
            self.assertEqual(1 + 1, 2)

        def test_fails(self):
            self.assertEqual(1 + 1, 3, "arithmetic drifted")

        def test_errors(self):
            raise RuntimeError("synthetic explosion")


    if __name__ == "__main__":
        unittest.main()
    """
)

PASSING_SUITE = textwrap.dedent(
    """
    import unittest


    class HappyTests(unittest.TestCase):
        def test_ok(self):
            self.assertTrue(True)
    """
)


class RunTestsDigestTests(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)

    def _write_suite(self, name: str, body: str) -> None:
        suite_dir = self.scope / name
        suite_dir.mkdir(parents=True, exist_ok=True)
        (suite_dir / "test_sample.py").write_text(body, encoding="utf-8")

    def test_failing_suite_yields_failures_only_digest(self) -> None:
        self._write_suite("suitetests", FAILING_SUITE)
        digest = run_tests(suite="suitetests")

        self.assertEqual(digest["ran"], 3)
        self.assertEqual(digest["failures"], 1)
        self.assertEqual(digest["errors"], 1)
        self.assertFalse(digest["ok"])
        self.assertEqual(len(digest["failing_tests"]), 2)

        ids = sorted(entry["id"] for entry in digest["failing_tests"])
        self.assertTrue(any("test_fails" in test_id for test_id in ids))
        self.assertTrue(any("test_errors" in test_id for test_id in ids))
        for entry in digest["failing_tests"]:
            self.assertLessEqual(len(entry["short_traceback"].splitlines()), 15)
            self.assertTrue(entry["short_traceback"])  # never empty
        # digest, not a dump: the passing test's name must not leak in
        self.assertNotIn("test_passes", str(digest["failing_tests"]))

    def test_passing_suite_is_ok_with_no_failures(self) -> None:
        self._write_suite("suitetests", PASSING_SUITE)
        digest = run_tests(suite="suitetests")
        self.assertTrue(digest["ok"])
        self.assertEqual(digest["ran"], 1)
        self.assertEqual(digest["failing_tests"], [])
        self.assertGreaterEqual(digest["duration_s"], 0)

    def test_unknown_runner_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_tests(runner="pytest")

    def test_missing_suite_dir_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_tests(suite="no-such-dir")

    def test_suite_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_tests(suite="../elsewhere")


class LintDigestTests(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        (self.scope / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)

    def test_reports_availability_shape(self) -> None:
        result = lint_digest(["mod.py"])
        self.assertIn("available", result)
        if result["available"]:
            self.assertIn(result["linter"], ("ruff", "flake8"))
            self.assertIn("issue_count", result)
            self.assertIsInstance(result["issues"], list)
        else:
            self.assertIn("reason", result)

    def test_paths_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            lint_digest(["../evil.py"])
