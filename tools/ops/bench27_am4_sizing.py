#!/usr/bin/env python3
"""Frozen sizing comparison over an existing AM4 localhost tunnel; never changes services."""
import argparse
from contextlib import closing
import copy
import hashlib
import http.client
import importlib.util
import json
import math
from pathlib import Path
import signal
import sqlite3
import threading
import time

WORKLOAD_SHA = '0f65fd1977ded2217bd1f35fd918447c1c983b93f2e82c9b3f9aabbce2fb750d'
HELPER_SHA = 'd0823b9ca7ec81a9491686c7cd68fe0410d15dbf0bad13cd39b55eb25e4c6b2e'


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def no_leases(database):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=2)) as db:
        count = db.execute('SELECT COUNT(*) FROM capacity_leases WHERE scope=? AND expires_at>?',
                           ('provider:am4-vllm', time.time())).fetchone()[0]
    if count:
        raise RuntimeError('AM4 has active door leases')
    return count


def counters(raw):
    names = {'vllm:num_requests_running': 'running', 'vllm:num_requests_waiting': 'waiting',
             'vllm:request_success_total': 'success'}
    found = {}
    for line in raw.decode().splitlines():
        name = line.split('{', 1)[0].split(' ', 1)[0]
        if name in names:
            value = float(line.rsplit(' ', 1)[1])
            if not math.isfinite(value) or value < 0:
                raise RuntimeError('invalid engine counter')
            key = names[name]; found[key] = found.get(key, 0) + value
    if set(found) != set(names.values()):
        raise RuntimeError('missing engine counters')
    return found


def client_class(helper):
    class Client(helper.Client):
        def __init__(self, args):
            self.database = args.coordination_db
            self.campaign_deadline = time.monotonic() + args.timeout
            self.calls = 0
            self.expected_prompt = None
            self.abort_lock = threading.RLock()
            super().__init__(args.port, args.guard_log, args.trip, args.timeout)

        def abort(self, reason):
            with self.abort_lock:
                if not self.failure:
                    super().abort(reason)

        def watch(self):
            while not self.closed.wait(.25):
                try:
                    self.check()
                except Exception as exc:
                    self.abort(str(exc)); return

        def check(self):
            super().check()
            if time.monotonic() >= self.campaign_deadline:
                raise helper.InfraError('sizing campaign deadline')

        def request(self, path, payload=None):
            self.check()
            if path == '/v1/chat/completions':
                no_leases(self.database)
            self.deadline = self.campaign_deadline
            self.conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            self.conn.connect(); self.sock = self.conn.sock
            self.sock.settimeout(max(.1, self.campaign_deadline - time.monotonic()))
            self.check()
            if path == '/v1/chat/completions':
                self.calls += 1
            self.conn.request('GET' if payload is None else 'POST', path,
                              body=None if payload is None else json.dumps(payload), headers={'Content-Type': 'application/json'})
            response = self.conn.getresponse()
            self.sock = self.conn.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            return response

        def tokens(self, body, directory=None):
            count = super().tokens(body, directory)
            if self.expected_prompt is not None and count != self.expected_prompt:
                raise helper.InfraError(f'frozen prompt token mismatch: {count} != {self.expected_prompt}')
            return count
    return Client


def recipe_manifest(path, expected_sha):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise RuntimeError('recipe manifest hash mismatch')
    manifest = json.loads(raw)
    argv = manifest['argv']
    def option(flag):
        if argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv):
            raise RuntimeError('missing or repeated recipe option: ' + flag)
        return argv[argv.index(flag) + 1]
    if manifest.get('backend') != 'am4-vllm' or not manifest.get('arm'):
        raise RuntimeError('unnamed or wrong recipe backend')
    window = manifest['recipe']['window']
    if type(window) is not int or window < 49152 or option('--max-model-len') != str(window):
        raise RuntimeError('recipe window invalid or inconsistent')
    if option('serve') != '/home/derek/models/qwen3-27b-gptq-int4' or option('--reasoning-parser') != 'qwen3':
        raise RuntimeError('recipe model path or reasoning parser mismatch')
    return manifest, raw


def validate_turn(record, budget, work=False):
    errors = []
    usage = record.get('usage') or {}
    prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(prompt) is not int or prompt != record.get('prompt_tokens_exact'):
        errors.append('usage prompt differs from exact admitted prompt')
    if type(completion) is not int or not 0 <= completion <= budget:
        errors.append('missing or excessive completion usage')
    if work and (not record.get('reasoning', '').strip() or any(t in record.get('content', '') for t in ('<think>', '</think>'))):
        errors.append('recipe mismatch: reasoning not separated from visible content')
    record['comparison_errors'] = errors
    return not errors


