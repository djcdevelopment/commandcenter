import os
import sys
import subprocess
import datetime
import time
from pathlib import Path

# Configuration
REMOTE_NAME = "origin"
BRANCH_NAME = "master"

# Utility: check if git is available
def git_available():
    try:
        subprocess.run(["git", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

# Utility: get current git remote
def get_remote_url():
    try:
        result = subprocess.run([
            "git", "config", "--get", f"remote.{REMOTE_NAME}.url"
        ], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

# Utility: check if remote exists
def remote_exists():
    return get_remote_url() is not None

# Utility: get current branch
def get_current_branch():
    try:
        result = subprocess.run([
            "git", "rev-parse", "--abbrev-ref", "HEAD"
        ], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

# Utility: get current timestamp for STAMP
def get_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

# Main function
def main():
    # Check git availability
    if not git_available():
        print("Error: git is not available or not installed", file=sys.stderr)
        sys.exit(1)

    # Check if remote exists
    if not remote_exists():
        print(f"Error: remote '{REMOTE_NAME}' is not configured", file=sys.stderr)
        sys.exit(1)

    # Check if we're on the correct branch
    current_branch = get_current_branch()
    if current_branch != BRANCH_NAME:
        print(f"Error: not on branch '{BRANCH_NAME}' (currently on '{current_branch}')", file=sys.stderr)
        sys.exit(1)

    # Parse command line arguments
    dry_run = False
    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        dry_run = True

    # Get the current timestamp
    stamp = get_timestamp()

    # Get staged status
    result = subprocess.run([
        "git", "status", "--porcelain"
    ], capture_output=True, text=True)
    staged_changes = result.stdout.strip()

    # If no changes, exit cleanly
    if not staged_changes:
        if dry_run:
            print(f"[DRY-RUN] No changes to stage. Would skip commit and push.")
        else:
            print("No changes to commit. Nothing to push.")
        return

    # Stage all changes
    if dry_run:
        print(f"[DRY-RUN] Would stage all changes (git add -A)")
    else:
        subprocess.run(["git", "add", "-A"], check=True)

    # Commit if not dry-run
    commit_message = f"backup: {stamp} corpus snapshot"
    if dry_run:
        print(f"[DRY-RUN] Would commit with message: '{commit_message}'")
    else:
        subprocess.run(["git", "commit", "-m", commit_message], check=True)

    # Push if not dry-run
    if dry_run:
        print(f"[DRY-RUN] Would push to remote '{REMOTE_NAME}' on branch '{BRANCH_NAME}'")
    else:
        try:
            subprocess.run([
                "git", "push", REMOTE_NAME, BRANCH_NAME
            ], check=True)
            print(f"Successfully pushed to {REMOTE_NAME} on branch {BRANCH_NAME}")
        except subprocess.CalledProcessError as e:
            print(f"Error: Failed to push to remote '{REMOTE_NAME}'", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
