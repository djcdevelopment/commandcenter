#!/usr/bin/env python3
"""Guarded paired B70 retrieval probes inside an already acquired experiment fence.

Never stages recipes or changes services. Closes only this process's HTTP requests.
Uses pinned reviewed AM4 transport/parser primitives with B70-specific guard/auth.
"""
import argparse
import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
import sqlite3
from pathlib import Path
import signal
import sys
import threading
import time


class Refused(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def guard_check(log, trip, now=None):
    if trip != Path(str(log) + '.tripped') or trip.exists():
        raise Refused('guard trip or mismatched guard path')
    try:
        with log.open('rb') as fh:
            fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - 65536)); raw = fh.read()
        if not raw.endswith(b'\n'):
            raw = raw[:raw.rfind(b'\n') + 1]
        row = json.loads(raw.splitlines()[-1])
        age = (now or datetime.now(timezone.utc)).timestamp() - datetime.fromisoformat(row['utc'].replace('Z', '+00:00')).timestamp()
        if not 0 <= age <= 40:
            raise ValueError('stale heartbeat')
        for card in ('card2', 'card3'):
            temperature = row[card]['vram_c']
            if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 150:
                raise ValueError('invalid or missing VRAM temperature')
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise Refused('B70 guard unavailable: ' + type(exc).__name__) from exc


def require_fence(experiment_id, database):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=2)) as connection:
        row = connection.execute("SELECT owner,session_id FROM gpu_tenancy WHERE resource=?", ('omen-b70-pool',)).fetchone()
    if row != ('experiment', experiment_id):
        raise Refused('direct probes require their exact experiment pool fence')


class Ownership:
    def __init__(self, log, trip, timeout):
        self.log, self.trip = log, trip
        self.deadline = time.monotonic() + timeout
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.clients = []
        self.reason = None

    def abort(self, reason):
        self.reason = reason
        self.stopped.set()
        with self.lock:
            for client in self.clients:
                client.abort(reason)

    def check(self):
        if self.stopped.is_set():
            raise Refused(self.reason or 'stopped')
        if time.monotonic() >= self.deadline:
            self.abort('campaign deadline')
            raise Refused('campaign deadline')
        guard_check(self.log, self.trip)

    def close(self):
        self.abort('owned probe finished')
        for client in self.clients:
            client.closed.set()
            client.release()
            client.watcher.join(timeout=1)


def client_class(helper):
    class B70Client(helper.Client):
        def __init__(self, seat, model, token, ownership, request_timeout):
            self.seat, self.model, self.token, self.ownership = seat, model, token, ownership
            self.calls = 0
            super().__init__(18091 + seat, ownership.log, ownership.trip, request_timeout)
            with ownership.lock:
                ownership.clients.append(self)
            if ownership.stopped.is_set():
                self.abort(ownership.reason)

        def watch(self):
            while not self.closed.wait(0.25):
                try:
                    self.check()
                    if self.deadline and time.monotonic() > self.deadline:
                        raise Refused('request deadline')
                except Exception as exc:
                    self.ownership.abort(type(exc).__name__ + ': ' + str(exc))
                    return

        def check(self):
            if self.failure:
                raise Refused(self.failure)
            self.ownership.check()

        def request(self, path, payload=None):
            self.check()
            self.deadline = min(time.monotonic() + self.timeout, self.ownership.deadline)
            self.conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            self.conn.connect()
            self.sock = self.conn.sock
            self.sock.settimeout(self.timeout)
            self.check()  # catches a trip while connecting before any request bytes
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token}
            if path == '/v1/chat/completions':
                if hasattr(self.ownership, 'check_fence'):
                    self.ownership.check_fence()
                self.check()
                self.calls += 1  # attempted completion sends; transport failures make attribution unknown
            self.conn.request('GET' if payload is None else 'POST', path,
                              body=None if payload is None else json.dumps(payload), headers=headers)
            response = self.conn.getresponse()
            self.sock = self.conn.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            return response

        def sample(self, path):
            try:
                Path(path).write_bytes(self.fetch('/metrics'))
            except Exception as exc:
                write(Path(str(path) + '.gap.json'), {'gap': type(exc).__name__})
                self.check()  # thermal failures remain hard even when metrics gaps are tolerated

        def stream(self, body, directory, window):
            directory.mkdir(parents=True, exist_ok=False)
            body = {**body, 'model': self.model, 'stream': True, 'stream_options': {'include_usage': True}}
            record = {'content': '', 'reasoning': '', 'done': False, 'finish_reason': None, 'seat': self.seat}
            start = time.monotonic()
            try:
                self.check()
                write(directory / 'request.json', body)
                count = self.tokens(body, directory)
                record['prompt_tokens_exact'] = count
                if type(body['max_tokens']) is not int or body['max_tokens'] < 1 or count + body['max_tokens'] > window:
                    record['admission'] = 'refused_context'
                    return record
                record['admission'] = 'accepted'
                self.sample(directory / 'metrics-before.txt')
                start = time.monotonic()
                response = self.request('/v1/chat/completions', body)
                with (directory / 'response.sse').open('xb') as raw:
                    pending = []
                    for line in response:
                        raw.write(line); raw.flush(); self.check()
                        if line.strip() == b'':
                            if pending:
                                helper.event('\n'.join(pending), record, time.monotonic() - start); pending = []
                            if record['done']:
                                break
                        elif line.startswith(b'data:'):
                            pending.append(line[5:].decode().strip())
                self.check()
                record['actual_prompt_tokens'] = (record.get('usage') or {}).get('prompt_tokens')
                record['tokenizer_usage_mismatch'] = None if record['actual_prompt_tokens'] is None else record['actual_prompt_tokens'] != count
                if response.status != 200 or not record['done'] or record['finish_reason'] != 'stop':
                    record['inference_error'] = f'HTTP {response.status}; done={record["done"]}; finish={record["finish_reason"]}'
            except helper.InferenceError as exc:
                record['inference_error'] = str(exc)
            except Exception as exc:
                record['infrastructure_error'] = type(exc).__name__
                self.ownership.abort(type(exc).__name__)
                raise
            finally:
                self.release()
                record['elapsed_s'] = time.monotonic() - start
                write(directory / 'result.json', record)
            self.sample(directory / 'metrics-after.txt')
            return record
    return B70Client


