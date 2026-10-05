import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('direct', Path(__file__).with_name('bench27_b70_direct.py'))
direct = importlib.util.module_from_spec(spec); spec.loader.exec_module(direct)
HELPER = Path('/home/derek/work/commandcenter-linux-flash/tools/ops/bench27_am4_probe.py')
helper = direct.load_module('reviewed_am4_helper_test', HELPER)


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'cards.jsonl'; self.trip = Path(str(self.log) + '.tripped')
        self.heartbeat()
        self.owner = direct.Ownership(self.log, self.trip, 30)
        self.addCleanup(self.owner.close)
        self.started = threading.Event(); self.unblock = threading.Event()
        self.addCleanup(self.unblock.set)
        self.posts, self.headers = [], []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'vllm:request_success_total 0\n')
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.headers.append(self.headers.get('Authorization'))
                if self.path == '/tokenize':
                    body = json.dumps({'count': outer.count}).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                outer.posts.append(request)
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers(); self.wfile.flush()
                outer.started.set()
                if outer.block:
                    outer.unblock.wait(5)
                content = {'choices': [{'delta': {'content': '{"needles":{"01":"12345678"}}'}, 'finish_reason': outer.finish}]}
                try:
                    self.wfile.write(('data: ' + json.dumps(content) + '\n\ndata: [DONE]\n\n').encode()); self.wfile.flush()
                except (OSError, BrokenPipeError):
                    pass
        self.count, self.block, self.finish = 38800, False, 'stop'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def heartbeat(self, age=0, tail=b''):
        row = {'utc': (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
               'card2': {'vram_c': 99}, 'card3': {'vram_c': 95}}
        self.log.write_bytes(json.dumps(row).encode() + b'\n' + tail)

    def client(self, seat=0):
        client = direct.client_class(helper)(seat, 'qwen3.8-27b', 'synthetic-test-key', self.owner, 10)
        client.port = self.server.server_port
        return client

    def body(self):
        return {'model': 'qwen3.8-27b', 'messages': [{'role': 'user', 'content': 'source'}], 'max_tokens': 600,
                'chat_template_kwargs': {'enable_thinking': False}}

    def test_exact_admission_refuses_without_completion(self):
        self.count = 90000
        client = self.client()
        result = client.stream(self.body(), self.root / 'refusal', 65536)
        self.assertEqual(result['admission'], 'refused_context')
        self.assertEqual(client.calls, 0); self.assertEqual(self.posts, [])
        self.assertEqual(json.loads((self.root / 'refusal/tokenize-response.json').read_text())['count'], 90000)

    def test_real_http_auth_sse_and_truncation(self):
        client = self.client()
        self.finish = 'length'
        result = client.stream(self.body(), self.root / 'truncated', 65536)
        self.assertEqual(client.calls, 1)
        self.assertIn('inference_error', result)
        self.assertFalse(helper.grade(result['content'], {'01': '12345678'}, result['finish_reason'])['ok'])
        self.assertTrue(all(h == 'Bearer synthetic-test-key' for h in self.headers))
        self.assertNotIn('synthetic-test-key', (self.root / 'truncated/request.json').read_text())
        with self.assertRaises(FileExistsError):
            client.stream(self.body(), self.root / 'truncated', 65536)

    def test_trip_interrupts_blocked_owned_http_stream(self):
        self.block = True; client = self.client()
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(client.stream, self.body(), self.root / 'trip', 65536)
            self.assertTrue(self.started.wait(2)); self.trip.touch()
            with self.assertRaises(Exception):
                future.result(timeout=3)
        self.assertTrue(self.owner.stopped.is_set()); self.assertEqual(client.calls, 1)
        self.assertIn('infrastructure_error', json.loads((self.root / 'trip/result.json').read_text()))

    def test_operator_abort_closes_both_owned_streams(self):
        self.block = True; clients = [self.client(0), self.client(1)]
        with ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(c.stream, self.body(), self.root / f'owned{i}', 65536) for i, c in enumerate(clients)]
            deadline = time.monotonic() + 2
            while len(self.posts) < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(len(self.posts), 2)
            self.owner.abort('operator signal')
            for future in futures:
                with self.assertRaises(Exception):
                    future.result(timeout=3)
        self.assertEqual([c.calls for c in clients], [1, 1])
        self.owner.abort('later failure')
        self.assertEqual(self.owner.reason, 'operator signal')

    def test_guard_freshness_partial_line_and_trip_before_send(self):
        self.heartbeat(tail=b'{"utc":')
        direct.guard_check(self.log, self.trip)
        self.heartbeat(age=41)
        with self.assertRaises(direct.Refused):
            direct.guard_check(self.log, self.trip)
        self.heartbeat(); self.trip.touch(); client = self.client()
        with self.assertRaises(direct.Refused):
            client.stream(self.body(), self.root / 'blocked', 65536)
        self.assertEqual(client.calls, 0)

    def test_request_deadline_interrupts_blocked_stream(self):
        self.block = True; client = self.client(); client.timeout = .3
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(client.stream, self.body(), self.root / 'deadline', 65536)
            with self.assertRaises(Exception):
                future.result(timeout=3)
        self.assertTrue(self.owner.stopped.is_set())


