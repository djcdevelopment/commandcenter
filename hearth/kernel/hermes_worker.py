"""Native admission for the resident-only Hermes worker lane; no lifecycle writes."""
from datetime import datetime, timezone
import json
import os
import time
import urllib.request

BACKEND = 'omen-arc'
MODEL = 'qwen3-30b-a3b'
BASE = 'http://127.0.0.1:8082'


def native(path, payload=None):
    key = os.environ.get('OMEN_ARC_TOKEN', '')
    if not key:
        raise RuntimeError('OMEN worker credential unavailable')
    req = urllib.request.Request(BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('oversized native response')
        return json.loads(data)


def query_omen_worker() -> dict:
    """Fresh resident identity, per-slot context and occupancy; never loads a model."""
    result = dict(backend=BACKEND, model=MODEL, physical_resource='omen:127.0.0.1:8082',
                  observed_at=datetime.now(timezone.utc).isoformat(), ready=False)
    try:
        from hearth.toolsurface.rotation import rotation_status
        rotation = rotation_status()
        tenancy = rotation.get('tenancy') or {}
        if tenancy.get('readable') is not True:
            raise RuntimeError('OMEN tenancy observation unavailable')
        if rotation.get('open_windows') or tenancy.get('image_session'):
            raise RuntimeError('OMEN has an active maintenance or GPU tenancy window')
        native('/health')
        models = native('/v1/models')
        if MODEL not in [row.get('id') for row in models.get('data', [])]:
            raise RuntimeError('resident model identity changed')
        props, slots = native('/props'), native('/slots')
        ctx = props.get('default_generation_settings', {}).get('n_ctx')
        count = props.get('total_slots')
        if type(ctx) is not int or ctx < 4096 or type(count) is not int or count < 1:
            raise RuntimeError('native slot capacity unknown')
        if not isinstance(slots, list) or len(slots) != count:
            raise RuntimeError('native slot observations inconsistent')
        if any(not isinstance(row,dict) or not (
                type(row.get('is_processing')) is bool or
                ('is_processing' not in row and type(row.get('state')) is int)) for row in slots):
            raise RuntimeError('native slot occupancy unknown')
        from hearth.toolsurface.occupancy import _slot_is_processing
        busy = sum(_slot_is_processing(row) for row in slots)
        result.update(ready=True, context_length=ctx, parallel_slots=count,
                      busy_slots=busy, free_slots=count-busy, reason='native resident verified')
    except Exception as exc:
        result['reason'] = str(exc)[:200]
    return result


def admit(args):
    """Count the exact two-message template before canonical dispatch."""
    if args.get('backend') not in (None, BACKEND) or args.get('model') not in (None, MODEL):
        raise PermissionError('worker route is OMEN resident only')
    from hearth.toolsurface.inference import DEFAULT_ENDPOINT
    if args.get('endpoint') not in (None, DEFAULT_ENDPOINT):
        raise PermissionError('worker endpoint overrides are forbidden')
    if any(args.get(k) is not None for k in ('files', 'quality', 'task', 'task_family')):
        raise PermissionError('worker routing and file overrides are forbidden')
    budget, timeout = args.get('max_tokens', 2048), args.get('timeout_s', 120)
    if type(budget) is not int or not 1 <= budget <= 4096:
        raise PermissionError('worker output budget must be 1..4096')
    if type(timeout) is not int or not 1 <= timeout <= 180:
        raise PermissionError('worker deadline must be 1..180 seconds')
    if not isinstance(args.get('task_id'), str) or not args['task_id'].startswith(('hermes-', 'hearth-hermes-')):
        raise PermissionError('worker generation needs a Hermes run id')
    if not isinstance(args.get('prompt'), str) or not args['prompt'].strip():
        raise PermissionError('worker prompt required')
    started = time.monotonic()
    while True:
        state = query_omen_worker()
        if not state['ready']:
            raise PermissionError(state['reason'])
        if state['free_slots']:
            break
        if time.monotonic() - started >= min(10, timeout / 4):
            raise PermissionError('OMEN busy; retry within the job deadline, no fallback')
        time.sleep(.25)
    messages = []
    if args.get('system'):
        messages.append({'role':'system', 'content':args['system']})
    messages.append({'role':'user', 'content':args['prompt']})
    try:
        rendered = native('/apply-template', {'model':MODEL, 'messages':messages,
                          'max_tokens':budget, 'stream':False})['prompt']
        tokens = native('/tokenize', {'content':rendered, 'add_special':True, 'parse_special':True})['tokens']
        if not isinstance(rendered, str) or not isinstance(tokens, list):
            raise ValueError('unusable native token count')
        if len(tokens) + budget + 32 > state['context_length']:
            raise ValueError('worker context exceeded; split the task, never truncate')
    except Exception as exc:
        raise PermissionError(str(exc)) from exc
    remaining = timeout - int(time.monotonic() - started + 1)
    if remaining < 1:
        raise PermissionError('worker admission deadline exhausted')
    args.update(backend=BACKEND, model=MODEL, max_tokens=budget, timeout_s=remaining)
