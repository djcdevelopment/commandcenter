import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import tempfile
import json
from tools.ops.bench27_am4_guard import parse_samples, main


class AM4Telemetry(unittest.TestCase):
    def test_records_each_gpu_and_retains_hot_sample(self):
        rows = parse_samples('00000000:09:00.0, 89, 110.5, 10000\n00000000:0A:00.0, 90, 111, 11000\n')
        self.assertEqual([r['gpu_core_c'] for r in rows], [89, 90])

    def test_missing_wrong_duplicate_and_unknown_telemetry_refused(self):
        for text in ['', '00000000:09:00.0, 80, 110, 10000',
                     '00000000:09:00.0, 80, 110, 10000\n00000000:09:00.0, 80, 110, 10000',
                     '00000000:09:00.0, N/A, 110, 10000\n00000000:0A:00.0, 80, 110, 10000']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_samples(text)

    def test_hot_or_unreadable_sample_stops_each_cycle_and_shutdown(self):
        for initial in ([{'pci': 'x', 'gpu_core_c': 90}], RuntimeError('SSH unavailable')):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as td:
                out = Path(td) / 'am4.jsonl'
                args = SimpleNamespace(out=out, on_trip='owned-lap-stop')
                samples = [initial, [{'pci': 'x', 'gpu_core_c': 89}]]
                with patch('tools.ops.bench27_am4_guard.Path.home', return_value=Path(td)), \
                     patch('tools.ops.bench27_am4_guard.argparse.ArgumentParser.parse_args', return_value=args), \
                     patch('tools.ops.bench27_am4_guard.signal.signal'), \
                     patch('tools.ops.bench27_am4_guard.sample', side_effect=samples), \
                     patch('tools.ops.bench27_am4_guard.pending_jobs', return_value=['owned-job']), \
                     patch('tools.ops.bench27_am4_guard.door_action', return_value=[]) as cancel, \
                     patch('tools.ops.bench27_am4_guard.stop_campaign', return_value={'returncode': 0}) as stop, \
                     patch('tools.ops.bench27_am4_guard.time.sleep', side_effect=[None, SystemExit(143)]):
                    with self.assertRaises(SystemExit):
                        main()
                self.assertTrue(Path(str(out)+'.tripped').exists())
                self.assertTrue(Path(str(out)+'.guard-failure').exists())
                self.assertEqual(stop.call_count, 3)
                self.assertEqual(len([c for c in cancel.call_args_list if c.args[0]=='cancel']), 3)
                self.assertEqual(len(out.read_text().splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