def calibrate(client, probe, target, model, max_tokens, tolerance):
    low, high, best = 1024, target * 8, None
    for _ in range(25):
        client.check()
        size = (low + high) // 2
        system, text, truth = probe.build_text(size, needles=12, seed=7)
        body = probe.body(model, system, text + probe.mtw.NEEDLE_FINAL, max_tokens, probe.schema_for(sorted(truth)), False)
        count = client.tokens(body)
        if best is None or abs(count - target) < abs(best[2] - target):
            best = body, truth, count
        if abs(count - target) <= tolerance:
            return best
        if count < target:
            low = size + 1
        else:
            high = size - 1
        if low > high:
            break
    raise Refused(f'could not calibrate target {target} within {tolerance} tokens; closest={None if best is None else best[2]}')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def verified_manifest(path):
    manifest = json.loads(path.read_text())
    if not isinstance(manifest.get('experiment_id'), str) or not manifest['experiment_id']:
        raise Refused('manifest must name its owning experiment')
    if manifest['model'] != 'qwen3.8-27b' or sorted(manifest['windows'].values()) != [65536, 131072] or set(manifest['windows']) != {'0', '1'}:
        raise Refused('requires one explicit 65K control and one 131K treatment')
    if set(manifest['sources']) != {'driver', 'am4_helper', 'seat_probe', 'workload_builder'}:
        raise Refused('manifest must pin driver and all three imported sources')
    if Path(manifest['sources']['workload_builder']['path']).resolve() != Path(manifest['sources']['seat_probe']['path']).resolve().with_name('make_thinking_workload.py'):
        raise Refused('workload builder pin does not match seat_probe import')
    for entry in manifest['sources'].values():
        if sha(entry['path']) != entry['sha256']:
            raise Refused('pinned source hash mismatch')
    if Path(manifest['sources']['driver']['path']).resolve() != Path(__file__).resolve():
        raise Refused('manifest does not pin this driver')
    if manifest['source_commit'] != 'de666ef75ce79733bffb34c5e146c9b8ddd42f30':
        raise Refused('unexpected frozen source commit')
    return manifest


