"""FX99 scheduler: no resident model, no shell supplied by JEV, one process."""
import argparse
import asyncio
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from fleet.jev import policy
from fleet.jev.client import atomic_json, evaluate
from fleet.jev.quality import load_quality, quality_line, with_quality

HOME = Path(os.environ.get('FLEET_SCHEDULER_HOME', '/home/derek/.local/state/fleet-scheduler'))


async def connect_call(name, args):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    key_file = Path(os.environ.get('FLEET_SCHEDULER_HEARTH_KEY', '/home/derek/.config/fleet-scheduler/hearth.key'))
    endpoint = os.environ.get('FLEET_SCHEDULER_MCP', 'http://127.0.0.1:8713/mcp')
    async with streamablehttp_client(endpoint, headers={'X-Hearth-Key': key_file.read_text().strip()}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            answer = await asyncio.wait_for(session.call_tool(name, args), timeout=120)
            if answer.isError:
                raise RuntimeError('gateway_call_refused')
            value = answer.structuredContent
            if not isinstance(value, dict):
                value = json.loads(next(c.text for c in answer.content if c.type == 'text'))
            if value.get('ok') is not True:
                raise RuntimeError(value.get('reason_code', 'gateway_operation_refused'))
            return value


async def lap():
    prepared = await connect_call('scheduler_prepare', {})
    status = {'scheduler': 'ready', 'gpu_reservation': False, 'active_build': prepared.get('active_build'),
              'pending_reviews': len(prepared.get('pending_reviews', [])),
              'reviewer': prepared.get('reviewer', 'unloaded'), 'wait_reason': prepared.get('wait_reason'),
              'observed_at': time.time()}
    atomic_json(HOME / 'status.json', status)
    if prepared.get('pending_reviews'):
        from fleet.jev.review import run_review
        task_id = prepared['pending_reviews'][0]
        status.update(reviewer='requested', selected_task=task_id)
        atomic_json(HOME / 'status.json', status)
        await run_review(task_id, connect_call, HOME)
        status.update(reviewer='unloaded', pending_reviews=0)
        atomic_json(HOME / 'status.json', status)
        return
    if not prepared.get('candidates'):
        return
    quality = load_quality(HOME / 'quality-history.json')
    state = policy.cloud_state(with_quality(prepared, quality))
    material = policy.digest({'state': state, 'knowledge': prepared.get('knowledge_digest'),
                              'policy': prepared.get('policy_version'),
                              'decision_request': policy.digest(policy.request_for(state))})
    previous_file = HOME / 'last-decision.json'
    previous = json.loads(previous_file.read_text()) if previous_file.exists() else {}
    if previous.get('material') == material:
        if not previous.get('candidate_id') or previous.get('dispatched'):
            status['wait_reason'] = previous.get('wait_reason') or 'unchanged_decision_inputs'
            atomic_json(HOME / 'status.json', status)
            return
        # Reuse judgments with a fresh local snapshot, never repeat cloud spend.
        selected = previous['candidate_id']
        evidence = {**previous, 'snapshot_id': prepared['snapshot_id']}
    else:
        answer, usage = await asyncio.to_thread(evaluate, state, HOME)
        selected, judgments = policy.evaluate(answer, prepared['candidates'])
        evidence = {'material': material, 'usage': usage, 'judgments': judgments,
                    'candidate_id': selected, 'snapshot_id': prepared['snapshot_id'],
                    'wait_reason': None if selected else 'needs_clarification'}
    if quality.get('available') is True:
        evidence['quality_history_sha256'] = quality['history_sha256']
    atomic_json(previous_file, evidence)
    atomic_json(HOME / 'decisions' / (evidence['usage']['request_sha256'] + '.json'), evidence)
    if selected:
        accepted = await connect_call('scheduler_select', {'candidate_id': selected,
                   'snapshot_id': prepared['snapshot_id'], 'evidence': evidence})
        status.update(selected_task=accepted['task_id'], active_build=accepted['receipt_id'])
        atomic_json(previous_file, {**evidence, 'dispatched': True})
    else:
        status['wait_reason'] = 'needs_clarification'
    atomic_json(HOME / 'status.json', status)


def get_daemon_status():
    """Observe the actual systemd owner; cached status is never liveness proof."""
    try:
        result = subprocess.run([
            'systemctl', '--user', 'show', 'fleet-scheduler.service',
            '--property=ActiveState', '--property=MainPID',
        ], capture_output=True, text=True, timeout=2)
        if result.returncode:
            return 'unknown'
        fields = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition('=')
            if not separator or key not in ('ActiveState', 'MainPID') or key in fields:
                return 'unknown'
            fields[key] = value
        if set(fields) != {'ActiveState', 'MainPID'} or not re.fullmatch(r'[0-9]{1,10}', fields['MainPID']):
            return 'unknown'
        if fields['ActiveState'] in ('inactive', 'failed'):
            return 'stopped'
        pid = int(fields['MainPID'])
        if fields['ActiveState'] != 'active' or not 0 < pid < 2**31:
            return 'unknown'
        os.kill(pid, 0)
        return 'running'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


# Status layout originated in OMEN candidate afa3331; validation and preservation
# of the scheduler functions below were corrected independently by Codex.
def format_status_human_readable(status):
    status = status if isinstance(status, dict) else {}
    def label(name, allowed):
        value = status.get(name)
        return value if isinstance(value, str) and value in allowed else 'unknown'
    scheduler = label('scheduler', {'ready', 'held', 'not_started'})
    reviewer = label('reviewer', {'unloaded', 'requested', 'recovery_required', 'unknown'})
    pending = status.get('pending_reviews')
    pending = str(pending) if type(pending) is int and pending >= 0 else 'unknown'
    build = status.get('active_build')
    build = ('none' if build is None else build if isinstance(build, str)
             and re.fullmatch(r'br-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', build) else 'unknown')
    observed = status.get('observed_at')
    now = time.time()
    age = 'unknown'
    if type(observed) in (int, float) and 0 <= observed <= now and math.isfinite(observed):
        seconds = now - observed
        age = f'{seconds:.1f} seconds (' + ('stale' if seconds > 90 else 'fresh') + ')'
    return '\n'.join((
        'Daemon state: ' + get_daemon_status(),
        'Cached snapshot age: ' + age,
        'Last cycle scheduler state: ' + scheduler,
        'Last observed active build: ' + build,
        'Last observed pending review count: ' + pending,
        'LAST OBSERVED reviewer state: ' + reviewer,
        'Note: cached observations are not live hardware residency',
    ))


def main():
    parser = argparse.ArgumentParser(prog='fleet-scheduler')
    parser.add_argument('action', choices=['run-once', 'serve', 'status', 'budget', 'quality'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.action == 'quality':
        value = load_quality(HOME / 'quality-history.json')
        print(json.dumps(value, indent=2) if args.json else
              quality_line(value) or 'Work quality: unavailable; no verified history was loaded.')
        return
    if args.action == 'budget':
        from fleet.jev.budget_view import summarize_budget
        try:
            ledger = json.loads((HOME / 'api-budget.json').read_text())
        except (OSError, UnicodeError, ValueError):
            ledger = None
        value = summarize_budget(ledger, policy.INPUT_USD_PER_TOKEN, policy.MAX_COST_USD)
        if args.json:
            print(json.dumps(value, indent=2, allow_nan=False))
        elif not value['available']:
            print('API budget: unavailable; ledger missing, malformed, or inconsistent.')
        else:
            print('\n'.join((
                f"API attempts: {value['calls']}; known input tokens: {value['input_tokens']}",
                f"Usage-based estimate: USD {value['usage_estimate_usd']:.9f}",
                f"Uncertain reservations: USD {value['uncertain_reserved_usd']:.9f}",
                f"Booked against cap: USD {value['booked_usd']:.9f} / {policy.MAX_COST_USD:.9f}; "
                f"remaining USD {value['remaining_usd']:.9f}",
                'Reservations are not confirmed provider charges.',
            )))
        return
    if args.action == 'status':
        try:
            value = json.loads((HOME / 'status.json').read_text())
        except FileNotFoundError:
            value = {'scheduler': 'not_started'}
        except (OSError, UnicodeError, ValueError):
            value = {'scheduler': 'unknown'}
        print(json.dumps(value, indent=2) if args.json else format_status_human_readable(value))
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
