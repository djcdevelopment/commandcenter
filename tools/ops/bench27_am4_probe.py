#!/usr/bin/env python3
"""One owned AM4 measurement lap over an existing localhost tunnel. Never installs/restarts/SSHes."""
import argparse
from datetime import datetime
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import signal
import socket
import threading
import time


class InfraError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


def guard_check(log, trip, now=None):
    if trip.exists():
        raise InfraError('sticky guard trip exists')
    try:
        row = json.loads(log.read_bytes().splitlines()[-1])
        age = (now or time.time()) - datetime.fromisoformat(row['utc'].replace('Z', '+00:00')).timestamp()
        gpus = row['gpus']
        if not 0 <= age <= 40 or len(gpus) != 2 or any(not 0 <= g['gpu_core_c'] < 90 for g in gpus):
            raise ValueError('stale or unsafe guard sample')
        if {g['pci'][-10:].lower() for g in gpus} != {'00:09:00.0', '00:0a:00.0'}:
            raise ValueError('wrong guarded GPU pair')
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise InfraError(f'guard unavailable: {exc}') from exc


def grade(content, truth, finish):
    try:
        parsed = json.loads(content)
        exact = parsed == {'needles': truth}
    except (ValueError, TypeError):
        exact = False
    return {'exact_truth': exact, 'finish_stop': finish == 'stop', 'ok': exact and finish == 'stop'}


def event(data, record, elapsed):
    if data == '[DONE]':
        record['done'] = True
        return
    value = json.loads(data)
    if not isinstance(value, dict) or not isinstance(value.get('choices', []), list):
        raise ValueError('invalid SSE completion object')
    record.setdefault('event_times_s', []).append(elapsed)
    if value.get('error'):
        raise InferenceError(str(value['error']))
    if value.get('usage'):
        record['usage'] = value['usage']
    for choice in value.get('choices', []):
        if not isinstance(choice, dict) or not isinstance(choice.get('delta', {}), dict):
            raise ValueError('invalid SSE choice/delta')
        if choice.get('finish_reason'):
            record['finish_reason'] = choice['finish_reason']
        delta = choice.get('delta', {})
        for field, output in [('content', 'content'), ('reasoning_content', 'reasoning'), ('reasoning', 'reasoning')]:
            if delta.get(field):
                if not isinstance(delta[field], str):
                    raise ValueError('non-string SSE content')
                record[output] += delta[field]
                record.setdefault('ttft_s', elapsed)
                record.setdefault(f'first_{output}_s', elapsed)


class Client:
    def __init__(self, port, log, trip, timeout):
        self.port, self.log, self.trip, self.timeout = port, log, trip, timeout
        self.conn = self.sock = None
        self.deadline = None
        self.failure = None
        self.closed = threading.Event()
        self.watcher = threading.Thread(target=self.watch, daemon=True)
        self.watcher.start()

    def abort(self, reason):
        self.failure = reason
        conn = self.conn
        for sock in (self.sock, conn.sock if conn else None):
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def watch(self):
        while not self.closed.wait(0.25):
            try:
                guard_check(self.log, self.trip)
                if self.deadline and time.monotonic() > self.deadline:
                    raise InfraError('request deadline exceeded')
            except InfraError as exc:
                self.abort(str(exc))
                return

    def check(self):
        if self.failure:
            raise InfraError(self.failure)
        guard_check(self.log, self.trip)

    def request(self, path, payload=None):
        self.check()
        self.deadline = time.monotonic() + self.timeout
        self.conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=40)
        self.conn.request('GET' if payload is None else 'POST', path,
                          body=None if payload is None else json.dumps(payload), headers={'Content-Type': 'application/json'})
        response = self.conn.getresponse()
        self.sock = self.conn.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
        return response

    def release(self):
        if self.conn:
            self.conn.close()
        self.conn = self.sock = None
        self.deadline = None

    def fetch(self, path, payload=None):
        try:
            response = self.request(path, payload)
            data = response.read()
            self.check()
            if response.status != 200:
                raise InfraError(f'{path}: HTTP {response.status}: {data[:500]!r}')
            return data
        finally:
            self.release()

    def tokens(self, body, directory=None):
        request = {k: body[k] for k in ('model', 'messages', 'chat_template_kwargs')}
        request['add_generation_prompt'] = True
        raw = self.fetch('/tokenize', request)
        if directory:
            (directory / 'tokenize-request.json').write_text(json.dumps(request, indent=2))
            (directory / 'tokenize-response.json').write_bytes(raw)
        count = json.loads(raw)['count']
        if type(count) is not int or count < 1:
            raise InfraError('invalid tokenizer count')
        return count

    def stream(self, body, directory, window):
        directory.mkdir()
        record = {'content': '', 'reasoning': '', 'done': False, 'finish_reason': None}
        start = time.monotonic()
        try:
            self.check()
            body = {**body, 'model': 'qwen3-27b', 'stream': True, 'stream_options': {'include_usage': True}}
            (directory / 'request.json').write_text(json.dumps(body, indent=2))
            count = self.tokens(body, directory)
            record['prompt_tokens_exact'] = count
            if count + body['max_tokens'] > window:
                raise InferenceError('exact prompt plus output exceeds observed window')
            (directory / 'metrics-before.txt').write_bytes(self.fetch('/metrics'))
            start = time.monotonic()
            response = self.request('/v1/chat/completions', body)
            with (directory / 'response.sse').open('wb') as raw:
                pending = []
                for line in response:
                    raw.write(line); raw.flush()
                    self.check()
                    if line.strip() == b'':
                        if pending:
                            event('\n'.join(pending), record, time.monotonic() - start)
                            pending = []
                        if record['done']:
                            break
                    elif line.startswith(b'data:'):
                        pending.append(line[5:].decode().strip())
            if response.status != 200 or not record['done'] or record['finish_reason'] != 'stop':
                raise InferenceError(f'HTTP {response.status}; done={record["done"]}; finish={record["finish_reason"]}')
        except (InferenceError, ValueError) as exc:
            record['error'] = str(exc)
        except Exception as exc:
            record['infrastructure_error'] = self.failure or str(exc)
            raise
        finally:
            record['elapsed_s'] = time.monotonic() - start
            self.release()
            (directory / 'result.json').write_text(json.dumps(record, indent=2))
        (directory / 'metrics-after.txt').write_bytes(self.fetch('/metrics'))
        return record


