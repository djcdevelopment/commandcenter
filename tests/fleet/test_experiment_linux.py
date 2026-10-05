import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fleet import experiment_linux as exp


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(exp, 'EXP_ROOT', Path(self.tmp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for sig in (signal.SIGTERM, signal.SIGINT):
            old = signal.getsignal(sig)
            self.addCleanup(signal.signal, sig, old)
        self.spec = {'id': 'pair', 'seats': [0, 1],
                     'dropin': {'0': None, '1': 'zz-arm'},
                     'backend': {'0': 'omen-dense-27b', '1': 'omen-dense-27b-b'}}

    def prepared(self):
        pair = exp.PairedExperiment(self.spec)
        pair.preflight = Mock()
        pair.acquire = Mock(side_effect=lambda: pair.save('acquired', tenancy={'epoch': 1}))
        pair.release = Mock()
        pair.campaign = Mock(side_effect=lambda: pair.save(campaign_rc=0, calls_reported={'0': 1, '1': 2}, foreign_requests=0))
        for m in pair.members.values():
            m.take_snapshot = Mock(side_effect=lambda m=m: m.save(resident={'served': [str(m.seat)]}))
            m.swap = Mock(side_effect=lambda m=m: m.save(effective_argv=['serve'], served_after_swap=[str(m.seat)]))
            m.restore = Mock()
        return pair

    def test_one_fence_two_restores(self):
        pair = self.prepared()
        self.assertEqual(pair.run(), 0)
        pair.acquire.assert_called_once()
        pair.release.assert_called_once()
        for m in pair.members.values():
            m.restore.assert_called_once()
        self.assertEqual(set(pair.state['resident']), {'0', '1'})

    def test_partial_swap_failure_restores_both(self):
        pair = self.prepared()
        pair.members['1'].swap.side_effect = RuntimeError('second restart failed')
        self.assertEqual(pair.run(), 1)
        pair.campaign.assert_not_called()
        for m in pair.members.values():
            m.restore.assert_called_once()
        pair.release.assert_called_once()

    def test_failed_restore_keeps_fence_and_attempts_other_seat(self):
        pair = self.prepared()
        pair.members['0'].restore.side_effect = RuntimeError('hash mismatch')
        self.assertEqual(pair.run(), 2)
        pair.release.assert_not_called()
        self.assertEqual(pair.members['1'].restore.call_count, 2)
        self.assertEqual(pair.state['outcome'], 'restore_failed')

    def test_per_seat_counts_cannot_cancel(self):
        pair = exp.PairedExperiment(self.spec)
        before = {'0': {'success': 10}, '1': {'success': 10}}
        after = {'0': {'success': 12}, '1': {'success': 10}}
        self.assertEqual(pair.foreign_requests(before, after, {'0': 1, '1': 1}), 2)
        self.assertEqual(pair.state['foreign_requests_by_seat'], {'0': 1, '1': -1})

    def test_calls_require_each_seat(self):
        pair = exp.PairedExperiment(self.spec)
        for calls, expected in [(3, None), ({'0': 1}, None), ({'0': 1, '1': 2}, {'0': 1, '1': 2})]:
            (pair.dir / 'campaign.out').write_text(json.dumps({'calls': calls}))
            self.assertEqual(pair._calls_reported(0), expected)

    def test_single_spec_remains_compatible(self):
        single = exp.Experiment({'id': 'single', 'seat': 0, 'dropin': None})
        self.assertEqual(single.foreign_requests({'success': 2}, {'success': 5}, 3), 0)
        self.assertIsNone(single.foreign_requests({'success': 2}, {'success': 5}, None))

    def test_ambiguous_spec_rejected(self):
        with self.assertRaises(ValueError):
            exp.PairedExperiment({**self.spec, 'seat': 0})
        with self.assertRaises(ValueError):
            exp.PairedExperiment({**self.spec, 'backend': {'0': 'same', '1': 'same'}})


if __name__ == '__main__':
    unittest.main()