def run(args):
    if hashlib.sha256(args.helper.read_bytes()).hexdigest() != HELPER_SHA:
        raise RuntimeError('reviewed helper hash mismatch')
    raw = args.workload.read_bytes()
    if hashlib.sha256(raw).hexdigest() != WORKLOAD_SHA:
        raise RuntimeError('frozen workload hash mismatch')
    if args.trip != Path(str(args.guard_log) + '.tripped'):
        raise RuntimeError('trip path mismatch')
    manifest, recipe_raw = recipe_manifest(args.recipe_manifest, args.recipe_sha256)
    workload = json.loads(raw)
    if workload['work_request']['max_tokens'] != 24000 or workload['final_request_template']['max_tokens'] != 4096:
        raise RuntimeError('output budgets changed')
    spec = importlib.util.spec_from_file_location('reviewed_am4_probe', args.helper)
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'workload.json').write_bytes(raw)
    (args.out / 'recipe-manifest.json').write_bytes(recipe_raw)
    summary = {'caller': 'codex', 'status': 'infrastructure_failure', 'calls': 0,
               'workload_sha256': WORKLOAD_SHA, 'helper_sha256': HELPER_SHA,
               'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'substance_verdict': 'awaiting_frontier_review', 'measurements': {}, 'transport_ok': False,
               'recipe_arm': manifest['arm'], 'recipe_manifest_sha256': args.recipe_sha256}
    client = None; handlers = {}; rc = 3
    try:
        no_leases(args.coordination_db)
        helper.guard_check(args.guard_log, args.trip)
        client = client_class(helper)(args)
        handlers = {s: signal.signal(s, lambda *_: client.abort('operator signal')) for s in (signal.SIGTERM, signal.SIGINT)}
        models = json.loads(client.fetch('/v1/models')); write(args.out / 'models.json', models)
        actual = [m for m in models['data'] if m['id'] == 'qwen3-27b']
        if len(actual) != 1 or type(actual[0].get('max_model_len')) is not int:
            raise helper.InfraError('missing observed AM4 model/window')
        window = actual[0]['max_model_len']; summary['observed_window'] = window
        if window != manifest['recipe']['window']:
            raise helper.InfraError('observed window differs from pinned recipe')
        def idle_snapshot(name):
            no_leases(args.coordination_db)
            raw = client.fetch('/metrics'); (args.out / (name + '.metrics.txt')).write_bytes(raw)
            result = counters(raw); summary[name] = result
            if result['running'] or result['waiting']:
                raise helper.InfraError('AM4 engine not idle')
            return result
        before = idle_snapshot('before')
        client.expected_prompt = 24521
        work = client.stream(copy.deepcopy(workload['work_request']), args.out / 'work', window)
        summary['measurements']['work'] = work
        work_valid = validate_turn(work, 24000, work=True)
        write(args.out / 'work/result.json', work)
        client.expected_prompt = None
        if work_valid and not work.get('error') and work['done'] and work['finish_reason'] == 'stop' and work['content']:
            idle_snapshot('between')
            final = copy.deepcopy(workload['final_request_template'])
            final['messages'] = copy.deepcopy(workload['work_request']['messages']) + [
                {'role': 'assistant', 'content': work['content']}, copy.deepcopy(workload['final_user_message'])]
            rec = client.stream(final, args.out / 'final', window)
            validate_turn(rec, 4096)
            write(args.out / 'final/result.json', rec)
            summary['measurements']['final'] = rec
        after = idle_snapshot('after')
        summary['foreign_requests'] = after['success'] - before['success'] - client.calls
        if summary['foreign_requests'] != 0:
            raise helper.InfraError('engine counter delta does not match owned sends')
        stages = summary['measurements']
        summary['transport_ok'] = len(stages) == 2 and all(r['done'] and r['finish_reason'] == 'stop' and not r.get('error') and not r.get('comparison_errors') for r in stages.values())
        summary['status'] = 'awaiting_review' if summary['transport_ok'] else 'inference_failure'
        rc = 0 if summary['transport_ok'] else 4
    except Exception as exc:
        summary['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if client:
            summary['calls'] = client.calls
            client.abort('lap finished'); client.closed.set(); client.release(); client.watcher.join(timeout=1)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        write(args.out / 'summary.json', summary)
        print(json.dumps({'calls': summary['calls'], 'status': summary['status'], 'out': str(args.out)}))
    return rc


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('helper', 'workload', 'out', 'guard-log', 'trip', 'coordination-db', 'recipe-manifest'):
        p.add_argument('--' + flag, type=Path, required=True)
    p.add_argument('--recipe-sha256', required=True)
    p.add_argument('--port', type=int, required=True, help='Existing localhost tunnel to AM4 engine')
    p.add_argument('--timeout', type=int, default=1800)
    args = p.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.timeout <= 2400:
        p.error('invalid port or timeout (maximum2400s)')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
