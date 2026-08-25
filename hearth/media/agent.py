"""The interactive-session render agent.

    GATEWAY  owns authority, admission, job lifecycle, and the ledger.
    AGENT    owns GPU process execution, and nothing else.

This process exists for exactly one reason: the HEARTH gateway runs in Windows
**session 0**, which has no GPU adapter access, and QSV renders need a real
interactive session. It is deliberately narrow:

* it owns no MCP door and opens no network listener;
* it understands only queued ``media.render`` work;
* it uses the existing cross-process ``CapacityLeaseStore`` for lane capacity;
* it NEVER writes the execution ledger -- that would corrupt it (measured:
  concurrent multi-process appends produced a duplicate sequence number and a
  wedged second writer). It hands terminal outcomes back through file sidecars
  and the gateway performs every state transition;
* it disappears safely: an unfinished job returns to the queue rather than being
  failed, because "the executor went away" is not "the render is impossible".

Run it in the interactive session::

    python -m hearth.media.agent

It is intended to be folded into the OMEN dispatcher rather than run as a second
persistent process -- the dispatcher already has to exist in this session for the
BF6 sidecar bridge. The modules stay separable even when they share a process.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from typing import Optional

from hearth.execution.coordination import CapacityLeaseStore
from hearth.media import gate as gate_mod
from hearth.media import handoff, lanes as lanes_mod, render as render_mod
from hearth.media import scheduler as scheduler_mod
from hearth.media.acceptance import load_acceptance
from hearth.media.jobspec import RenderArgumentError, parse_render_arguments
from hearth.media.occupancy import media_occupancy, spilling

POLL_SECONDS = 3.0
HEARTBEAT_SECONDS = 15.0


def _discover(name: str, env_var: str) -> str:
    configured = os.environ.get(env_var)
    if configured:
        return configured
    import shutil

    return shutil.which(name) or name


class RenderAgent:
    """Claims queued render jobs and drives them through the B70 lanes."""

    def __init__(
        self,
        *,
        ffmpeg: Optional[str] = None,
        ffprobe: Optional[str] = None,
        poll_seconds: float = POLL_SECONDS,
        gate=None,
        leases: Optional[CapacityLeaseStore] = None,
    ) -> None:
        self._ffmpeg = ffmpeg or _discover("ffmpeg", "HEARTH_FFMPEG")
        self._ffprobe = ffprobe or _discover("ffprobe", "HEARTH_FFPROBE")
        self._poll = poll_seconds
        # The gate withholds the lane OBS/BF6 is contending for, not the whole
        # renderer -- the other B70 keeps working while you play.
        self._gate = gate if gate is not None else gate_mod.make_gate(self.lanes)
        self._leases = leases or CapacityLeaseStore()
        self._lock = threading.RLock()
        self._pending: dict = {}
        # The scheduler owns lane selection, leasing, and the worker pool. The
        # agent previously rendered inline and returned after ONE job, which
        # made two-lane concurrency impossible through the real path however
        # many lanes were accepted.
        self._scheduler = scheduler_mod.RenderScheduler(
            runner=self._run_job,
            leases=self._leases,
            lanes_provider=self.lanes,
            gate=self._gate,
            occupancy=media_occupancy,
            spill=spilling,
            acceptance_provider=load_acceptance,
            on_error=self._job_error,
            autostart=False,
        )
        self._stop = False
        self._capable: Optional[bool] = None
        self._capability_detail = "not probed"
        self._last_beat = 0.0

    # ------------------------------------------------------------ capability

    def lanes(self) -> list:
        calibration = lanes_mod.load_calibration()
        if calibration is None:
            self._capability_detail = "no lane calibration found"
            return []
        healthy = calibration.healthy_lanes()
        if not healthy:
            self._capability_detail = "no healthy lanes in the calibration"
            return []
        if self._capable is None:
            ok, detail = lanes_mod.can_create_d3d_device(
                self._ffmpeg, healthy[0].child_device
            )
            self._capable, self._capability_detail = ok, detail
        return healthy if self._capable else []

    def beat(self) -> None:
        available = self.lanes()
        handoff.beat(
            capable=bool(available),
            detail=self._capability_detail,
            lanes=[lane.lane_id for lane in available],
        )
        self._last_beat = time.time()

    # -------------------------------------------------------------- recovery

    def recover_orphans(self) -> int:
        """Return jobs whose executor died back to the queue.

        A claim with no live process behind it means the agent (or the machine)
        went away mid-render. That is not a failed render -- nothing is wrong
        with the job -- so it is requeued rather than reported as an error.
        """
        recovered = 0
        # A ".claiming" file is a job taken out of the queue whose runner never
        # started -- the window between claim_job() and publish_claim(). It has
        # no claim record, so the loop below cannot see it.
        for staged in handoff.claims_dir().glob("*.claiming"):
            try:
                if time.time() - staged.stat().st_mtime < handoff.CLAIM_ORPHAN_S:
                    continue
                record = handoff._read(staged) or {}
                staged.unlink()
            except OSError:
                continue
            job_id = record.get("job_id") or staged.stem
            handoff.requeue(job_id, record or {
                "schema_version": handoff.SCHEMA_VERSION, "job_id": job_id,
                "arguments": {}, "queued_at": time.time()})
            recovered += 1
        for path in handoff.list_claims():
            record = handoff._read(path)
            if record is None:
                continue
            job_id = record.get("job_id") or path.stem
            if handoff.read_result(job_id) is not None:
                continue  # finished; the gateway just has not ingested it yet
            started = record.get("started_at") or 0
            if time.time() - float(started) < handoff.CLAIM_ORPHAN_S:
                continue
            pid = record.get("pid")
            if pid and _process_alive(int(pid)):
                continue  # a live agent still owns it
            render_mod.reap_orphan(job_id)
            handoff.requeue(job_id, {
                "schema_version": handoff.SCHEMA_VERSION,
                "job_id": job_id,
                "arguments": record.get("arguments", {}),
                "deadline_s": record.get("deadline_s"),
                "principal": record.get("principal"),
                "queued_at": time.time(),
            })
            recovered += 1
        return recovered

    # ------------------------------------------------------------- execution

    def claim_available(self) -> int:
        """Claim queued work up to current capacity and hand it to the scheduler.

        Deliberately claims only what there is room for. Claiming the whole
        queue would move every job out of ``queue/`` into a ``.claiming`` state
        this process alone knows about -- a hoard that another agent could not
        take and that recovery would have to reason about. Capacity-bounded
        claiming keeps that window small.
        """
        capacity = load_acceptance().accepted_lane_count
        room = capacity - self._scheduler.active_count() - self._scheduler.queue_depth()
        if room <= 0:
            return 0
        claimed = 0
        for path in handoff.list_queued():
            if claimed >= room:
                break
            record = handoff.claim_job(path)
            if record is None:
                continue
            job_id = record.get("job_id") or path.stem
            with self._lock:
                self._pending[job_id] = record
            self._scheduler.enqueue(job_id)
            claimed += 1
        return claimed

    def _run_job(self, *, job_id, lane, lease_id, scheduling) -> None:
        """The scheduler's runner. It is handed a lane that is already leased.

        The scheduler releases the lease in its own finally block, so this must
        not -- releasing twice would free a lane a later job already holds.
        """
        with self._lock:
            record = self._pending.pop(job_id, None)
        if record is None:
            return
        try:
            spec = parse_render_arguments(record.get("arguments") or {})
        except RenderArgumentError as exc:
            handoff.publish_result(job_id, {}, ok=False,
                                   reason="invalid render job: %s" % exc)
            handoff.clear_claim(job_id)
            return

        handoff.publish_claim(job_id, record, lane_id=lane.lane_id, pid=os.getpid())
        try:
            receipt = render_mod.render_clip(
                spec=spec, lane=lane, job_id=job_id,
                ffmpeg=self._ffmpeg, ffprobe=self._ffprobe,
                scheduling=scheduling, leases=self._leases,
                # Checked between variants. A variant already encoding finishes;
                # this stops the ones that have not started.
                cancelled=lambda: handoff.is_cancelled(job_id),
            )
            handoff.publish_result(job_id, receipt.to_dict(), ok=receipt.ok,
                                   reason=receipt.error)
        except Exception as exc:
            handoff.publish_result(job_id, {}, ok=False,
                                   reason="render agent error: %s" % exc)

    def _job_error(self, job_id, exc) -> None:
        if job_id is None:
            return
        with self._lock:
            self._pending.pop(job_id, None)
        handoff.publish_result(job_id, {}, ok=False,
                               reason="render worker crashed: %s" % exc)

    # ------------------------------------------------------------------ loop

    def stop(self, *_args) -> None:
        self._stop = True

    def run(self) -> None:
        handoff.ensure_dirs()
        self.beat()
        print("render agent: session=%s capable=%s (%s)"
              % (os.environ.get("SESSIONNAME", "?"), bool(self.lanes()),
                 self._capability_detail), flush=True)
        while not self._stop:
            try:
                if time.time() - self._last_beat >= HEARTBEAT_SECONDS:
                    self.beat()
                self.recover_orphans()
                self.claim_available()
                if self._scheduler.pump() == 0 and self._scheduler.active_count() == 0:
                    time.sleep(self._poll)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # never let one bad job kill the agent
                print("render agent error: %s" % exc, file=sys.stderr, flush=True)
                time.sleep(self._poll)
        print("render agent: stopped", flush=True)


def _process_alive(pid: int) -> bool:
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        import subprocess

        proc = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, errors="replace",
        )
        return str(pid) in (proc.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="HEARTH interactive render agent")
    parser.add_argument("--once", action="store_true",
                        help="claim and run at most one job, then exit")
    parser.add_argument("--poll", type=float, default=POLL_SECONDS)
    args = parser.parse_args(argv)

    agent = RenderAgent(poll_seconds=args.poll)
    if args.once:
        handoff.ensure_dirs()
        agent.beat()
        agent.recover_orphans()
        claimed = agent.claim_available()
        agent._scheduler.pump()
        agent._scheduler.wait_idle(3600)
        print("ran: %d job(s)" % claimed, flush=True)
        agent._scheduler.close()
        return 0
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, agent.stop)
        except (ValueError, OSError):
            pass
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
