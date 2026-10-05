import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

SPEC = importlib.util.spec_from_file_location('am4probe', Path(__file__).with_name('bench27_am4_probe.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / 'guard.jsonl'
        self.trip = Path(str(self.log) + '.tripped')
        self.sample = {'utc': datetime.now(timezone.utc).isoformat(), 'gpus': [
            {'pci': '00000000:09:00.0', 'gpu_core_c': 60},
            {'pci': '00000000:0a:00.0', 'gpu_core_c': 65}]}
        self.log.write_text(json.dumps(self.sample) + '\n')

    def test_guard_fresh_stale_trip_and_nonfinite(self):
        m.guard_check(self.log, self.trip)
        with self.assertRaises(m.InfraError):
            m.guard_check(self.log, self.trip, time.time() + 41)
        self.trip.touch()
        with self.assertRaises(m.InfraError):
            m.guard_check(self.log, self.trip)
        self.trip.unlink()
        self.sample['gpus'][0]['gpu_core_c'] = float('nan')
        self.log.write_text(json.dumps(self.sample))
        with self.assertRaises(m.InfraError):
            m.guard_check(self.log, self.trip)

    def test_event_separates_reasoning_without_repairing_content(self):
        record = {'content': '', 'reasoning': '', 'done': False}
        m.event(json.dumps({'choices': [{'delta': {'reasoning_content': 'think', 'content': '<think>raw</think>answer'}, 'finish_reason': 'stop'}]}), record, 1.2)
        m.event('[DONE]', record, 1.3)
        self.assertEqual(record['content'], '<think>raw</think>answer')
        self.assertEqual(record['reasoning'], 'think')
        self.assertEqual(record['ttft_s'], 1.2)
        self.assertTrue(record['done'])
        with self.assertRaises(ValueError):
            m.event('broken json', record, 1.4)

    def test_truth_requires_exact_keys_values_and_stop(self):
        truth = {'01': 'abcd1234'}
        correct = json.dumps({'needles': truth})
        self.assertTrue(m.grade(correct, truth, 'stop')['ok'])
        self.assertFalse(m.grade(correct, truth, 'length')['ok'])
        self.assertFalse(m.grade(json.dumps({'needles': truth, 'extra': True}), truth, 'stop')['ok'])
        self.assertFalse(m.grade('```' + correct + '```', truth, 'stop')['ok'])

    def test_abort_shuts_down_inflight_socket(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        right.settimeout(1)
        c = m.Client.__new__(m.Client)
        c.sock, c.conn, c.failure = left, None, None
        c.abort('SIGTERM')
        self.assertEqual(right.recv(1), b'')
        self.assertEqual(c.failure, 'SIGTERM')

    def client(self):
        c = m.Client(self.port if hasattr(self, 'port') else 18094, self.log, self.trip, 2)
        self.addCleanup(lambda: (c.closed.set(), c.watcher.join(timeout=1)))
        c.tokens = Mock(return_value=100)
        c.fetch = Mock(return_value=b'metrics')
        return c

    def test_stream_preserves_wire_and_rejects_missing_done(self):
        c = self.client()
        response = io.BytesIO(b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n')
        response.status = 200
        c.request = Mock(return_value=response)
        body = {'model': 'am4-dense-27b', 'messages': [], 'max_tokens': 600, 'chat_template_kwargs': {'enable_thinking': False}}
        rec = c.stream(body, self.root / 'call', 16384)
        self.assertIn('done=False', rec['error'])
        self.assertTrue((self.root / 'call/response.sse').read_bytes())
        self.assertEqual(json.loads((self.root / 'call/request.json').read_text())['model'], 'qwen3-27b')

    def test_exact_admission_rejects_before_inference(self):
        c = self.client()
        c.tokens.return_value = 16000
        c.request = Mock()
        rec = c.stream({'max_tokens': 600}, self.root / 'too-big', 16384)
        self.assertIn('exceeds', rec['error'])
        c.request.assert_not_called()

    def test_watchdog_stops_inflight_socket_on_trip(self):
        c = self.client()
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        right.settimeout(2)
        c.sock = left
        self.trip.touch()
        self.assertEqual(right.recv(1), b'')
        self.assertIn('trip', c.failure)


if __name__ == '__main__':
    unittest.main()
