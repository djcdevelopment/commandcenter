"""The render scheduler: durable queue, deterministic lane choice, no idle threads.

THE INVARIANT THIS EXISTS TO HOLD
---------------------------------
    A render waiting for capacity must not consume a render executor thread.

The LLM path in ExecutionService gets this wrong on purpose-built hardware and
gets away with it: a job that cannot acquire a provider lease sits in ``_run``
and *spins*, holding one of the 16 shared workers until capacity frees up. With
two B70 lanes and a session's worth of clips queued, copying that design would
park every worker on a sleep loop and starve ``llm.chat`` completely.

So the render path separates the three roles the LLM path conflates:

    submit_render      -> validate, ledger `job.queued`, enqueue, RETURN
                          (no worker touched)
    scheduler thread   -> exactly ONE thread. Picks a lane, leases it, and only
                          then hands the job to the executor.
    render executor    -> 2 workers. Every worker is running ffmpeg; none is
                          ever waiting for a lane.

The queue is a projection of ledger ``job.queued`` events, so it survives a
gateway restart without separate persistence.

LANE CHOICE IS A PURE FILTER-AND-SORT
-------------------------------------
Same observable conditions, same decision, every time. Two GPUs do not warrant
anything cleverer, and a scheduler whose choices cannot be replayed cannot be
debugged from a receipt.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from hearth.execution.coordination import CapacityUnavailable
from hearth.media import acceptance as acceptance_mod
from hearth.media import occupancy as occupancy_mod

RENDER_WORKERS = 2

# LANE PREFERENCE -- an explicit policy, not a derived measurement.
#
# Both lanes are accepted for concurrent use (Phase 6: 2 lanes, -1.34%/+0.87%
# inference impact). But the two cards are NOT interchangeable: the resident
# model's `-sm layer -ts 1,1` split does not land evenly, and bus4 was observed
# at 29.45 GB of ~31.8 GB dedicated while bus9 held 0.36 GB. A LONE render has
# no reason to choose the card that is nearly full.
#
# Deliberately a stated policy rather than a live VRAM reading: the dedicated
# counter is not stable enough to key on -- the same two adapters measured
# 29.45/0.36 GB under load and 0.003/0.003 GB minutes later at idle. Sorting on
# that would silently flip preference with the weather. This is overridable via
# HEARTH_RENDER_LANE_ORDER, and should be revisited if the model's split changes.
DEFAULT_LANE_ORDER = ("b70@bus9", "b70@bus4")
LANE_ORDER_ENV = "HEARTH_RENDER_LANE_ORDER"


def preferred_order() -> tuple:
    configured = os.environ.get(LANE_ORDER_ENV)
    if configured:
        return tuple(part.strip() for part in configured.split(",") if part.strip())
    return DEFAULT_LANE_ORDER


def lane_rank(lane) -> tuple:
    """Sort key: declared preference first, then lane_id for a stable tie-break."""
    order = preferred_order()
    lane_id = getattr(lane, "lane_id", "")
    try:
        return (order.index(lane_id), lane_id)
    except ValueError:
        return (len(order), lane_id)
LEASE_TTL_SECONDS = 900.0
TICK_SECONDS = 5.0


def lane_scope(lane_id: str) -> str:
    """Lease scope for one lane. One lane, one render."""
    return "render:%s" % lane_id


@dataclass
class LaneDecision:
    """Why each lane was or was not chosen. Recorded on the receipt."""

    chosen: Optional[str] = None
    rejected: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"chosen": self.chosen, "rejected": dict(self.rejected)}


def select_lane_candidates(
    lanes: Sequence,
    *,
    accepted_lane_count: int,
    leases,
    gate=None,
    occupancy=None,
    spill=None,
) -> tuple:
    """Ordered, deterministic list of lanes that may take work right now.

    Filter order is fixed so the reason a lane was skipped is unambiguous:

      1. unhealthy (calibration fingerprint no longer matches)
      2. withheld by the gaming/OBS gate
      3. failing the shared-memory spill guard
      4. media engines already busy
      5. already leased
      6. beyond the accepted lane count

    Returns ``(ordered_lanes, LaneDecision)``.
    """
    decision = LaneDecision()
    # Declared preference first (see DEFAULT_LANE_ORDER), lane_id as tie-break.
    ordered = sorted(lanes, key=lane_rank)
    candidates = []

    for lane in ordered:
        if not getattr(lane, "healthy", False):
            decision.rejected[lane.lane_id] = "unhealthy:not_calibrated"
            continue
        if gate is not None:
            withheld, reason = gate(lane)
            if withheld:
                decision.rejected[lane.lane_id] = "withheld:%s" % reason
                continue
        if spill is not None:
            is_spilling, reason = spill(lane)
            if is_spilling:
                decision.rejected[lane.lane_id] = "spill:%s" % reason
                continue
        if occupancy is not None:
            state = occupancy(lane)
            if state.busy:
                decision.rejected[lane.lane_id] = (
                    "media_busy:%s" % ("unknown" if not state.known else state.detail)
                )
                continue
        if leases.active_count(lane_scope(lane.lane_id)) >= 1:
            decision.rejected[lane.lane_id] = "leased:in_use"
            continue
        candidates.append(lane)

    # The accepted benchmark is a CAP applied last, so the receipt still shows
    # why the other lanes were individually eligible or not.
    if accepted_lane_count is not None and len(candidates) > accepted_lane_count:
        for lane in candidates[accepted_lane_count:]:
            decision.rejected[lane.lane_id] = (
                "capacity:beyond accepted_lane_count=%d" % accepted_lane_count
            )
        candidates = candidates[:accepted_lane_count]

    return candidates, decision


class RenderScheduler:
    """Owns the render queue, the lane leases, and the 2-worker executor."""

    def __init__(
        self,
        *,
        runner: Callable,
        leases,
        lanes_provider: Callable,
        gate: Optional[Callable] = None,
        occupancy: Optional[Callable] = None,
        spill: Optional[Callable] = None,
        acceptance_provider: Optional[Callable] = None,
        workers: int = RENDER_WORKERS,
        lease_ttl_seconds: float = LEASE_TTL_SECONDS,
        tick_seconds: float = TICK_SECONDS,
        on_error: Optional[Callable] = None,
        autostart: bool = True,
    ) -> None:
        self._runner = runner
        self._leases = leases
        self._lanes_provider = lanes_provider
        self._gate = gate
        self._occupancy = occupancy
        self._spill = spill
        self._acceptance_provider = acceptance_provider or acceptance_mod.load_acceptance
        self._lease_ttl = lease_ttl_seconds
        self._tick = tick_seconds
        self._on_error = on_error

        self._queue: list = []
        self._active: dict = {}
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="hearth-render"
        )
        self._thread: Optional[threading.Thread] = None
        self._idle = threading.Event()
        self._idle.set()
        if autostart:
            self.start()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hearth-render-scheduler", daemon=True
        )
        self._thread.start()

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        with self._wake:
            self._wake.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    # ----------------------------------------------------------------- queue

    def enqueue(self, job_id: str) -> None:
        """Accept a job into durable queued state. Consumes no worker."""
        with self._wake:
            if job_id in self._queue or job_id in self._active:
                return
            self._queue.append(job_id)
            self._idle.clear()
            self._wake.notify_all()

    def dequeue(self, job_id: str) -> bool:
        """Remove a job that has not started yet. Returns whether it was queued.

        This is the ENFORCEABLE half of cancellation. A job still in the queue
        can be stopped absolutely -- it has no lane, no lease and no ffmpeg. A
        job already dispatched cannot be, so the caller is told which happened
        rather than being allowed to assume the strong case (see cancel_render).
        """
        with self._wake:
            if job_id not in self._queue:
                return False
            self._queue.remove(job_id)
            if not self._queue and not self._active:
                self._idle.set()
            self._wake.notify_all()
            return True

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def active_count(self) -> int:
        """Render executor workers currently occupied. Never counts waiters."""
        with self._lock:
            return len(self._active)

    def active_lanes(self) -> dict:
        with self._lock:
            return {job: lane.lane_id for job, (lane, _) in self._active.items()}

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """Block until the queue is empty and nothing is running (tests)."""
        return self._idle.wait(timeout)

    # ------------------------------------------------------------- scheduling

    def pump(self) -> int:
        """Dispatch as many queued jobs as there are eligible lanes.

        Split out from the loop so tests can drive scheduling deterministically
        instead of racing a background thread.
        """
        dispatched = 0
        while True:
            with self._lock:
                if not self._queue:
                    break
                lanes = list(self._lanes_provider() or ())
                accepted = self._acceptance_provider().accepted_lane_count
                candidates, decision = select_lane_candidates(
                    lanes,
                    accepted_lane_count=accepted,
                    leases=self._leases,
                    gate=self._gate,
                    occupancy=self._occupancy,
                    spill=self._spill,
                )
                if not candidates:
                    break
                lane = candidates[0]
                job_id = self._queue[0]
                try:
                    lease_id = self._leases.acquire(
                        scope=lane_scope(lane.lane_id),
                        job_id=job_id,
                        invocation_id="render-%s" % job_id,
                        limit=1,
                        ttl_seconds=self._lease_ttl,
                    )
                except CapacityUnavailable:
                    # Lost a race with another gateway process; try again on the
                    # next pass rather than dropping the job.
                    break
                self._queue.pop(0)
                self._active[job_id] = (lane, lease_id)
            # Outside the lock: the worker is only handed work it can start.
            self._executor.submit(self._execute, job_id, lane, lease_id, decision)
            dispatched += 1
        return dispatched

    def _execute(self, job_id, lane, lease_id, decision) -> None:
        try:
            self._runner(job_id=job_id, lane=lane, lease_id=lease_id,
                         scheduling=decision.to_dict())
        except Exception as exc:  # a runner failure must not kill the lane
            if self._on_error is not None:
                try:
                    self._on_error(job_id, exc)
                except Exception:
                    pass
        finally:
            try:
                self._leases.release(lease_id)
            finally:
                with self._wake:
                    self._active.pop(job_id, None)
                    if not self._queue and not self._active:
                        self._idle.set()
                    self._wake.notify_all()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.pump()
            except Exception as exc:
                if self._on_error is not None:
                    try:
                        self._on_error(None, exc)
                    except Exception:
                        pass
            with self._wake:
                if not self._queue and not self._active:
                    self._idle.set()
                # A tick still fires with work queued: a lane may free up
                # because a DIFFERENT process released it, or because the gate
                # re-opened, and neither of those notifies us.
                self._wake.wait(self._tick)
