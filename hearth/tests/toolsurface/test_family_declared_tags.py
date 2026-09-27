"""A per-host routing-families file may declare a family's routing tags.

Earned by the 2026-09-27 Linux cutover: the Linux pool has lanes (omen-dense-27b:
dense/quality/agent; fx99-vllm: utility) that the packaged FAMILY_TAGS table cannot
name without breaking the Windows pool's tests, so `tags` became declarable data in
the file named by HEARTH_ROUTING_FAMILIES (see ~/hearth-production/routing-families-linux.toml).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.scheduler import families as fam

MINIMAL = '''
contract = "routing-families.v1"
[family.default]
model_id = "m0"
evidence = "e"
reason = "r"
[family.code_fix]
model_id = "m1"
tags = ["agent"]
evidence = "e"
reason = "r"
'''


class DeclaredTagsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "families.toml"
        self.path.write_text(MINIMAL)
        fam._DECLARED_TAGS_CACHE.clear()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        fam._DECLARED_TAGS_CACHE.clear()

    def test_declared_tags_win_for_families_the_table_does_not_know(self) -> None:
        with mock.patch.dict(os.environ, {fam.ENV_VAR: str(self.path)}):
            self.assertEqual(fam.tags_for("code_fix"), ["agent"])
            self.assertEqual(fam.tags_for("summarization"), fam.FAMILY_TAGS["summarization"])
            self.assertEqual(fam.tags_for("banana_peeling"), fam.FAMILY_TAGS["default"])

    def test_loader_exposes_tags_and_rejects_bad_shapes(self) -> None:
        loaded = fam.load_families(self.path)
        self.assertEqual(loaded.get("code_fix").tags, ("agent",))
        self.assertIsNone(loaded.get("default").tags)
        self.path.write_text(MINIMAL.replace('tags = ["agent"]', 'tags = []'))
        with self.assertRaises(fam.FamiliesConfigError):
            fam.load_families(self.path)

    def test_packaged_file_declares_no_tags_so_the_table_still_rules(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(fam.ENV_VAR, None)
            self.assertEqual(fam.tags_for("reasoning_planning"), ["reasoning"])

    def test_missing_file_is_silent_for_tags(self) -> None:
        with mock.patch.dict(os.environ, {fam.ENV_VAR: str(self.path) + ".missing"}):
            self.assertEqual(fam.tags_for("code_fix"), fam.FAMILY_TAGS["default"])


if __name__ == "__main__":
    unittest.main()
