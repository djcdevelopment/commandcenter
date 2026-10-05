import json
import subprocess
import os
import sys
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
            m.restore = Mock(side_effect=lambda m=m: m.save("restored"))
            m.wait_drained = Mock(return_value={"running": 0, "waiting": 0, "success": 0})
            m.seat_counters = Mock(return_value={"running": 0, "waiting": 0, "success": 0})
            m.record_argv = Mock(return_value=["serve"])
            m.refuse_busy = Mock()
            m.restart_and_wait = Mock()
            m.check_running_args = Mock()
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
        self.assertEqual(pair.members['1'].restore.call_count, 1)
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

    def test_real_lifecycles_and_campaign_with_fake_seat_io(self):
        pair = exp.PairedExperiment({**self.spec, 'campaign': [sys.executable, '-c', 'print(\'{"calls":{"0":0,"1":0}}\')']})
        pair.acquire = Mock(side_effect=lambda: pair.save('acquired', tenancy={'epoch': 1}))
        pair.release = Mock()
        pair._renew_loop = Mock()
        pair.preflight = Mock()  # binding and leasing checked separately; never touch live DB
        with patch.object(exp, 'UNIT_DIR', Path(self.tmp.name) / 'units'), \
                patch.object(exp.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='active')):
            for m in pair.members.values():
                m.service_d.mkdir(parents=True)
                if m.dropin:
                    (m.service_d / (m.dropin + '.staged')).write_text('[Service]\nEnvironment=TEST=1\n')
                m.unit_env = Mock(return_value={'OMEN_MODEL': '/fake/model'})
                m.stack_hashes = lambda model, m=m: {'model_dir': model, 'hashes': {str(p): exp.Experiment._sha(p) for p in m.service_d.glob('*.conf')}, 'weights': {}}
                proc = {'pid': '1', 'argv': ['vllm', 'serve', '/fake/model']}
                m.main_proc = lambda proc=proc: (proc['pid'], proc['argv'])
                m.effective_argv = lambda m=m: ['vllm', 'serve', '/fake/model'] + (['--test'] if m.dropin and (m.service_d / m.dropin).exists() else [])
                def restart(m=m, proc=proc):
                    proc.update(pid=str(int(proc['pid']) + 1), argv=m.effective_argv())
                m.restart_and_wait = Mock(side_effect=restart)
                m.served_models = Mock(return_value=['model'])
                m.seat_counters = Mock(return_value={'running': 0, 'waiting': 0, 'success': 0})
            self.assertEqual(pair.run(), 0)
            self.assertEqual(pair.members['0'].restart_and_wait.call_count, 1)
            self.assertEqual(pair.members['1'].restart_and_wait.call_count, 2)
            self.assertEqual(pair.state['foreign_requests_by_seat'], {'0': 0, '1': 0})
            pair.restore()
            self.assertEqual(pair.members['1'].restart_and_wait.call_count, 2)

    def test_backend_seat_binding_rejects_reversed_registry_endpoints(self):
        pair = exp.PairedExperiment(self.spec)
        registry = Path(self.tmp.name) / 'backends.toml'
        registry.write_text('[[backend]]\nname="omen-dense-27b"\nendpoint="http://127.0.0.1:18096"\n'
                            '[[backend]]\nname="omen-dense-27b-b"\nendpoint="http://127.0.0.1:18095"\n')
        for m in pair.members.values():
            m.preflight = Mock()
        with patch.dict(os.environ, {'HEARTH_BACKENDS': str(registry)}), self.assertRaises(RuntimeError):
            pair.preflight()
        for m in pair.members.values():
            m.preflight.assert_not_called()

    def test_restore_attempts_second_seat_after_baseexception(self):
        pair = self.prepared()
        pair.members['0'].restore.side_effect = SystemExit('unexpected callback')
        with self.assertRaises(RuntimeError):
            pair.restore()
        pair.members['1'].restore.assert_called_once()

    def test_drain_waits_then_succeeds_or_times_out(self):
        member = exp.Experiment({'id': 'drain', 'seat': 0, 'dropin': None})
        member.seat_counters = Mock(side_effect=[{'running': 1, 'waiting': 0}, {'running': 0, 'waiting': 0}])
        with patch.object(exp.time, 'sleep'):
            self.assertEqual(member.wait_drained()['running'], 0)
        member.seat_counters = Mock(return_value={'running': 1, 'waiting': 0})
        with self.assertRaises(RuntimeError):
            member.wait_drained(0)

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
