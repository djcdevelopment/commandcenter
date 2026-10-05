"""A same-model campaign replica must not receive model-only calls while absent."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.backends import BackendRoutingRefusal, load_pool, select_backend


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
