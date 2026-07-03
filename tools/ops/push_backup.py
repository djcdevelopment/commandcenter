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

    # Check if there are any staged changes
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip()
    except subprocess.CalledProcessError:
        staged_files = ''

    # If dry-run, just show what would happen
    if len(sys.argv) > 1 and sys.argv[1] == '--dry-run':
        print('Would stage all files:')
        try:
            result = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], capture_output=True, text=True, check=True)
            untracked = result.stdout.strip().split('\n')
            if untracked and untracked != ['']:
                for f in untracked:
                    print(f'  git add {f}')
            else:
                print('  (no untracked files)')
        except subprocess.CalledProcessError:
            pass

        if staged_files:
            print('Would commit with message:')
            print(f'  backup: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}')
        else:
            print('No staged changes to commit.')
        print('Would push to origin (non-force)')
        return

    # Otherwise, proceed with actual backup
    # Stage all files
    try:
        subprocess.run(['git', 'add', '-A'], check=True)
    except subprocess.CalledProcessError:
        print('Error: failed to stage files', file=sys.stderr)
        sys.exit(1)

    # Check if any files were staged
    try:
        result = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, check=True)
        staged_files = result.stdout.strip()
    except subprocess.CalledProcessError:
        staged_files = ''

    if not staged_files:
        print('No changes to commit. Nothing to backup.', file=sys.stderr)
        return

    # Commit with timestamped message
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    commit_msg = f'backup: {stamp}'
    try:
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
    except subprocess.CalledProcessError:
        print('Error: failed to commit changes', file=sys.stderr)
        sys.exit(1)

    # Push to origin (non-force)
    try:
        subprocess.run(['git', 'push', 'origin', 'HEAD'], check=True)
    except subprocess.CalledProcessError:
        print('Error: failed to push to origin', file=sys.stderr)
        sys.exit(1)

    print(f'Backup completed: {commit_msg}')


if __name__ == '__main__':
    main()