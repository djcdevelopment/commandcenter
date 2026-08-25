"""Render scheduler tests.

The load-bearing assertion is about threads, not throughput:

    A render waiting for capacity must not consume a render executor thread.

The LLM path spins in its worker while waiting for a provider lease. Copying
that here would park both render workers on sleep loops and, worse, is invisible
in any test that only checks "the job eventually ran". So several tests below
assert ``active_count()`` -- workers actually occupied -- rather than outcomes.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from hearth.execution import CapacityLeaseStore
from hearth.media import lanes as L
from hearth.media import scheduler as S
from hearth.media.acceptance import Acceptance


def lane(lane_id, bus, luid, healthy=True):
    return L.Lane(
        lane_id=lane_id, pci_bus=bus, device_uuid="uuid-%d" % bus, luid=luid,
        child_device=bus, media_engines=["videodecode"], engine_profile={},
        engtypes=[], healthy=healthy, detail="",
    )


LANE_A = lane("b70@bus4", 4, "luid_0x00000000_0x00016d21")
LANE_B = lane("b70@bus9", 9, "luid_0x00000000_0x0001714b")


def accepted(count):
    return lambda: Acceptance(count, {}, "", False, {})


class SchedulerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # ignore_cleanup_errors: on Windows a worker thread can still hold the
        # sqlite handle for a moment after the test body ends.
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.leases = CapacityLeaseStore(Path(self._temp.name) / "coordination.sqlite")
        self.started = threading.Event()
        self.release = threading.Event()
        self.ran = []
        self.lock = threading.Lock()

    def blocking_runner(self, **kwargs):
        with self.lock:
            self.ran.append(kwargs)
        self.started.set()
        self.release.wait(10)

    def make(self, lanes, count=2, **kw):
        sched = S.RenderScheduler(
            runner=kw.pop("runner", self.blocking_runner),
            leases=self.leases,
            lanes_provider=lambda: lanes,
            acceptance_provider=accepted(count),
            autostart=False,
            **kw,
        )
        # Release the blocking runner FIRST, then wait for workers to drain,
        # so nothing is still touching the lease store at teardown.
        self.addCleanup(lambda: (self.release.set(), sched.close(wait=True)))
        return sched


class ThreadOccupancyTest(SchedulerTestBase):
    def test_two_eligible_lanes_run_two_renders_concurrently(self) -> None:
        sched = self.make([LANE_A, LANE_B], count=2)
        sched.enqueue("job_1")
        sched.enqueue("job_2")
        sched.pump()
        self.assertEqual(2, sched.active_count())
        self.assertEqual(0, sched.queue_depth())
        self.assertEqual({"b70@bus4", "b70@bus9"}, set(sched.active_lanes().values()))

    def test_third_job_stays_queued_and_occupies_no_worker(self) -> None:
        # THE invariant. A third render must wait in durable queued state, not
        # in a worker.
        sched = self.make([LANE_A, LANE_B], count=2)
        for job in ("job_1", "job_2", "job_3"):
            sched.enqueue(job)
        sched.pump()
        self.assertEqual(2, sched.active_count(), "only two lanes exist")
        self.assertEqual(1, sched.queue_depth(), "the third job must still be queued")
        self.assertEqual(2, len(self.ran), "a queued job must not have started")

    def test_one_lane_withheld_runs_exactly_one_render(self) -> None:
        withhold = {"b70@bus9"}
        sched = self.make(
            [LANE_A, LANE_B], count=2,
            gate=lambda lane: (lane.lane_id in withhold, "bf6_running"),
        )
        for job in ("job_1", "job_2"):
            sched.enqueue(job)
        sched.pump()
        self.assertEqual(1, sched.active_count())
        self.assertEqual(1, sched.queue_depth())
        self.assertEqual("b70@bus4", list(sched.active_lanes().values())[0],
                         "bus9 is withheld, so the render falls to bus4")

    def test_both_lanes_withheld_occupies_zero_workers(self) -> None:
        sched = self.make(
            [LANE_A, LANE_B], count=2,
            gate=lambda lane: (True, "obs_recording"),
        )
        for job in ("job_1", "job_2", "job_3"):
            sched.enqueue(job)
        sched.pump()
        self.assertEqual(0, sched.active_count(), "nothing may spin while gated")
        self.assertEqual(3, sched.queue_depth())
        self.assertEqual([], self.ran)

    def test_releasing_a_lane_dispatches_queued_work(self) -> None:
        sched = self.make([LANE_A], count=1)
        sched.enqueue("job_1")
        sched.enqueue("job_2")
        sched.pump()
        self.assertEqual(1, sched.active_count())
        self.assertEqual(1, sched.queue_depth())

        # Finish the first render; its lane and worker come back.
        self.release.set()
        deadline = threading.Event()
        for _ in range(100):
            if sched.active_count() == 0:
                break
            deadline.wait(0.05)
        sched.pump()
        self.assertEqual(1, sched.active_count())
        self.assertEqual(0, sched.queue_depth())

    def test_enqueue_is_idempotent(self) -> None:
        sched = self.make([LANE_A], count=1)
        sched.enqueue("job_1")
        sched.enqueue("job_1")
        self.assertEqual(1, sched.queue_depth())

    def test_a_running_job_is_not_re_dispatched(self) -> None:
        sched = self.make([LANE_A, LANE_B], count=2)
        sched.enqueue("job_1")
        sched.pump()
        sched.enqueue("job_1")
        sched.pump()
        self.assertEqual(1, sched.active_count())
        self.assertEqual(1, len(self.ran))

    def test_lease_is_released_even_when_the_runner_raises(self) -> None:
        def boom(**kwargs):
            raise RuntimeError("ffmpeg exploded")

        errors = []
        sched = self.make([LANE_A], count=1, runner=boom,
                          on_error=lambda job, exc: errors.append((job, exc)))
        sched.enqueue("job_1")
        sched.pump()
        self.assertTrue(sched.wait_idle(10))
        self.assertEqual(0, self.leases.active_count(S.lane_scope("b70@bus4")))
        self.assertEqual(1, len(errors))
        self.assertEqual("job_1", errors[0][0])
        # And the freed lane is genuinely reusable -- a runner crash must not
        # strand the lane behind a leaked lease.
        sched.enqueue("job_2")
        sched.pump()
        self.assertTrue(sched.wait_idle(10))
        self.assertEqual(2, len(errors), "the second job should also have run")
        self.assertEqual(0, self.leases.active_count(S.lane_scope("b70@bus4")))


class LaneSelectionTest(SchedulerTestBase):
    def _select(self, lanes, **kw):
        kw.setdefault("accepted_lane_count", 2)
        return S.select_lane_candidates(lanes, leases=self.leases, **kw)

    def test_is_deterministic_across_repeated_calls(self) -> None:
        for _ in range(10):
            candidates, _ = self._select([LANE_B, LANE_A])
            self.assertEqual(["b70@bus9", "b70@bus4"],
                             [lane.lane_id for lane in candidates])

    def test_input_order_does_not_change_the_decision(self) -> None:
        forward, _ = self._select([LANE_A, LANE_B])
        backward, _ = self._select([LANE_B, LANE_A])
        self.assertEqual([l.lane_id for l in forward], [l.lane_id for l in backward])

    def test_a_lone_render_takes_the_preferred_lane(self) -> None:
        # Both lanes are accepted for concurrent use, but the cards are not
        # interchangeable: bus4 carries the resident model (29.45 GB of ~31.8
        # observed under load) while bus9 held 0.36 GB. One render has no reason
        # to choose the nearly-full card.
        candidates, _ = self._select([LANE_A, LANE_B], accepted_lane_count=1)
        self.assertEqual(["b70@bus9"], [lane.lane_id for lane in candidates])

    def test_preference_is_overridable(self) -> None:
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {S.LANE_ORDER_ENV: "b70@bus4,b70@bus9"}):
            candidates, _ = self._select([LANE_A, LANE_B], accepted_lane_count=1)
            self.assertEqual(["b70@bus4"], [lane.lane_id for lane in candidates])

    def test_an_unlisted_lane_sorts_last_but_stably(self) -> None:
        other = lane("b70@bus7", 7, "luid_0x00000000_0x0000aaaa")
        # accepted_lane_count=3 so the capacity cap does not truncate the point.
        candidates, _ = self._select([other, LANE_A, LANE_B], accepted_lane_count=3)
        self.assertEqual(["b70@bus9", "b70@bus4", "b70@bus7"],
                         [lane.lane_id for lane in candidates])

    def test_unhealthy_lane_is_rejected_with_a_reason(self) -> None:
        broken = lane("b70@bus4", 4, "luid_a", healthy=False)
        candidates, decision = self._select([broken, LANE_B])
        self.assertEqual(["b70@bus9"], [l.lane_id for l in candidates])
        self.assertIn("unhealthy", decision.rejected["b70@bus4"])

    def test_busy_media_engine_rejects_the_lane(self) -> None:
        from hearth.media.occupancy import MediaOccupancy

        def occupancy(lane):
            busy = lane.lane_id == "b70@bus4"
            return MediaOccupancy(lane.lane_id, busy, True, 90.0 if busy else 0.0,
                                  ("videodecode",), "detail")

        candidates, decision = self._select([LANE_A, LANE_B], occupancy=occupancy)
        self.assertEqual(["b70@bus9"], [l.lane_id for l in candidates])
        self.assertIn("media_busy", decision.rejected["b70@bus4"])

    def test_unknown_occupancy_is_treated_as_busy(self) -> None:
        from hearth.media.occupancy import MediaOccupancy

        def occupancy(lane):
            return MediaOccupancy(lane.lane_id, True, False, 0.0, (), "counters down")

        candidates, decision = self._select([LANE_A, LANE_B], occupancy=occupancy)
        self.assertEqual([], candidates)
        self.assertIn("unknown", decision.rejected["b70@bus4"])

    def test_spill_guard_rejects_the_lane(self) -> None:
        candidates, decision = self._select(
            [LANE_A, LANE_B],
            spill=lambda lane: (lane.lane_id == "b70@bus9", "shared usage high"),
        )
        self.assertEqual(["b70@bus4"], [l.lane_id for l in candidates])
        self.assertIn("spill", decision.rejected["b70@bus9"])

    def test_already_leased_lane_is_rejected(self) -> None:
        held = self.leases.acquire(scope=S.lane_scope("b70@bus4"), job_id="other",
                                   invocation_id="inv", limit=1, ttl_seconds=60)
        try:
            candidates, decision = self._select([LANE_A, LANE_B])
            self.assertEqual(["b70@bus9"], [l.lane_id for l in candidates])
            self.assertIn("leased", decision.rejected["b70@bus4"])
        finally:
            self.leases.release(held)

    def test_accepted_lane_count_caps_capacity(self) -> None:
        # Production capability is the last accepted benchmark, not the hardware.
        candidates, decision = self._select([LANE_A, LANE_B], accepted_lane_count=1)
        self.assertEqual(["b70@bus9"], [l.lane_id for l in candidates])
        self.assertIn("accepted_lane_count", decision.rejected["b70@bus4"])

    def test_zero_accepted_capacity_yields_no_candidates(self) -> None:
        candidates, _ = self._select([LANE_A, LANE_B], accepted_lane_count=0)
        self.assertEqual([], candidates)

    def test_decision_is_recorded_for_the_receipt(self) -> None:
        candidates, decision = self._select(
            [LANE_A, LANE_B], accepted_lane_count=1,
            gate=lambda lane: (False, ""),
        )
        payload = decision.to_dict()
        self.assertIn("rejected", payload)
        self.assertIn("b70@bus4", payload["rejected"])


class AcceptedCapacityTest(unittest.TestCase):
    def test_missing_record_falls_back_to_one_lane_not_two(self) -> None:
        from hearth.media import acceptance as A

        with tempfile.TemporaryDirectory() as temporary:
            record = A.load_acceptance(Path(temporary) / "absent.json")
            self.assertEqual(1, record.accepted_lane_count)
            self.assertTrue(record.stale)

    def test_malformed_record_falls_back_to_one_lane(self) -> None:
        from hearth.media import acceptance as A

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(1, A.load_acceptance(path).accepted_lane_count)

    def test_round_trips_and_obeys_the_stored_count(self) -> None:
        from hearth.media import acceptance as A

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            A.save_acceptance(
                A.Acceptance(2, {"driver_version": "101.8974"}, "t", False,
                             {"two_lane": "art_1"}), path)
            loaded = A.load_acceptance(path)
            self.assertEqual(2, loaded.accepted_lane_count)
            self.assertFalse(loaded.stale)

    def test_fingerprint_change_marks_stale_but_never_raises_capacity(self) -> None:
        # A driver update makes two lanes eligible for RE-BENCHMARK. It does not
        # grant them. Drifting upward on an update is the failure this prevents.
        from hearth.media import acceptance as A

        record = A.Acceptance(1, {"driver_version": "101.8974", "adapter_uuids": ["a"],
                                  "ffmpeg_version": "8.1.2"}, "t", False, {})
        reconciled = A.reconcile(record, {"driver_version": "102.0",
                                          "adapter_uuids": ["a"],
                                          "ffmpeg_version": "8.1.2"})
        self.assertTrue(reconciled.stale)
        self.assertEqual(1, reconciled.accepted_lane_count)

    def test_unchanged_fingerprint_stays_accepted(self) -> None:
        from hearth.media import acceptance as A

        fingerprint = {"driver_version": "101.8974", "adapter_uuids": ["a"],
                       "ffmpeg_version": "8.1.2"}
        record = A.Acceptance(2, fingerprint, "t", False, {})
        self.assertFalse(A.reconcile(record, dict(fingerprint)).stale)


if __name__ == "__main__":
    unittest.main()
