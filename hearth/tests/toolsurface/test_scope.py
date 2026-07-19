"""Multi-root HEARTH_SCOPE sandbox tests: the first root is primary (relative
paths resolve against it, preserving repo-relative callers); later roots only
widen containment for absolute paths."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface._scope import (
    REPO_ROOT,
    in_any_scope,
    resolve_in_scope,
    scope_root,
    scope_roots,
)


class MultiRootScopeTests(TestCase):
    def setUp(self) -> None:
        self.primary = Path(mkdtemp()).resolve()
        self.secondary = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = os.pathsep.join(
            [str(self.primary), str(self.secondary)]
        )

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous
        shutil.rmtree(self.primary, ignore_errors=True)
        shutil.rmtree(self.secondary, ignore_errors=True)

    def test_scope_roots_parses_pathsep_list(self) -> None:
        self.assertEqual(scope_roots(), [self.primary, self.secondary])

    def test_scope_root_is_first_entry(self) -> None:
        self.assertEqual(scope_root(), self.primary)

    def test_relative_path_resolves_against_primary(self) -> None:
        self.assertEqual(resolve_in_scope("note.txt"), self.primary / "note.txt")

    def test_absolute_path_under_secondary_root_allowed(self) -> None:
        target = self.secondary / "other-repo" / "mod.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1", encoding="utf-8")
        self.assertEqual(resolve_in_scope(str(target)), target)

    def test_path_outside_every_root_rejected(self) -> None:
        outside = Path(mkdtemp()).resolve()
        try:
            with self.assertRaisesRegex(ValueError, "escapes"):
                resolve_in_scope(str(outside / "evil.txt"))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_parent_escape_from_primary_still_rejected(self) -> None:
        # ADR-0019 tightened this: '..' is now refused OUTRIGHT rather than
        # normalized away and then containment-checked. Same security outcome,
        # reached earlier and without depending on resolution order — so a
        # traversal cannot be laundered through a symlinked parent.
        with self.assertRaisesRegex(ValueError, r"must not contain '\.\.'"):
            resolve_in_scope("../evil.txt")

    def test_parent_traversal_rejected_even_when_it_lands_in_scope(self) -> None:
        # The stricter rule holds even for a '..' hop that would have resolved
        # back inside the sandbox: the input shape is refused, not just the
        # destination.
        inside = self.primary / "pkg" / ".." / "ok.txt"
        with self.assertRaisesRegex(ValueError, r"must not contain '\.\.'"):
            resolve_in_scope(str(inside))

    def test_missing_listed_root_rejected(self) -> None:
        os.environ["HEARTH_SCOPE"] = os.pathsep.join(
            [str(self.primary), str(self.secondary / "ghost")]
        )
        with self.assertRaisesRegex(ValueError, "not an existing directory"):
            scope_roots()

    def test_blank_entries_are_skipped(self) -> None:
        os.environ["HEARTH_SCOPE"] = os.pathsep.join(
            [str(self.primary), "", str(self.secondary)]
        )
        self.assertEqual(scope_roots(), [self.primary, self.secondary])

    def test_in_any_scope_checks_all_roots(self) -> None:
        self.assertTrue(in_any_scope(self.secondary / "deep" / "file.txt"))
        self.assertFalse(in_any_scope(Path(mkdtemp()).resolve() / "file.txt"))


class DefaultScopeTests(TestCase):
    def setUp(self) -> None:
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ.pop("HEARTH_SCOPE", None)

    def tearDown(self) -> None:
        if self._previous is not None:
            os.environ["HEARTH_SCOPE"] = self._previous

    def test_unset_env_defaults_to_repo_root(self) -> None:
        self.assertEqual(scope_roots(), [REPO_ROOT])
        self.assertEqual(scope_root(), REPO_ROOT)
