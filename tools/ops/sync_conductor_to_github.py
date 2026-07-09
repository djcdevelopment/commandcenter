from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Dataflow: cc-conductor (local bare repo, no GitHub credential) -> OMEN (this machine,
# already gh-authenticated) -> GitHub. The conductor never holds a GitHub token; OMEN
# mirror-clones the conductor's repo over SSH, then pushes that mirror to GitHub. See
# docs/conductor-github-sync.md.

REPO_ROOT = Path(__file__).resolve().parents[2]
MIRROR_DIR = REPO_ROOT.parent / "conductor-mirror.git"
CONDUCTOR_SSH_URL = "ssh://claude@cc-conductor.mshome.net/home/claude/work/commandcenter.git"  # local vswitch, not tailnet (ADR-0014)
GITHUB_URL = "https://github.com/djcdevelopment/conductor.git"
BRANCH = "main"


def run(*args: str, cwd: Path | None = None, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, check=True, text=True, capture_output=capture_output)


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    dry_run = argv == ["--dry-run"]
    if argv and not dry_run:
        return fail("Usage: python tools/ops/sync_conductor_to_github.py [--dry-run]")

    if dry_run:
        if MIRROR_DIR.exists():
            print(f"Would run: git --git-dir={MIRROR_DIR} fetch --prune {CONDUCTOR_SSH_URL}")
        else:
            print(f"Would run: git clone --mirror {CONDUCTOR_SSH_URL} {MIRROR_DIR}")
        print(f"Would run: git --git-dir={MIRROR_DIR} push {GITHUB_URL} refs/heads/{BRANCH}:refs/heads/{BRANCH}")
        return 0

    try:
        if MIRROR_DIR.exists():
            run("git", f"--git-dir={MIRROR_DIR}", "fetch", "--prune", CONDUCTOR_SSH_URL,
                f"+refs/heads/*:refs/heads/*")
        else:
            run("git", "clone", "--mirror", CONDUCTOR_SSH_URL, str(MIRROR_DIR))

        before = subprocess.run(
            ["git", f"--git-dir={MIRROR_DIR}", "rev-parse", f"refs/heads/{BRANCH}"],
            check=False, text=True, capture_output=True,
        ).stdout.strip()

        run("git", f"--git-dir={MIRROR_DIR}", "push", GITHUB_URL, f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")

        after = subprocess.run(
            ["git", f"--git-dir={MIRROR_DIR}", "rev-parse", f"refs/heads/{BRANCH}"],
            check=False, text=True, capture_output=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(exc.cmd)
        return fail(f"Error: command failed: {cmd}")

    print(f"Synced cc-conductor -> OMEN mirror ({MIRROR_DIR}) -> github.com/djcdevelopment/conductor")
    print(f"{BRANCH}: {before or '(none)'} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
