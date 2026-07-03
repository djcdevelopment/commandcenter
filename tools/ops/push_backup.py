import os
import sys
import subprocess
import datetime
import argparse


def main():
    parser = argparse.ArgumentParser(description='Backup the corpus and push to origin.')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be done without changing anything')
    args = parser.parse_args()

    # Check if origin remote exists
    try:
        result = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, check=True)
        if 'origin' not in result.stdout:
            print('Error: no remote named origin configured', file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError:
        print('Error: no remote named origin configured', file=sys.stderr)
        sys.exit(1)

    # Stage all changes
    try:
        subprocess.run(['git', 'add', '-A'], check=True)
    except subprocess.CalledProcessError as e:
        print(f'Error: failed to stage changes: {e}', file=sys.stderr)
        sys.exit(1)

    # Check if there are any staged changes
    try:
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        staged_changes = result.stdout.strip()
        if not staged_changes:
            if args.dry_run:
                print('No changes to commit. Nothing to backup.')
            return
    except subprocess.CalledProcessError as e:
        print(f'Error: failed to check staged changes: {e}', file=sys.stderr)
        sys.exit(1)

    # Generate timestamp
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    commit_msg = f'backup: {stamp} corpus snapshot'

    # Commit if not dry-run
    if not args.dry_run:
        try:
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
        except subprocess.CalledProcessError as e:
            print(f'Error: failed to commit: {e}', file=sys.stderr)
            sys.exit(1)

    # Push to origin (non-force)
    if not args.dry_run:
        try:
            subprocess.run(['git', 'push', 'origin', 'HEAD'], check=True)
        except subprocess.CalledProcessError as e:
            print(f'Error: failed to push: {e}', file=sys.stderr)
            sys.exit(1)
    else:
        # Dry-run: print actions
        print(f'Would stage all changes (git add -A)')
        print(f'Would commit with message: "{commit_msg}"')
        print(f'Would push to origin: git push origin HEAD')


if __name__ == '__main__':
    main()