class PhaseTests(unittest.TestCase):
    def test_full_phase_plan_reports_control_refusals_and_separate_counts(self):
        from unittest.mock import patch, Mock
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); log = root / 'guard.jsonl'
            log.write_text(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'card2': {'vram_c': 90}, 'card3': {'vram_c': 90}}) + '\n')
            manifest = {'experiment_id': 'exp', 'model': 'qwen3.8-27b', 'windows': {'0': 65536, '1': 131072}, 'source_commit': 'commit',
                        'sources': {'am4_helper': {'path': 'helper'}, 'seat_probe': {'path': 'probe'}}, 'flash_source': 'flash'}
            path = root / 'manifest.json'; path.write_text(json.dumps(manifest))
            requests = {}
            class FakeClient:
                def __init__(self, seat, model, token, owner, timeout):
                    self.seat, self.calls, self.owner = seat, 0, owner
                    self.closed, self.watcher = threading.Event(), Mock()
                    owner.clients.append(self)
                def fetch(self, _):
                    return json.dumps({'data': [{'id': 'qwen3.8-27b', 'max_model_len': manifest['windows'][str(self.seat)]}]}).encode()
                def stream(self, body, folder, window):
                    folder.mkdir(parents=True)
                    requests[str(folder.relative_to(root/'out'))] = body
                    admitted = body['target'] + 600 <= window
                    self.calls += int(admitted)
                    return {'done': admitted, 'admission': 'accepted' if admitted else 'refused_context', 'content': '{"needles":{"01":"12345678"}}' if admitted else '', 'finish_reason': 'stop' if admitted else None}
                def abort(self, _): pass
                def release(self): pass
            fake_probe = SimpleNamespace(mtw=SimpleNamespace(), api_key=lambda: 'test')
            def calibration(*args):
                target = args[2]
                return {'target': target, 'messages': [{'role': 'user', 'content': 'source'}]}, {'01': '12345678'}, target
            args = SimpleNamespace(manifest=path, out=root/'out', guard_log=log, trip=Path(str(log)+'.tripped'), max_seconds=30, request_timeout=10)
            with patch.object(direct, 'verified_manifest', return_value=manifest), patch.object(direct, 'require_fence'), \
                    patch.object(direct, 'load_module', side_effect=[helper, fake_probe]), patch.object(direct, 'client_class', return_value=FakeClient), \
                    patch.object(direct, 'calibrate', side_effect=calibration):
                self.assertEqual(direct.run(args), 0)
            summary = json.loads((args.out/'summary.json').read_text())
            self.assertEqual(summary['calls'], {'0': 5, '1': 15})
            self.assertEqual(len(summary['measurements']), 6)
            for target in (38800, 90000, 120000):
                phase = f'target-{target}-concurrency-2'
                first = requests[f'{phase}/seat-1/conversation-0/work']
                second = requests[f'{phase}/seat-1/conversation-1/work']
                self.assertNotEqual(first['messages'][0]['content'], second['messages'][0]['content'])
                self.assertTrue(first['messages'][0]['content'].startswith('[probe run='))
                self.assertEqual(first, requests[f'{phase}/seat-0/conversation-0/work'])
                self.assertEqual(second, requests[f'{phase}/seat-0/conversation-1/work'])
                self.assertEqual(first['messages'][0], requests[f'{phase}/seat-1/conversation-0/final']['messages'][0])
            self.assertEqual(summary['measurements']['target-120000-concurrency-2']['conversations']['0:0']['work']['admission'], 'refused_context')

    def test_exact_fence_required(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            database = Path(root)/'coord.sqlite'
            from contextlib import closing
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE gpu_tenancy(resource TEXT, owner TEXT, session_id TEXT)')
                conn.execute('INSERT INTO gpu_tenancy VALUES(?,?,?)', ('omen-b70-pool', 'experiment', 'correct'))
                conn.commit()
            direct.require_fence('correct', database)
            with self.assertRaises(direct.Refused):
                direct.require_fence('wrong', database)

if __name__ == '__main__':
    unittest.main()
