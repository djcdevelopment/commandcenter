import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from tools.ops.bench27_guard import Trips, pending_jobs, sample_card, guard_failure


class GuardTests(unittest.TestCase):
    def test_independent_consecutive_trips_and_missing_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'cards.jsonl'
            guard = Trips(out, {'card2': 'a', 'card3': 'b'})
            self.assertEqual(guard.observe({'card2': {'vram_c': 104}, 'card3': {'vram_c': 99}}), [])
            self.assertEqual(guard.observe({'card2': {'vram_c': 99}, 'card3': {'vram_c': 104}}), [])
            trips = guard.observe({'card2': {'vram_c': 104}, 'card3': {'vram_c': 104}})
            self.assertEqual([t['backend'] for t in trips], ['b'])
            self.assertTrue(Path(str(out) + '.tripped').exists())
            trips = guard.observe({'card2': {'vram_c': None}, 'card3': {'vram_c': 105}})
            self.assertEqual([t['backend'] for t in trips], ['a'])
            self.assertIn('unavailable', trips[0]['reason'])

    def test_cancel_selection_includes_standalone_jobs_excludes_other_callers(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'projection.sqlite'
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE jobs (state_json TEXT, status TEXT)')
                for job, backend, owner, status in [('standalone', 'a', 'codex-cli', 'running'),
                                                   ('delivery', 'a', 'codex-cli', 'queued'),
                                                   ('other', 'a', 'claude-frontier', 'running'),
                                                   ('other_card', 'b', 'codex-cli', 'running'),
                                                   ('done', 'a', 'codex-cli', 'succeeded')]:
                    doc = {'job_id': job, 'desired': {'arguments': {'backend': backend}}, 'principal': {'id': owner, 'type': 'hearth_caller', 'authenticated': True}}
                    conn.execute('INSERT INTO jobs VALUES (?,?)', (json.dumps(doc), status))
            self.assertEqual(pending_jobs(db, 'a', 'codex-cli'), ['standalone', 'delivery'])

    def test_provider_resolution_and_principal_type(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'projection.sqlite'
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE jobs (state_json TEXT, status TEXT)')
                for typ in ('hearth_caller', 'delegated_user'):
                    d = {'job_id': typ, 'provider': 'a', 'principal': {'id': 'codex-cli', 'type': typ, 'authenticated': True, 'profile': 'unrestricted'}}
                    conn.execute('INSERT INTO jobs VALUES (?,?)', (json.dumps(d), 'running'))
            self.assertEqual(pending_jobs(db, 'a', 'codex-cli'), ['hearth_caller'])

    def test_guard_failure_leaves_sticky_marker(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'cards.jsonl'
            guard_failure(out, RuntimeError('not safe'))
            self.assertEqual(json.loads(Path(str(out)+'.tripped').read_text())['reason'], 'guard failure')

    def test_missing_sensor_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(sample_card('card2', Path(td))['vram_c'])


if __name__ == '__main__':
    unittest.main()
