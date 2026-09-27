"""git apply --recount is a mechanical repair, not a retry.

Earned 2026-09-27 (omen-linux, first Linux local-work candidate): the 27B wrote a
correct two-hunk patch whose second @@ header carried wrong line counts, and the strict
`git apply --check` rejected it as "corrupt patch at line 36". With --recount the same
bytes apply; the manifest must say so.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.localwork.service import LocalWorkService, LocalWorkError


def _repo(tmp: Path) -> tuple[Path, str]:
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    (tmp / "a.txt").write_text("one\ntwo\nthree\nfour\n")
    subprocess.run(["git", "-C", str(tmp), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(tmp), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return tmp, sha


GOOD_HUNK_BAD_COUNTS = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,9 +1,9 @@
 one
-two
+TWO
 three
"""

BROKEN = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-zzz
+TWO
 three
"""


class RecountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo, self.sha = _repo(Path(self.tmp.name))
        self.svc = LocalWorkService.__new__(LocalWorkService)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _manifest(self):
        return {"repo": str(self.repo), "base_commit": self.sha, "declared_paths": ["a.txt"],
                "artifact_kind": "unified_diff", "target_path": None}

    def test_wrong_counts_pass_with_recount_and_are_noted(self) -> None:
        notes = self.svc._validate_candidate(self._manifest(), {"citations": [], "content": GOOD_HUNK_BAD_COUNTS, "target_path": None})
        self.assertEqual(notes.get("git_apply"), "recount")
        self.assertIn("corrupt patch", notes.get("git_apply_strict_error", ""))

    def test_wrong_content_still_fails(self) -> None:
        with self.assertRaises(LocalWorkError):
            self.svc._validate_candidate(self._manifest(), {"citations": [], "content": BROKEN, "target_path": None})


if __name__ == "__main__":
    unittest.main()
