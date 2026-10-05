"""Profile displacement is reversible, hash-verified, and rolled back with a failed switch."""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'host/omen-linux/bin/omen-profile'
spec = importlib.util.spec_from_loader('omen_profile_replacement', SourceFileLoader('omen_profile_replacement', str(SCRIPT)))
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.unit = self.home / '.config/systemd/user'
        self.snap = self.home / '.config/omen-vllm/snapshots'
        self.snap.mkdir(parents=True)
        self.state = self.home / '.config/omen-vllm/profile'
        self.state.write_text('two-lane')
        self.profiles = ROOT / 'host/omen-linux/profiles'
        self.dense = json.loads((self.profiles / 'two-dense.json').read_text())
        self.normal = json.loads((self.profiles / 'two-lane.json').read_text())
        for seat in (0, 1):
            target = self.unit / f'omen-vllm@{seat}.service.d'
            target.mkdir(parents=True)
            source = ROOT / f'host/omen-linux/systemd/omen-vllm@{seat}.service.d'
            for name in self.normal['seats'][str(seat)]['dropins']:
                shutil.copy2(source / name, target / name)
            if seat == 1:
                for name in ('attn-backend.conf', 'stage9-two-dense.conf.staged'):
                    shutil.copy2(source / name, target / name)
        self.seat = self.unit / 'omen-vllm@1.service.d'
        self.originals = self.active_files()
        for name, value in [('HOME', self.home), ('UNIT_DIR', self.unit), ('SNAPSHOT_DIR', self.snap),
                            ('PROFILE_STATE_FILE', self.state), ('PROFILE_DIRS', [self.profiles])]:
            p = patch.object(op, name, value); p.start(); self.addCleanup(p.stop)

    def active_files(self):
        return {p.name: p.read_bytes() for p in self.seat.glob('*.conf')}

    def test_enter_has_single_setter_and_exit_restores_exact_originals(self):
        expected_env = op.build_seat_dry_env(1, self.dense['seats']['1'])
        op.apply_replacement(1, self.dense['seats']['1'])
        self.assertEqual(set(self.active_files()), {'stage9-two-dense.conf'})
        for key, value in [('OMEN_MTP_K', '2'), ('OMEN_ATTN_BACKEND', 'FLASH_ATTN'),
                           ('OMEN_MAX_MODEL_LEN', '65536'), ('OMEN_MAX_NUM_SEQS', '4')]:
            self.assertEqual(expected_env[key], value)
        original_env = op.build_seat_dry_env(1, self.normal['seats']['1'])
        self.assertEqual(original_env['OMEN_MAX_MODEL_LEN'], '40960')
        self.assertEqual(original_env['OMEN_MAX_NUM_SEQS'], '8')
        op.apply_replacement(1, self.normal['seats']['1'])
        self.assertEqual(self.active_files(), self.originals)
        self.assertFalse(op.replacement_journal(1).exists())

    def test_corrupt_snapshot_refuses_restoration_without_changes(self):
        op.apply_replacement(1, self.dense['seats']['1'])
        state = op.replacement_state(1)
        Path(next(iter(state['originals'].values()))['path']).write_text('corrupted')
        before = self.active_files()
        with self.assertRaisesRegex(RuntimeError, 'integrity failed'):
            op.restore_replacement(1)
        self.assertEqual(before, self.active_files())

    def test_reentry_preserves_originals(self):
        op.apply_replacement(1, self.dense['seats']['1'])
        op.apply_replacement(1, self.dense['seats']['1'])
        op.restore_replacement(1)
        self.assertEqual(self.active_files(), self.originals)

    def run_switch(self, target, fail_wait=False):
        store = Mock()
        store.active_owner.return_value = None
        store.acquire.return_value = types.SimpleNamespace(epoch=1)
        coordination = types.ModuleType('hearth.execution.coordination')
        coordination.GpuTenancyStore = lambda: store
        failed = False
        def run(argv, **kwargs):
            nonlocal failed
            if fail_wait and str(argv[0]) == str(op.WAIT_SCRIPT) and kwargs.get('capture_output') and not failed:
                failed = True
                return subprocess.CompletedProcess(argv, 1, 'load failed', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        def models(port, key):
            if port == 18091 or (self.seat / 'stage9-two-dense.conf').exists():
                return ['qwen3.8-27b']
            return ['qwen3-30b-a3b']
        with patch.dict('sys.modules', {'hearth.execution.coordination': coordination}), \
             patch.object(op, 'count_active_jobs', return_value=0), \
             patch.object(op, 'get_vllm_key', return_value=''), \
             patch.object(op, 'query_live_models', side_effect=models), \
             patch.object(op, 'cmd_status', return_value=0), \
             patch.object(op.subprocess, 'run', side_effect=run):
            rc = op.cmd_switch(target)
        self.assertEqual(store.release.call_count, 1)
        return rc

    def test_switch_enter_and_exit_restore(self):
        self.assertEqual(self.run_switch('two-dense'), 0)
        self.assertTrue(op.replacement_journal(1).exists())
        self.assertEqual(self.run_switch('two-lane'), 0)
        self.assertEqual(self.active_files(), self.originals)
        self.assertFalse(op.replacement_journal(1).exists())

    def test_failed_enter_restores_configuration_and_journal(self):
        self.assertEqual(self.run_switch('two-dense', fail_wait=True), 1)
        self.assertEqual(self.active_files(), self.originals)
        self.assertEqual(self.state.read_text(), 'two-lane')
        self.assertFalse(op.replacement_journal(1).exists())

    def test_failed_exit_restores_dense_and_retains_originals(self):
        self.assertEqual(self.run_switch('two-dense'), 0)
        dense = self.active_files()
        journal = op.replacement_journal(1).read_bytes()
        self.assertEqual(self.run_switch('two-lane', fail_wait=True), 1)
        self.assertEqual(self.active_files(), dense)
        self.assertEqual(op.replacement_journal(1).read_bytes(), journal)
        self.assertEqual(self.state.read_text(), 'two-dense')
        op.restore_replacement(1)
        self.assertEqual(self.active_files(), self.originals)


    def test_manifest_checks_displaced_originals_and_active_recipe(self):
        host_spec = importlib.util.spec_from_file_location('replacement_host_config', ROOT / 'tools/ops/host_config.py')
        hc = importlib.util.module_from_spec(host_spec)
        host_spec.loader.exec_module(hc)
        op.apply_replacement(1, self.dense['seats']['1'])
        with patch.object(hc, 'HOME', self.home):
            pairs = hc.active_manifest()
            prefix = 'systemd/omen-vllm@1.service.d/'
            for name in self.dense['seats']['1']['replace_dropins']:
                path = next(live for rel, live in pairs if rel == prefix + name)
                self.assertTrue(path.is_relative_to(self.snap))
                self.assertEqual(path.read_bytes(), self.originals[name])
            self.assertIn((prefix + 'stage9-two-dense.conf.staged', self.seat / 'stage9-two-dense.conf'), pairs)
            (self.seat / 'max-num-seqs.conf').write_text('unexpected active override')
            with self.assertRaisesRegex(ValueError, 'still active'):
                hc.active_manifest()