def run(args):
    manifest = verified_manifest(args.manifest)
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'infrastructure_failure', 'calls': {'0': 0, '1': 0}, 'measurements': {}, 'model': manifest['model'],
               'windows': manifest['windows'], 'source_commit': manifest['source_commit'], 'manifest_sha256': sha(args.manifest)}
    owner = Ownership(args.guard_log, args.trip, args.max_seconds)
    database = Path(os.environ.get('HEARTH_COORDINATION_DB', str(Path.home() / 'hearth-production/var/execution/coordination.sqlite')))
    owner.check_fence = lambda: require_fence(manifest['experiment_id'], database)
    old_signals = {sig: signal.signal(sig, lambda *_: owner.abort('operator signal')) for sig in (signal.SIGTERM, signal.SIGINT)}
    rc = 3
    try:
        write(args.out / 'manifest.json', manifest)
        helper = load_module('bench27_direct_am4_base', manifest['sources']['am4_helper']['path'])
        probe = load_module('bench27_direct_seat_probe', manifest['sources']['seat_probe']['path'])
        probe.mtw.FLASH = manifest['flash_source']; probe.mtw.COMMIT = manifest['source_commit']
        owner.check()
        owner.check_fence()
        Client = client_class(helper)
        token = probe.api_key()  # existing internal key loader; token is never serialized
        clients = {str(s): [Client(s, manifest['model'], token, owner, args.request_timeout) for _ in range(2)] for s in (0, 1)}
        for seat in clients:
            models = json.loads(clients[seat][0].fetch('/v1/models'))
            write(args.out / f'seat-{seat}-models.json', models)
            actual = [m for m in models['data'] if m['id'] == manifest['model']]
            if len(actual) != 1 or actual[0].get('max_model_len') != manifest['windows'][seat]:
                raise Refused('actual model/window differs from manifest')
        treatment = next(s for s, w in manifest['windows'].items() if w == 131072)
        def perform(seat, index, name, body, truth, two_turns):
            client = clients[seat][index]; folder = args.out / name / f'seat-{seat}' / f'conversation-{index}'
            first = client.stream(body, folder / 'work', manifest['windows'][seat])
            first['grade'] = helper.grade(first['content'], truth, first['finish_reason'])
            records = {'work': first, 'final': None}
            if two_turns and first['admission'] == 'accepted' and not first.get('inference_error'):
                final = copy.deepcopy(body)
                final['messages'] += [{'role': 'assistant', 'content': first['content']},
                                      {'role': 'user', 'content': 'Verify all twelve values against the original source and return the same required JSON object, with no extra text.'}]
                second = client.stream(final, folder / 'final', manifest['windows'][seat])
                second['grade'] = helper.grade(second['content'], truth, second['finish_reason']); records['final'] = second
            write(folder / 'conversation.json', records)
            return records
        for target in (38800, 90000, 120000):
            owner.check()
            body, truth, count = calibrate(clients[treatment][0], probe, target, manifest['model'], 600, 64)
            write(args.out / f'workload-{target}.json', {'request': body, 'truth': truth, 'target_tokens': target, 'calibrated_tokens': count,
                  'request_sha256': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), 'tolerance_tokens': 64})
            for n in (1, 2):
                name = f'target-{target}-concurrency-{n}'
                records = {}; start = time.monotonic()
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {executor.submit(perform, seat, i, name, body, truth, n == 2): (seat, i) for seat in clients for i in range(n)}
                    for future in as_completed(futures):
                        seat, i = futures[future]
                        try:
                            records[f'{seat}:{i}'] = future.result()
                        except BaseException:
                            owner.abort('paired infrastructure failure'); raise
                summary['measurements'][name] = {'wall_s': time.monotonic() - start, 'conversations': records}
                write(args.out / 'progress.json', summary)
        failures = []
        for name, group in summary['measurements'].items():
            for key, conversation in group['conversations'].items():
                seat = key.split(':')[0]
                for stage, record in conversation.items():
                    if record is None:
                        continue
                    expected_refusal = manifest['windows'][seat] == 65536 and not name.startswith('target-38800-')
                    good = record['admission'] == 'refused_context' if expected_refusal else record.get('grade', {}).get('ok', False) and record.get('done', False) and not record.get('inference_error') and record.get('tokenizer_usage_mismatch') is not True
                    if not good:
                        failures.append(f'{name}/{key}/{stage}')
        summary.update(status='complete', model_failures=failures, ok=not failures,
                       limitation='Long contexts intentionally inadmissible on65K control. Two-turn retrieval conversations, thinkingoff; not carry or general reasoning qualification.')
        rc = 4 if failures else 0
    except BaseException as exc:
        summary['error'] = type(exc).__name__ + ': ' + str(exc)
        owner.abort(type(exc).__name__)
    finally:
        summary['calls'] = {str(s): sum(c.calls for c in owner.clients if c.seat == s) for s in (0, 1)}
        summary['attribution'] = 'attempted_completion_sends; harness independently checks per-seat success counters'
        owner.close()
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
        write(args.out / 'summary.json', summary)
        print(json.dumps({'calls': summary['calls'], 'ok': summary.get('ok', False), 'status': summary['status'], 'out': str(args.out)}))
    return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('manifest', 'out', 'guard-log', 'trip'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=2400)
    parser.add_argument('--request-timeout', type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 3600 or not 1 <= args.request_timeout <= 900:
        parser.error('campaign bound must be1..3600s and request bound1..900s')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
