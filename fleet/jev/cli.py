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
from fleet.jev.status_view import render_status

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


def finish_lap(status, next_poll_s):
    # This is a control-loop hint, not a refresh of the hardware observation time.
    status['next_poll_s'] = next_poll_s
    atomic_json(HOME / 'status.json', status)
    return next_poll_s


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
        return finish_lap(status, 1)
    if not prepared.get('candidates'):
        active = prepared.get('active_build') and prepared.get('wait_reason') == 'work_in_progress'
        return finish_lap(status, 5 if active else 30)
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
            return finish_lap(status, 30)
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
    return finish_lap(status, 5 if selected else 30)


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
    return render_status(status, get_daemon_status(), time.time())


def main():
    parser = argparse.ArgumentParser(prog='fleet-scheduler')
    parser.add_argument('action', choices=['run-once', 'serve', 'status', 'budget', 'quality', 'decision'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.action == 'decision':
        from fleet.jev.decision_view import summarize_decision
        try:
            record = json.loads((HOME / 'last-decision.json').read_text())
        except (OSError, UnicodeError, ValueError):
            record = None
        value = summarize_decision(record)
        if args.json:
            print(json.dumps(value, indent=2, allow_nan=False))
        elif not value['available']:
            print('Saved JEV decision: unavailable; record missing, malformed, or inconsistent.')
        else:
            print('\n'.join((
                f"Saved JEV decision: {value['state']}; candidate {value['candidate_id'] or 'none'}",
                f"Quality history used: {value['quality_history_sha256'] or 'not recorded'}",
                f"Current admission thresholds: fit >= {policy.MIN_FIT:g}; confidence >= {policy.MIN_CONFIDENCE:g}; "
                f"ambiguity <= {policy.MAX_AMBIGUITY:g}",
                'Task/profile fit is not patch correctness or current capacity.',
            )))
            for row in value['judgments']:
                print(f"{row['candidate_id']}: fit {row['score']:g}/3; confidence {row['confidence']:g}; "
                      f"ambiguity {row['ambiguity']:g}; eligible {str(row['eligible']).lower()}")
        return
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
                next_poll_s = asyncio.run(lap())
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
            time.sleep(next_poll_s)


if __name__ == '__main__':
    main()
