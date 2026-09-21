"""FX99 scheduler: no resident model, no shell supplied by JEV, one process."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import time

from fleet.jev import policy
from fleet.jev.client import atomic_json, evaluate

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
    state = policy.cloud_state(prepared)
    material = policy.digest({'state': state, 'knowledge': prepared.get('knowledge_digest'),
                              'policy': prepared.get('policy_version')})
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
    atomic_json(previous_file, evidence)
    if selected:
        accepted = await connect_call('scheduler_select', {'candidate_id': selected,
                   'snapshot_id': prepared['snapshot_id'], 'evidence': evidence})
        status.update(selected_task=accepted['task_id'], active_build=accepted['receipt_id'])
        atomic_json(previous_file, {**evidence, 'dispatched': True})
    else:
        status['wait_reason'] = 'needs_clarification'
    atomic_json(HOME / 'status.json', status)


def main():
    parser = argparse.ArgumentParser(prog='fleet-scheduler')
    parser.add_argument('action', choices=['run-once', 'serve', 'status'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.action == 'status':
        value = json.loads((HOME / 'status.json').read_text()) if (HOME / 'status.json').exists() else {'scheduler': 'not_started'}
        print(json.dumps(value, indent=2))
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
