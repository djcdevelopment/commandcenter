import os
import sys
import subprocess
from datetime import datetime, timezone


def main():
    # Check for origin remote
    try:
        result = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, check=True)
        if 'origin' not in result.stdout:
            print('error: no remote named origin configured', file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError:
        print('error: failed to query git remotes', file=sys.stderr)
        sys.exit(1)

    # Check for staged changes
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except subprocess.CalledProcessError:
        print('error: failed to check staged changes', file=sys.stderr)
        sys.exit(1)

    # Parse command line args
    dry_run = '--dry-run' in sys.argv

    # Stage all changes
    if not dry_run:
        try:
            subprocess.run(['git', 'add', '-A'], check=True)
        except subprocess.CalledProcessError:
            print('error: failed to stage all changes', file=sys.stderr)
            sys.exit(1)

    # Get list of staged files
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except subprocess.CalledProcessError:
        print('error: failed to get staged files', file=sys.stderr)
        sys.exit(1)

    # Print dry-run plan
    if dry_run:
        print('DRY-RUN: would stage the following files:')
        for f in staged_files:
            print(f'  {f}')
        if staged_files:
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            print(f'DRY-RUN: would commit with message: backup: {stamp} corpus snapshot')
            print(f'DRY-RUN: would push to origin')
        else:
            print('DRY-RUN: no changes to commit')
        return

    # Commit only if there are staged changes
    if not staged_files:
        print('info: no changes to commit', file=sys.stderr)
        return

    # Create commit message
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    commit_msg = f'backup: {stamp} corpus snapshot'

    # Commit
    try:
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
    except subprocess.CalledProcessError:
        print('error: failed to commit changes', file=sys.stderr)
        sys.exit(1)

    # Push
    try:
        subprocess.run(['git', 'push', 'origin', 'HEAD'], check=True)
    except subprocess.CalledProcessError:
        print('error: failed to push to origin', file=sys.stderr)
        sys.exit(1)

    print(f'backup: successfully pushed to origin with commit {stamp}')


if __name__ == '__main__':
    main()