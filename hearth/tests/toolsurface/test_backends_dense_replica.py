"""A same-model campaign replica must not receive model-only calls while absent."""
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.backends import BackendConfigError, BackendRoutingRefusal, load_pool, select_backend


class DenseReplicaRoutingTests(TestCase):
    def setUp(self):
        self.pool = load_pool(Path(__file__).resolve().parents[3] / 'host/omen-linux/hearth-production/backends-linux.toml')

    def test_model_uses_first_available_declared_provider(self):
        with patch('hearth.execution.lab_config.get_backend_status', return_value=('live', 'three-dense')):
            backend, _, _ = select_backend(self.pool, model='qwen3.8-27b')
        self.assertEqual(backend.name, 'omen-dense-27b')

    def test_model_does_not_fall_through_to_absent_replica(self):
        def status(name):
            return ('absent' if name == 'omen-dense-27b-b' else 'live', 'day')
        with patch('hearth.execution.lab_config.get_backend_status', side_effect=status):
            with self.assertRaises(BackendRoutingRefusal):
                select_backend(self.pool, model='qwen3.8-27b', exclude={'omen-dense-27b'})

    def test_empty_tags_are_not_a_wildcard(self):
        self.assertEqual(self.pool.by_name('omen-dense-27b-b').tags, ())
        with patch('hearth.execution.lab_config.get_backend_status', return_value=('live', 'three-dense')):
            backend, _, _ = select_backend(self.pool, tags=['quality'])
        self.assertEqual(backend.name, 'omen-dense-27b')

    def test_absent_default_falls_back_by_configuration(self):
        """ADR-0066: three-dense marks omen-vllm absent; a default call walks default_fallback, skipping exclusions."""
        with patch.dict(os.environ, {'HEARTH_LAB_CONFIGURATION': 'three-dense'}):
            backend, reason, occ = select_backend(self.pool, tags=['default'])
            self.assertEqual((backend.name, reason), ('omen-dense-27b', 'absent_fallback:omen-vllm->omen-dense-27b'))
            self.assertEqual(occ['fallback_from'], 'omen-vllm')
            backend, reason, _ = select_backend(self.pool, exclude={'omen-dense-27b'})
            self.assertEqual(reason, 'absent_fallback:omen-vllm->omen-dense-27b-b')

    def test_wholly_absent_capability_lane_refuses_loudly(self):
        with patch.dict(os.environ, {'HEARTH_LAB_CONFIGURATION': 'three-dense'}):
            with self.assertRaisesRegex(BackendConfigError, r"lane \['tool-use'\] absent under configuration three-dense: am4-tool"):
                select_backend(self.pool, tags=['tool-use'])
