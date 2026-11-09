"""A same-model campaign replica must not receive model-only calls while absent."""
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest import TestCase
from unittest.mock import patch

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.operations import load_operations
from hearth.execution.service import ExecutionService
from hearth.toolsurface.backends import BackendConfigError, BackendRoutingRefusal, load_pool, select_backend
from hearth.toolsurface.occupancy import OccupancyCache, check_occupancy, routing_reservation_context


class DenseReplicaRoutingTests(TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.backends_path = self.repo_root / 'host/omen-linux/hearth-production/backends-linux.toml'
        self.families_path = self.repo_root / 'host/omen-linux/hearth-production/routing-families-linux.toml'
        self.lab_configs_path = self.repo_root / 'host/lab-configurations.toml'
        self.pool = load_pool(self.backends_path)

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

    def test_absent_fallback_and_model_distribute_when_first_replica_has_active_reservation(self):
        """When omen-dense-27b has 1 active reservation (even below parallel_slots=2) and omen-dense-27b-b has 0,
        both absent_fallback and model='qwen3.8-27b' route the next call to omen-dense-27b-b."""
        occ_map = {
            'omen-dense-27b': {
                'occupancy': 'available',
                'detail': {'active_reservations': 0, 'parallel_slots': 2},
            },
            'omen-dense-27b-b': {
                'occupancy': 'available',
                'detail': {'active_reservations': 0, 'parallel_slots': 2},
            },
            'am4-vllm': {
                'occupancy': 'unknown',
                'detail': {'alias': 'am4-dense-27b', 'reason': 'no AM4_VLLM_TOKEN in the environment'},
            },
        }
        with patch.dict(os.environ, {
            'HEARTH_LAB_CONFIGURATIONS': str(self.lab_configs_path),
            'HEARTH_LAB_CONFIGURATION': 'three-dense',
        }):
            # 1) 0 reservations on both -> omen-dense-27b first
            backend0, reason0, _ = select_backend(
                self.pool, tags=['default'], occupancy_check=lambda name: occ_map.get(name, {'occupancy': 'unknown'})
            )
            self.assertEqual(backend0.name, 'omen-dense-27b')
            self.assertEqual(reason0, 'absent_fallback:omen-vllm->omen-dense-27b')

            # 2) 1 active reservation on omen-dense-27b, 0 on omen-dense-27b-b -> omen-dense-27b-b
            occ_map['omen-dense-27b']['detail']['active_reservations'] = 1
            backend1, reason1, _ = select_backend(
                self.pool, tags=['default'], occupancy_check=lambda name: occ_map.get(name, {'occupancy': 'unknown'})
            )
            self.assertEqual(backend1.name, 'omen-dense-27b-b')
            self.assertEqual(reason1, 'absent_fallback:omen-vllm->omen-dense-27b-b')

            # 3) model='qwen3.8-27b' also selects omen-dense-27b-b when omen-dense-27b has 1 reservation
            m_backend, m_reason, _ = select_backend(
                self.pool, model='qwen3.8-27b', occupancy_check=lambda name: occ_map.get(name, {'occupancy': 'unknown'})
            )
            self.assertEqual(m_backend.name, 'omen-dense-27b-b')
            self.assertEqual(m_reason, 'model:qwen3.8-27b')

    def test_unknown_occupancy_is_never_treated_as_available_capacity(self):
        """A candidate with occupancy == 'unknown' (e.g. am4-vllm without token/readiness or unreachable vLLM seat)
        must never be selected as available fallback capacity."""
        with patch.dict(os.environ, {
            'HEARTH_LAB_CONFIGURATIONS': str(self.lab_configs_path),
            'HEARTH_LAB_CONFIGURATION': 'three-dense',
        }):
            # Seat 0 unreachable ('unknown'), Seat 1 available -> Seat 1 chosen
            occ_partial = {
                'omen-dense-27b': {'occupancy': 'unknown', 'detail': {'error': 'ConnectionRefusedError'}},
                'omen-dense-27b-b': {'occupancy': 'available', 'detail': {'active_reservations': 0, 'parallel_slots': 2}},
                'am4-vllm': {'occupancy': 'unknown', 'detail': {'reason': 'no AM4_VLLM_TOKEN'}},
            }
            backend, reason, _ = select_backend(
                self.pool, tags=['default'], occupancy_check=lambda name: occ_partial.get(name, {'occupancy': 'unknown'})
            )
            self.assertEqual(backend.name, 'omen-dense-27b-b')
            self.assertEqual(reason, 'absent_fallback:omen-vllm->omen-dense-27b-b')

            # All fallback candidates unknown (including am4-vllm) -> refuses loudly
            with self.assertRaisesRegex(BackendConfigError, r"no live default_fallback fits"):
                select_backend(
                    self.pool, tags=['default'], occupancy_check=lambda _name: {'occupancy': 'unknown'}
                )

    def test_dense_seat_probe_refreshes_live_reservations_without_serving_stale_cache(self):
        """check_occupancy for omen-dense-27b and omen-dense-27b-b refreshes active_leases and active_jobs
        on every check even when the HTTP /metrics reading is served from the 30s cache."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leases = CapacityLeaseStore(root / 'coordination.sqlite')
            ledger = ExecutionLedger(root / 'ledger')
            cache = OccupancyCache(ttl_s=30.0)
            gauges = {'vllm:num_requests_running': 0.0, 'vllm:num_requests_waiting': 0.0}
            with patch('hearth.toolsurface.occupancy._fetch_vllm_metrics', return_value=(gauges, None)) as mock_metrics, \
                  patch('hearth.toolsurface.occupancy._tenancy_fence', return_value=None), \
                 routing_reservation_context(leases=leases, ledger=ledger):
                occ0 = check_occupancy('omen-dense-27b', cache=cache)
                self.assertEqual(occ0['occupancy'], 'available')
                self.assertEqual(occ0['detail']['active_reservations'], 0)
                self.assertEqual(occ0['detail']['active_leases'], 0)
                self.assertEqual(mock_metrics.call_count, 1)

                # Acquire 1 lease: cached check must immediately reflect active_reservations=1 without re-fetching HTTP metrics
                lease1 = leases.acquire(
                    scope='provider:omen-dense-27b', job_id='job_1', invocation_id='inv_1', limit=2, ttl_seconds=60
                )
                occ1 = check_occupancy('omen-dense-27b', cache=cache)
                self.assertTrue(occ1['cached'])
                self.assertEqual(mock_metrics.call_count, 1)
                self.assertEqual(occ1['occupancy'], 'available')
                self.assertEqual(occ1['detail']['active_leases'], 1)
                self.assertEqual(occ1['detail']['active_reservations'], 1)

                # Acquire 2nd lease: cached check must immediately transition occupancy to 'busy' (2 >= parallel_slots=2)
                lease2 = leases.acquire(
                    scope='provider:omen-dense-27b', job_id='job_2', invocation_id='inv_2', limit=2, ttl_seconds=60
                )
                occ2 = check_occupancy('omen-dense-27b', cache=cache)
                self.assertEqual(occ2['occupancy'], 'busy')
                self.assertEqual(occ2['detail']['active_leases'], 2)
                self.assertEqual(occ2['detail']['active_reservations'], 2)

                leases.release(lease1)
                leases.release(lease2)

    def test_concurrent_admissions_distribute_across_two_dense_replicas(self):
        """Two concurrent unpinned submissions arriving milliseconds apart must not both overlook an existing
        reservation and must distribute across omen-dense-27b and omen-dense-27b-b."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            both_entered = threading.Barrier(2, timeout=5.0)
            release_gate = threading.Event()
            dispatched_backends: list[str] = []
            lock = threading.Lock()

            def fake_generate(prompt, *, system=None, backend=None, model=None, max_tokens=2048,
                              temperature=None, top_p=None, seed=None, response_schema=None,
                              files=None, task=None, quality=None, **_kwargs):
                with lock:
                    dispatched_backends.append(backend)
                try:
                    both_entered.wait()
                except threading.BrokenBarrierError:
                    pass
                release_gate.wait(timeout=5.0)
                return {
                    'ok': True,
                    'text': f'ok from {backend}',
                    'provider': backend,
                    'model': model or 'qwen3.8-27b',
                    'routed_by': f'pinned:{backend}',
                    'tokens_in': 10,
                    'tokens_out': 5,
                    'duration_ms': 5,
                }

            ops = load_operations()
            gauges = {'vllm:num_requests_running': 0.0, 'vllm:num_requests_waiting': 0.0}
            env = {
                'HEARTH_BACKENDS': str(self.backends_path),
                'HEARTH_ROUTING_FAMILIES': str(self.families_path),
                'HEARTH_LAB_CONFIGURATIONS': str(self.lab_configs_path),
                'HEARTH_LAB_CONFIGURATION': 'three-dense',
                'HEARTH_EXECUTION_DIR': str(root / 'ledger'),
            }
            with patch.dict(os.environ, env), \
                 patch('hearth.toolsurface.occupancy._fetch_vllm_metrics', return_value=(gauges, None)), \
                 patch('hearth.toolsurface.occupancy._tenancy_fence', return_value=None):
                from hearth.toolsurface.occupancy import default_cache
                default_cache().invalidate()
                service = ExecutionService(
                    ledger=ExecutionLedger(root / 'ledger'),
                    artifacts=ArtifactStore(root / 'artifacts'),
                    leases=CapacityLeaseStore(root / 'coordination.sqlite'),
                    operations=ops,
                    generate=fake_generate,
                    workers=4,
                    recover_pending=False,
                )
                try:
                    principal = {'type': 'hearth_caller', 'id': 'test-concurrent', 'authenticated': True}
                    source = {'transport': 'mcp', 'adapter': 'unit-test'}
                    j1 = service.submit(
                        operation_name='inference.generate',
                        arguments={'prompt': 'first concurrent request', 'task_family': 'coding'},
                        principal=principal,
                        source=source,
                    )
                    j2 = service.submit(
                        operation_name='inference.generate',
                        arguments={'prompt': 'second concurrent request', 'task_family': 'coding'},
                        principal=principal,
                        source=source,
                    )
                    release_gate.set()

                    def wait_final(job_id: str) -> dict:
                        deadline = time.monotonic() + 5.0
                        while time.monotonic() < deadline:
                            st = service.get_job(job_id)
                            if st and st.get('status') in {'succeeded', 'failed', 'cancelled', 'expired'}:
                                return st
                            time.sleep(0.01)
                        self.fail(f'job {job_id} did not reach final state')

                    f1 = wait_final(j1['job_id'])
                    f2 = wait_final(j2['job_id'])
                    self.assertEqual(f1['status'], 'succeeded')
                    self.assertEqual(f2['status'], 'succeeded')
                    providers = {f1['provider'], f2['provider']}
                    self.assertEqual(providers, {'omen-dense-27b', 'omen-dense-27b-b'})
                    routed_by_set = {f1['routed_by'], f2['routed_by']}
                    self.assertEqual(
                        routed_by_set,
                        {
                            'family:default:absent_fallback:omen-vllm->omen-dense-27b',
                            'family:default:absent_fallback:omen-vllm->omen-dense-27b-b',
                        },
                    )
                finally:
                    release_gate.set()
                    service.close()
