from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.execution import CapacityLeaseStore, CapacityUnavailable


class CapacityLeaseStoreTest(unittest.TestCase):
    def test_enforces_one_global_limit_across_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "coordination.sqlite"
            first = CapacityLeaseStore(path)
            second = CapacityLeaseStore(path)
            lease = first.acquire(
                scope="provider:am4-moe:model:gpt-oss-120b",
                job_id="job_a",
                invocation_id="inv_a",
                limit=1,
                ttl_seconds=30,
                now=100,
            )
            with self.assertRaises(CapacityUnavailable):
                second.acquire(
                    scope="provider:am4-moe:model:gpt-oss-120b",
                    job_id="job_b",
                    invocation_id="inv_b",
                    limit=1,
                    ttl_seconds=30,
                    now=101,
                )
            self.assertTrue(second.release(lease))
            self.assertEqual(0, first.active_count("provider:am4-moe:model:gpt-oss-120b", now=102))

    def test_expired_lease_is_reaped_during_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CapacityLeaseStore(Path(temporary) / "coordination.sqlite")
            store.acquire(
                scope="provider:a",
                job_id="job_a",
                invocation_id="inv_a",
                limit=1,
                ttl_seconds=5,
                now=100,
            )
            replacement = store.acquire(
                scope="provider:a",
                job_id="job_b",
                invocation_id="inv_b",
                limit=1,
                ttl_seconds=5,
                now=106,
            )
            self.assertTrue(replacement.startswith("lease_"))
            self.assertEqual(1, store.active_count("provider:a", now=107))

    def test_renewal_cannot_resurrect_expired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CapacityLeaseStore(Path(temporary) / "coordination.sqlite")
            lease = store.acquire(
                scope="provider:a",
                job_id="job_a",
                invocation_id="inv_a",
                limit=1,
                ttl_seconds=5,
                now=100,
            )
            self.assertFalse(store.renew(lease, ttl_seconds=5, now=106))


if __name__ == "__main__":
    unittest.main()