def near_needle(client, probe, fraction, window):
    target = int(window * fraction)
    low, high, best = 1024, window * 8, None
    for _ in range(22):
        size = (low + high) // 2
        system, text, truth = probe.build_text(size)
        body = probe.body('qwen3-27b', system, text + probe.mtw.NEEDLE_FINAL, 600, probe.schema_for(sorted(truth)), False)
        count = client.tokens(body)
        if best is None or abs(count - target) < abs(best[2] - target):
            best = body, truth, count
        if abs(count - target) <= max(1, target * .01):
            break
        if count < target:
            low = size + 1
        else:
            high = size - 1
        if low > high:
            break
    if best is None or abs(best[2] - target) > max(1, target * .01):
        raise InfraError(f'could not construct exact-tokenized needle near {fraction}')
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'workload', 'out', 'guard_log', 'trip', 'lab_source', 'flash_source'):
        ap.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    ap.add_argument('--port', type=int, default=18094)
    ap.add_argument('--timeout', type=int, default=600)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'infrastructure_failure', 'measurements': {}, 'caller': 'codex', 'direct_model': 'qwen3-27b'}
    client = None
    try:
        manifest = json.loads(a.manifest.read_text()); raw = a.workload.read_bytes()
        if manifest.get('backend') != 'am4-vllm' or a.trip != Path(str(a.guard_log) + '.tripped'):
            raise InfraError('wrong backend or trip path does not belong to guard log')
        if hashlib.sha256(raw).hexdigest() != manifest['workload_sha256']:
            raise InfraError('prepared workload hash mismatch')
        workload = json.loads(raw)
        (a.out / 'manifest.json').write_bytes(a.manifest.read_bytes())
        (a.out / 'workload.json').write_bytes(raw)
        spec = importlib.util.spec_from_file_location('am4_seat_probe', a.lab_source / 'research/seat_probe.py')
        probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
        probe.mtw.FLASH = str(a.flash_source)
        summary['helper_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        summary['seat_probe_sha256'] = hashlib.sha256((a.lab_source / 'research/seat_probe.py').read_bytes()).hexdigest()
        guard_check(a.guard_log, a.trip)
        client = Client(a.port, a.guard_log, a.trip, a.timeout)
        signal.signal(signal.SIGTERM, lambda *_: client.abort('SIGTERM'))
        signal.signal(signal.SIGINT, lambda *_: client.abort('SIGINT'))
        models = json.loads(client.fetch('/v1/models')); (a.out / 'models.json').write_text(json.dumps(models))
        model = [m for m in models['data'] if m['id'] == 'qwen3-27b']
        if len(model) != 1 or model[0]['max_model_len'] != manifest['recipe']['window']:
            raise InfraError('served model/window differs from prepared arm')
        window = model[0]['max_model_len']; summary['window'] = window
        for fraction in (.25, .5, .9):
            body, truth, count = near_needle(client, probe, fraction, window)
            name = f'needle-{int(fraction * 100)}'
            rec = client.stream(body, a.out / name, window)
            rec['grade'] = grade(rec['content'], truth, rec['finish_reason'])
            rec.update(target_fraction=fraction, prompt_tokens_exact=count, truth=truth)
            summary['measurements'][name] = rec
        work = client.stream(workload['work_request'], a.out / 'work', window)
        summary['measurements']['work'] = work
        if not work.get('error') and work['content']:
            final = {**workload['final_request_template'], 'messages': workload['work_request']['messages'] +
                     [{'role': 'assistant', 'content': work['content']}, workload['final_user_message']]}
            rec = client.stream(final, a.out / 'final', window)
            rec['grade'] = grade(rec['content'], {k: v['value'] for k, v in workload['needle_truth'].items()}, rec['finish_reason'])
            summary['measurements']['final'] = rec
        summary['ok'] = len(summary['measurements']) == 5 and all(not r.get('error') and r.get('grade', {'ok': bool(r['content'])})['ok'] for r in summary['measurements'].values())
        summary['status'] = 'complete' if summary['ok'] else 'inference_failure'
        return 0 if summary['ok'] else 4
    except Exception as exc:
        summary['error'] = str(exc)
        return 3
    finally:
        if client:
            client.abort('lap finished'); client.closed.set(); client.watcher.join(timeout=1)
        (a.out / 'summary.json').write_text(json.dumps(summary, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
