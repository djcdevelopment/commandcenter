import os
import sys
import subprocess
import datetime
import time

from pathlib import Path


def main():
    # Check for origin remote
    try:
        result = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, check=True)
        if 'origin' not in result.stdout:
            print('Error: no remote named origin configured', file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError:
        print('Error: no remote named origin configured', file=sys.stderr)
        sys.exit(1)

    # Check if there are staged changes
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except subprocess.CalledProcessError:
        print('Error: failed to check staged changes', file=sys.stderr)
        sys.exit(1)

    # Parse command line args
    dry_run = '--dry-run' in sys.argv

    # Stage all changes
    if not dry_run:
        try:
            subprocess.run(['git', 'add', '-A'], check=True)
        except subprocess.CalledProcessError:
            print('Error: failed to stage changes', file=sys.stderr)
            sys.exit(1)

    # Get current timestamp
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')

    # Print dry-run plan
    if dry_run:
        print(f'Would stage all changes (git add -A)')
        if staged_files:
            print(f'Would commit with message: backup: {stamp} corpus snapshot')
            print(f'Would push to origin')
        else:
            print(f'No changes to commit. Would push nothing.')
        return

    # Commit only if there are staged changes
    if not staged_files:
        print('No changes to commit. Skipping commit and push.')
        return

    # Commit
    try:
        subprocess.run(['git', 'commit', '-m', f'backup: {stamp} corpus snapshot'], check=True)
    except subprocess.CalledProcessError:
        print('Error: failed to commit changes', file=sys.stderr)
        sys.exit(1)

    # Push
    try:
        subprocess.run(['git', 'push', 'origin', 'HEAD'], check=True)
    except subprocess.CalledProcessError:
        print('Error: failed to push to origin', file=sys.stderr)
        sys.exit(1)

    print(f'Successfully pushed backup: {stamp}')


if __name__ == '__main__':
    main()