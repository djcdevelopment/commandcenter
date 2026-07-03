from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface.fs import glob_files, list_dir, read_file, write_file


class ScopedTestCase(TestCase):
    """Each test gets its own sandbox via HEARTH_SCOPE."""

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


class FsRoundtripTests(ScopedTestCase):
    def test_write_then_read_roundtrip(self) -> None:
        written = write_file("sub/note.txt", "hello hearth", create_dirs=True)
        self.assertTrue(written["created"])
        self.assertEqual(written["bytes_written"], len(b"hello hearth"))

        result = read_file("sub/note.txt")
        self.assertEqual(result["content"], "hello hearth")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["size"], len(b"hello hearth"))

    def test_read_truncates_at_max_bytes(self) -> None:
        write_file("big.txt", "abcdefghij")
        result = read_file("big.txt", max_bytes=4)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["content"], "abcd")
        self.assertEqual(result["size"], 10)
        self.assertEqual(result["returned_bytes"], 4)

    def test_write_without_create_dirs_rejects_missing_parent(self) -> None:
        with self.assertRaises(ValueError):
            write_file("missing/parent.txt", "content")

    def test_overwrite_reports_overwrote(self) -> None:
        write_file("again.txt", "one")
        result = write_file("again.txt", "two")
        self.assertTrue(result["overwrote"])
        self.assertEqual(read_file("again.txt")["content"], "two")


class FsListGlobTests(ScopedTestCase):
    def test_list_dir_reports_kinds_and_sizes(self) -> None:
        write_file("a.txt", "aa")
        (self.scope / "child").mkdir()
        result = list_dir(".")
        by_name = {entry["name"]: entry for entry in result["entries"]}
        self.assertEqual(by_name["a.txt"]["kind"], "file")
        self.assertEqual(by_name["a.txt"]["size"], 2)
        self.assertEqual(by_name["child"]["kind"], "dir")

    def test_glob_files_recursive(self) -> None:
        write_file("pkg/mod.py", "x = 1", create_dirs=True)
        write_file("pkg/deep/other.py", "y = 2", create_dirs=True)
        write_file("pkg/readme.md", "-", create_dirs=True)
        result = glob_files("**/*.py", root="pkg")
        self.assertEqual(result["count"], 2)
        self.assertTrue(all(match.endswith(".py") for match in result["matches"]))

    def test_glob_rejects_escaping_pattern(self) -> None:
        with self.assertRaises(ValueError):
            glob_files("../*.txt")


class FsScopeEscapeTests(ScopedTestCase):
    def test_read_rejects_parent_escape(self) -> None:
        with self.assertRaises(ValueError):
            read_file("../outside.txt")

    def test_write_rejects_absolute_path_outside_scope(self) -> None:
        outside = Path(mkdtemp()).resolve()
        try:
            with self.assertRaises(ValueError):
                write_file(str(outside / "evil.txt"), "nope")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_absolute_path_inside_scope_is_allowed(self) -> None:
        target = self.scope / "ok.txt"
        write_file(str(target), "fine")
        self.assertEqual(read_file("ok.txt")["content"], "fine")

    def test_list_dir_rejects_escape(self) -> None:
        with self.assertRaises(ValueError):
            list_dir("..")
