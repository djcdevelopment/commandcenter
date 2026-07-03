from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_git(*args: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def has_origin_remote() -> bool:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def list_worktree_changes() -> list[str]:
    result = run_git("status", "--porcelain", capture_output=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def has_staged_changes() -> bool:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 1


def main(argv: list[str]) -> int:
    dry_run = argv == ["--dry-run"]
    if argv and not dry_run:
        return fail("Usage: python tools/ops/push_backup.py [--dry-run]")

    if not has_origin_remote():
        return fail("Error: remote 'origin' is not configured.")

    changes = list_worktree_changes()
    if dry_run:
        print("Would run: git add -A")
        if changes:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"Would commit: backup: {stamp} corpus snapshot")
            print("Would push: git push origin HEAD")
        else:
            print("No changes to commit or push.")
        return 0

    try:
        run_git("add", "-A")
        if not has_staged_changes():
            print("No changes to commit or push.")
            return 0
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run_git("commit", "-m", f"backup: {stamp} corpus snapshot")
        run_git("push", "origin", "HEAD")
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(exc.cmd)
        return fail(f"Error: command failed: {cmd}")

    print("Backup push completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
