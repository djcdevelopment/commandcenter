"""One operator-authorized AM4 qualification; not a JEV-admitted route.

Reuses the installed worker loop and its qualified JSON decoder. No shared
runner configuration, resolver, conductor, or gateway modification occurs.
Run only in a new private lap directory containing task.json.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request


ROOT = Path('/home/claude/fleet-worker-node')
EXPECTED = {
    'scripts/agent_openai.py': '9f6d134922e4d63c199eb717ed43474e63d13b37321a2a72c30bde6b5e50d575',
    'scripts/agent_hearth.py': '9c3dec010663bd52b8f0b3352424a258d0982bfeb6422664c61d7aa3012a66d1',
    'scripts/runner_presets.py': 'e48b640e0f13fa0bb4c0e629f054b3cbb3c8c0d5adb2c165f658e09cc0fff49a',
    'runner.json': '7718d852af0181b02aaee72bcd433a53b82f17588f13505eb4b489ecbe16e79d',
    'runner-presets/am4-shared-27b.json': 'c5fffbd01f6117891279cc0b424e9f9becca86ee4696036a9ed13e1d31c8486c',
}


def main():
    os.umask(0o077)
    lap = Path(__file__).resolve().parent
    if (lap / 'started.json').exists():
        raise RuntimeError('one_attempt_only')
    for name, expected in EXPECTED.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('worker_changed_' + name)
    cfg = json.loads((ROOT / 'runner-presets/am4-shared-27b.json').read_text())
    if (cfg['runner'], cfg['base_url'], cfg['model'], cfg['context_length']) != (
            'openai', 'http://192.168.12.233:8090/v1', 'am4-dense-27b', 131072):
        raise RuntimeError('qualification_route_mismatch')
    # Credentials remain on the worker and never enter public evidence.
    token = Path(cfg['token_file']).read_text().strip()
    if not token:
        raise RuntimeError('missing_builder_credential')
    task = json.loads((lap / 'task.json').read_text())
    work = lap / 'work'
    work.mkdir(exist_ok=False)
    plan = lap / 'plan.md'
    plan.write_text(task['brief'])
    (lap / 'started.json').write_text(json.dumps({
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'task_brief_sha256': hashlib.sha256(task['brief'].encode()).hexdigest(),
        'route': {k: cfg[k] for k in ('runner', 'base_url', 'model', 'context_length')},
        'source_hashes': EXPECTED, 'budget_s': 180, 'max_steps': 12,
        'max_tokens': 4096, 'reasoning_effort': 'none',
        'operator_driven': True, 'promotion_policy': 'manual',
    }, indent=2))
    sys.path.insert(0, str(ROOT / 'scripts'))
    import agent_openai as runner
    from agent_hearth import extract_action
    runner.extract_action = extract_action
    calls = []
    actions = []
    original_action = runner.do_action
    start = time.monotonic()

    def save():
        (lap / 'trace.json').write_text(json.dumps({
            'elapsed_s': round(time.monotonic() - start, 3),
            'calls': calls, 'actions': actions,
        }, indent=2))

    def chat(base_url, model, supplied_token, messages, max_tokens=4096, timeout=120):
        if (base_url, model) != (cfg['base_url'], cfg['model']):
            raise RuntimeError('route_changed')
        sent = json.dumps({'model': model, 'messages': messages, 'temperature': .2,
                           'max_tokens': 4096, 'reasoning_effort': 'none'}).encode()
        req = urllib.request.Request(base_url + '/chat/completions', data=sent,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        began = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
        choice = data['choices'][0]
        content = choice['message'].get('content') or ''
        calls.append({'elapsed_s': round(time.monotonic() - began, 3),
                      'usage': data.get('usage'), 'finish_reason': choice.get('finish_reason'),
                      'response_model': data.get('model'),
                      'request_sha256': hashlib.sha256(sent).hexdigest(),
                      'response_content_sha256': hashlib.sha256(content.encode()).hexdigest()})
        save()
        return content

    def do_action(action, workdir):
        # Record observable actions/results, never a private thought field.
        public = {k: v for k, v in action.items() if k != 'thought'}
        observation, finished = original_action(action, workdir)
        actions.append({'at_s': round(time.monotonic() - start, 3),
                        'action': public, 'observation': observation, 'finished': finished})
        save()
        return observation, finished

    runner.chat = chat
    runner.do_action = do_action
    sys.argv = [str(ROOT / 'scripts/agent_openai.py'), '--plan-file', str(plan),
                '--workdir', str(work), '--base-url', cfg['base_url'], '--model', cfg['model'],
                '--token-file', cfg['token_file'], '--max-steps', '12',
                '--task-id', 'jev-am4-cycle21-20260921', '--budget-s', '180']
    try:
        code = runner.main()
    finally:
        save()
    (lap / 'result.json').write_text(json.dumps({'exit_code': code,
        'elapsed_s': round(time.monotonic() - start, 3),
        'files': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                         'bytes': p.stat().st_size} for p in work.iterdir() if p.is_file()},
        'finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
