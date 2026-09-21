"""FX99 scheduler: no resident model, no shell supplied by JEV, one process."""
import argparse
import asyncio
import fcntl
import json
import os
import subprocess
import time
import signal
from pathlib import Path

from fleet.jev import policy
from fleet.jev.client import atomic_json, evaluate

HOME = Path(os.environ.get('FLEET_SCHEDULER_HOME', '/home/derek/.local/state/fleet-scheduler'))


def get_daemon_status():
    try:
        result = subprocess.run([
            'systemctl', '--user', 'show', 'fleet-scheduler.service',
            '--property=ActiveState', '--property=MainPID'
        ], capture_output=True, text=True, timeout=2)
        
        if result.returncode != 0:
            return 'unknown'
        
        # Parse the output as KEY=VALUE pairs
        state = None
        pid = None
        for line in result.stdout.strip().split('\n'):
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key == 'ActiveState':
                state = value.strip()
            elif key == 'MainPID':
                try:
                    pid = int(value.strip())
                except (ValueError, TypeError):
                    return 'unknown'
        
        # Check if service is active
        if state != 'active':
            return 'stopped'
        
        # Check if PID is positive
        if not isinstance(pid, int) or pid <= 0:
            return 'unknown'
        
        # Try to send signal 0 to check if process exists
        try:
            os.kill(pid, 0)
        except OSError:
            return 'stopped'
        
        return 'running'
    except (subprocess.TimeoutExpired, Exception):
        return 'unknown'

def validate_pending_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 'unknown'
    return value

def validate_build_id(value):
    if not isinstance(value, str):
        return 'none'
    import re
    if re.match(r'br-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', value):
        return value
    return 'none'

def validate_age(observed_at):
    if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
        return 'unknown'
    if observed_at < 0 or observed_at > time.time():
        return 'unknown'
    age = time.time() - observed_at
    if age > 90:
        return 'stale'
    return age

def format_status_human_readable(status):
    # Extract data from status
    scheduler_state = status.get('scheduler', 'not_started')
    pending_reviews = validate_pending_count(status.get('pending_reviews', 0))
    active_build = validate_build_id(status.get('active_build', ''))
    reviewer_state = status.get('reviewer', 'unknown')
    observed_at = status.get('observed_at')
    age = validate_age(observed_at)
    
    # Validate reviewer and scheduler states
    valid_reviewer_states = {'unloaded', 'requested', 'recovery_required', 'unknown'}
    if reviewer_state not in valid_reviewer_states:
        reviewer_state = 'unknown'
    
    valid_scheduler_states = {'ready', 'held', 'not_started'}
    if scheduler_state not in valid_scheduler_states:
        scheduler_state = 'unknown'
    
    # Get daemon status
    daemon_status = get_daemon_status()
    
    # Build human-readable output
    lines = [
        f'Daemon state: {daemon_status}',
        f'Cached snapshot age: {age if isinstance(age, str) else f"{age:.1f} seconds"}',
        f'Scheduler state: {scheduler_state}',
        f'Active build: {active_build if active_build != "none" else "none"}',
        f'Pending review count: {pending_reviews if pending_reviews != "unknown" else "unknown"}',
        f'LAST OBSERVED reviewer state: {reviewer_state}'
    ]
    
    # Add note about cached observations
    lines.append('Note: cached observations are not live hardware residency')
    
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(prog='fleet-scheduler')
    parser.add_argument('action', choices=['run-once', 'serve', 'status'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    
    if args.action == 'status':
        status_path = HOME / 'status.json'
        if not status_path.exists():
            # Return default JSON for missing file
            if args.json:
                print(json.dumps({'scheduler': 'not_started'}, indent=2))
            else:
                print(format_status_human_readable({'scheduler': 'not_started'}))
            return
        
        try:
            status = json.loads(status_path.read_text())
            
            # Preserve existing JSON output
            if args.json:
                print(json.dumps(status, indent=2))
                return
            
            # Format human-readable output
            print(format_status_human_readable(status))
            
        except (json.JSONDecodeError, Exception) as e:
            # Handle malformed/missing status data without crashing
            if args.json:
                # For JSON output, return default if file is malformed
                print(json.dumps({'scheduler': 'not_started'}, indent=2))
            else:
                # For human-readable output, show unknown fields
                print(format_status_human_readable({'scheduler': 'unknown', 'pending_reviews': 'unknown',
                                                  'active_build': 'unknown', 'reviewer': 'unknown'}))
        
        return
    
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    if (HOME / 'HOLD').exists():
        raise SystemExit('operator_hold_requires_review')
    with (HOME / 'process.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('scheduler_already_running')
        while not (HOME / 'STOP').exists():
            try:
                asyncio.run(lap())
            except Exception as error:
                # Only fixed codes from our own RuntimeErrors reach public status.
                code = str(error) if type(error) is RuntimeError and str(error).replace('_', '').isalnum() else type(error).__name__
                before = json.loads((HOME / 'status.json').read_text()) if (HOME / 'status.json').exists() else {}
                atomic_json(HOME / 'status.json', {**before, 'scheduler': 'held', 'wait_reason': code,
                                                  'gpu_reservation': False, 'observed_at': time.time()})
                # A failed lap ends automatic work. Operator inspects, removes HOLD,
                # and restarts explicitly; no recurring paid/auth-failure loop.
                atomic_json(HOME / 'HOLD', {'reason': code})
                if args.action == 'run-once':
                    raise SystemExit(code)
                break
            if args.action == 'run-once':
                break
            time.sleep(30)


if __name__ == '__main__':
    main()
