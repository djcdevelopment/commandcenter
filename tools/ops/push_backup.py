import os
import sys
import subprocess
import datetime
import argparse


def main():
    parser = argparse.ArgumentParser(description='Backup the corpus and push to origin')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be done without doing it')
    args = parser.parse_args()

    # Check for origin remote
    try:
        result = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, check=True)
        if 'origin' not in result.stdout:
            print('Error: no remote named origin configured', file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError:
        print('Error: no remote named origin configured', file=sys.stderr)
        sys.exit(1)

    # Get current timestamp
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')

    # Stage all changes
    try:
        result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
        if result.returncode != 0:
            print('Error: git add -A failed', file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f'Error: failed to stage changes: {e}', file=sys.stderr)
        sys.exit(1)

    # Check if there are staged changes
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip()
        if not staged_files:
            if args.dry_run:
                print(f'No changes to commit. Would skip commit and push for stamp {stamp}')
            return
    except Exception as e:
        print(f'Error: failed to check staged files: {e}', file=sys.stderr)
        sys.exit(1)

    # Commit with message
    commit_msg = f'backup: {stamp} corpus snapshot'
    try:
        result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True, check=True)
        if args.dry_run:
            print(f'DRY-RUN: Would commit with message: {commit_msg}')
    except subprocess.CalledProcessError as e:
        print(f'Error: git commit failed: {e.stderr}', file=sys.stderr)
        sys.exit(1)

    # Push to origin
    try:
        result = subprocess.run(['git', 'push', 'origin', 'HEAD'], capture_output=True, text=True, check=True)
        if args.dry_run:
            print(f'DRY-RUN: Would push to origin')
        else:
            print(f'Pushed to origin with commit {stamp}')
    except subprocess.CalledProcessError as e:
        print(f'Error: git push failed: {e.stderr}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()