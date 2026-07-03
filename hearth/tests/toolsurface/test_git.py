from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface.git import git_commit_push, git_diff, git_log, git_status


def _run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=60, check=True)
    return proc.stdout.strip()


class GitToolTests(TestCase):
    """Tools exercised against a throwaway repo with a local bare remote."""

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

        self.remote = self.scope / "remote.git"
        _run(["init", "--bare", str(self.remote)], cwd=self.scope)
        self.repo = self.scope / "repo"
        self.repo.mkdir()
        _run(["init", "-b", "master"], cwd=self.repo)
        _run(["config", "user.email", "hearth@test.local"], cwd=self.repo)
        _run(["config", "user.name", "hearth-test"], cwd=self.repo)
        _run(["remote", "add", "origin", str(self.remote)], cwd=self.repo)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _run(["add", "-A"], cwd=self.repo)
        _run(["commit", "-m", "seed"], cwd=self.repo)
        _run(["push", "-u", "origin", "master"], cwd=self.repo)

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)

    def test_status_clean_then_dirty(self) -> None:
        clean = git_status("repo")
        self.assertTrue(clean["clean"])
        self.assertEqual(clean["branch"], "master")

        (self.repo / "new.txt").write_text("x\n", encoding="utf-8")
        dirty = git_status("repo")
        self.assertFalse(dirty["clean"])
        self.assertIn("new.txt", [entry["path"] for entry in dirty["entries"]])

    def test_diff_shows_unstaged_change_and_truncates(self) -> None:
        (self.repo / "seed.txt").write_text("seed\nmore\n", encoding="utf-8")
        diff = git_diff("repo")
        self.assertIn("+more", diff["diff"])
        self.assertFalse(diff["truncated"])

        tiny = git_diff("repo", max_bytes=10)
        self.assertTrue(tiny["truncated"])
        self.assertLessEqual(len(tiny["diff"].encode("utf-8")), 10)

    def test_log_returns_recent_commits(self) -> None:
        log = git_log("repo", n=5)
        self.assertEqual(log["count"], 1)
        self.assertEqual(log["commits"][0]["subject"], "seed")

    def test_commit_push_stages_commits_and_pushes(self) -> None:
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        result = git_commit_push("add feature", repo="repo", push=True)
        self.assertTrue(result["committed"])
        self.assertTrue(result["pushed"])
        self.assertEqual(result["sha"], _run(["rev-parse", "HEAD"], cwd=self.repo))
        self.assertEqual(_run(["rev-parse", "master"], cwd=self.remote), result["sha"])

    def test_clean_tree_still_pushes(self) -> None:
        """The F/0 fleet bug: nothing-to-commit must NOT skip the push."""
        (self.repo / "local.txt").write_text("local only\n", encoding="utf-8")
        _run(["add", "-A"], cwd=self.repo)
        _run(["commit", "-m", "local-only commit"], cwd=self.repo)
        local_sha = _run(["rev-parse", "HEAD"], cwd=self.repo)
        self.assertNotEqual(_run(["rev-parse", "master"], cwd=self.remote), local_sha)

        result = git_commit_push("sync", repo="repo", push=True)
        self.assertFalse(result["committed"])  # tree was clean
        self.assertTrue(result["pushed"])      # but the push still ran
        self.assertEqual(result["sha"], local_sha)
        self.assertEqual(_run(["rev-parse", "master"], cwd=self.remote), local_sha)
        self.assertIn("nothing to commit", result["summary"])

    def test_commit_without_push_leaves_remote_untouched(self) -> None:
        remote_before = _run(["rev-parse", "master"], cwd=self.remote)
        (self.repo / "unpushed.txt").write_text("u\n", encoding="utf-8")
        result = git_commit_push("unpushed", repo="repo", push=False)
        self.assertTrue(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertEqual(_run(["rev-parse", "master"], cwd=self.remote), remote_before)

    def test_empty_message_rejected(self) -> None:
        with self.assertRaises(ValueError):
            git_commit_push("   ", repo="repo")

    def test_repo_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            git_status("../somewhere-else")

    def test_status_on_non_repo_directory_raises(self) -> None:
        (self.scope / "plain").mkdir()
        with self.assertRaises(ValueError):
            git_status("plain")
