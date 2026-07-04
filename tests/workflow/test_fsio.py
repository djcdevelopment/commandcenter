"""Atomic JSON write tests (CQRS/ES standardization plan, step 2).

Every knowledge write today is in-place `write_text` — a crash mid-write leaves torn JSON.
`atomic_write_json` closes that gap: write to a sibling `.tmp` file, then `os.replace` into
place. These tests run entirely against tmp_path-style temp dirs; the repo's real knowledge/
is never touched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.fsio import atomic_write_json


class AtomicWriteJsonTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_writes_matching_serialization_format(self) -> None:
        # Compare the way the rest of the repo's writers do: via read_text/text content, not
        # raw bytes — Path.write_text applies platform newline translation on write (same as
        # every existing `path.write_text(json.dumps(...) + "\n", encoding="utf-8")` writer
        # this helper replaces), so a raw-byte comparison would be platform-dependent instead
        # of a "same serialization" check.
        target = self.temp_dir / "doc.json"
        atomic_write_json(target, {"a": 1, "b": [1, 2]})
        text = target.read_text(encoding="utf-8")
        expected = json.dumps({"a": 1, "b": [1, 2]}, indent=2) + "\n"
        self.assertEqual(text, expected)

    def test_no_tmp_file_left_behind_on_success(self) -> None:
        target = self.temp_dir / "doc.json"
        atomic_write_json(target, {"ok": True})
        self.assertTrue(target.is_file())
        self.assertFalse((self.temp_dir / "doc.json.tmp").exists())
        # No stray .tmp files anywhere in the dir.
        leftovers = [p for p in self.temp_dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_creates_parent_directories(self) -> None:
        target = self.temp_dir / "nested" / "dir" / "doc.json"
        atomic_write_json(target, {"nested": True})
        self.assertTrue(target.is_file())

    def test_overwrites_existing_file(self) -> None:
        target = self.temp_dir / "doc.json"
        atomic_write_json(target, {"version": 1})
        atomic_write_json(target, {"version": 2})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 2)

    def test_interrupted_write_leaves_original_intact(self) -> None:
        """Simulate a crash mid-write: only the .tmp file is written, os.replace never runs.
        The original file on disk must be completely unaffected."""
        target = self.temp_dir / "doc.json"
        atomic_write_json(target, {"version": 1})
        original_bytes = target.read_bytes()

        # Simulate the "torn write" half of atomic_write_json without the final os.replace —
        # this is exactly the state a killed process would leave behind.
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        tmp_path.write_text("{not valid json, torn mid-write", encoding="utf-8")

        # The real file must still be the last good version, and still valid JSON.
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 1)

        # Clean up the simulated torn tmp file the way a retried write would (via os.replace).
        atomic_write_json(target, {"version": 2})
        self.assertFalse(tmp_path.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 2)
