"""HEARTH tool provider: git (Stream H-B).

Subprocess-backed, non-interactive, sandboxed to HEARTH_SCOPE. `git_commit_push` is the
one audited stage+commit+push call — and it fixes the F/0 fleet bug: with push=True the
push runs even when the tree was already clean (nothing-to-commit is not push-skipping).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from hearth.toolsurface._scope import resolve_repo

_GIT_TIMEOUT_S = 60
_PUSH_TIMEOUT_S = 300


def _repo_path(repo: str) -> Path:
    # REPOSITORY authority, not filesystem (ADR-0019): a repo is a named grant.
    # Falls back to filesystem containment when the caller declares no
    # repo_access, which preserves pre-ADR-0019 behavior for legacy callers.
    path = resolve_repo(repo)
    if not path.is_dir():
        raise ValueError(f"repo is not a directory: {repo}")
    return path


def _git(args: list[str], repo_path: Path, timeout_s: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, timeout=timeout_s, env=env, check=False,
    )


def _tail(text: str, lines: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-lines:]) if text.strip() else ""


def git_status(repo: str = ".") -> dict:
    """Working-tree status of a sandboxed git repo: branch, cleanliness, changed paths."""
    repo_path = _repo_path(repo)
    proc = _git(["status", "--porcelain=v1", "--branch"], repo_path)
    if proc.returncode != 0:
        raise ValueError(f"git status failed in {repo}: {_tail(proc.stderr)}")
    lines = proc.stdout.splitlines()
    branch = lines[0].removeprefix("## ").split("...")[0] if lines else ""
    entries = [{"status": line[:2].strip(), "path": line[3:]} for line in lines[1:] if line.strip()]
    return {"repo": str(repo_path), "branch": branch, "clean": not entries,
            "changed_count": len(entries), "entries": entries}


def git_diff(repo: str = ".", staged: bool = False, max_bytes: int = 100_000) -> dict:
    """Unified diff of a sandboxed git repo (staged or unstaged), truncated to max_bytes."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    repo_path = _repo_path(repo)
    args = ["diff", "--no-color"] + (["--staged"] if staged else [])
    proc = _git(args, repo_path)
    if proc.returncode != 0:
        raise ValueError(f"git diff failed in {repo}: {_tail(proc.stderr)}")
    raw = proc.stdout.encode("utf-8", errors="replace")
    truncated = len(raw) > max_bytes
    return {"repo": str(repo_path), "staged": staged, "size": len(raw),
            "truncated": truncated, "diff": raw[:max_bytes].decode("utf-8", errors="replace")}


def git_log(repo: str = ".", n: int = 10) -> dict:
    """Recent commits of a sandboxed git repo: sha, author, ISO date, subject."""
    if n <= 0:
        raise ValueError("n must be positive")
    repo_path = _repo_path(repo)
    proc = _git(["log", f"-{n}", "--pretty=format:%H%x1f%an%x1f%aI%x1f%s"], repo_path)
    if proc.returncode != 0:
        raise ValueError(f"git log failed in {repo}: {_tail(proc.stderr)}")
    commits = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        sha, author, date, subject = line.split("\x1f", 3)
        commits.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return {"repo": str(repo_path), "count": len(commits), "commits": commits}


def git_commit_push(message: str, repo: str = ".", add_all: bool = True, push: bool = False) -> dict:
    """Stage, commit, and optionally push in one audited call; push runs even on a clean tree."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    repo_path = _repo_path(repo)
    notes: list[str] = []

    if add_all:
        add = _git(["add", "-A"], repo_path)
        if add.returncode != 0:
            raise ValueError(f"git add -A failed in {repo}: {_tail(add.stderr)}")

    commit = _git(["commit", "-m", message], repo_path)
    combined = (commit.stdout + commit.stderr).lower()
    committed = commit.returncode == 0
    if committed:
        notes.append("committed")
    elif "nothing to commit" in combined or "no changes added to commit" in combined:
        # The F/0 lesson: a clean tree is NOT an error and must NOT skip the push below.
        notes.append("nothing to commit (clean tree)")
    else:
        raise ValueError(f"git commit failed in {repo}: {_tail(commit.stdout + commit.stderr)}")

    rev = _git(["rev-parse", "HEAD"], repo_path)
    sha = rev.stdout.strip() if rev.returncode == 0 else None

    pushed = False
    if push:
        push_proc = _git(["push"], repo_path, timeout_s=_PUSH_TIMEOUT_S)
        pushed = push_proc.returncode == 0
        notes.append("pushed" if pushed else f"push FAILED: {_tail(push_proc.stderr)}")

    return {"committed": committed, "pushed": pushed, "sha": sha,
            "summary": f"{repo_path.name}@{(sha or 'no-head')[:12]}: " + "; ".join(notes)}


def get_tools() -> list[Callable]:
    return [git_status, git_diff, git_log, git_commit_push]
