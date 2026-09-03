"""Fenced art-session ownership and ArcServe handoff."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from hearth.execution.coordination import GpuTenancyStore, TenancyConflict
from hearth.imagegen import agent as agent_control, handoff
from hearth.media import gate as media_gate
from hearth.media import lanes as media_lanes

POOL = "omen-b70-pool"
SESSION_TTL_SECONDS = 180.0
IDLE_RESTORE_SECONDS = 30 * 60.0
DRAIN_TIMEOUT_SECONDS = 15 * 60.0
ARC_SENTINEL = Path(r"C:\work\commandcenter\hearth\var\arc-maintenance.stop")
ARC_RESTART_TASK = "ArcServeRestart"
ARC_SLOTS = "http://127.0.0.1:8082/slots"
ARC_CHAT = "http://127.0.0.1:8082/v1/chat/completions"


class ImageSessionError(RuntimeError):
    pass


class ImageSessionController:
    """Own the machine-wide on/off transition for image generation.

    Public methods return quickly; potentially slow drains and model warm-up run
    on daemon threads. The renewable SQLite fence is authoritative for routing,
    while append-only session events explain every transition after a restart.
    """

    def __init__(
        self, *, store: Optional[GpuTenancyStore] = None,
        agent_status: Callable = handoff.agent_status,
        agent_start: Callable = agent_control.ensure_running,
        gate_probe: Optional[Callable] = None,
        cancel_all: Optional[Callable[[str], None]] = None,
        autostart: bool = True,
        idle_seconds: float = IDLE_RESTORE_SECONDS,
    ) -> None:
        self.store = store or GpuTenancyStore()
        self._agent_status = agent_status
        self._agent_start = agent_start
        self._gate_probe = gate_probe or self._default_gate_probe
        self._cancel_all = cancel_all
        self._idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._transition: Optional[threading.Thread] = None
        self._monitor: Optional[threading.Thread] = None
        self._empty_since: Optional[float] = None
        self._agent_missing_since: Optional[float] = None
        self.events_path = handoff.root() / "session-events.ndjson"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        if autostart:
            existing = self.store.active_image_session(POOL)
            if existing is not None and existing.state != "imagegen":
                self._transition = threading.Thread(
                    target=self._resume_transition, args=(existing,),
                    name="hearth-imagegen-resume", daemon=True,
                )
                self._transition.start()
            self._monitor = threading.Thread(
                target=self._monitor_loop, name="hearth-imagegen-session", daemon=True
            )
            self._monitor.start()

    def _resume_transition(self, snapshot) -> None:
        """Finish a transition whose daemon thread was lost with the gateway."""
        try:
            if snapshot.state in {"draining_llm", "starting_imagegen"}:
                agent = self._agent_status()
                gate = self._gate_probe()
                if not agent.available or getattr(gate, "active", False):
                    self._fault_and_restore(
                        snapshot, "gateway restart made the original start preconditions stale"
                    )
                else:
                    self._start_transition(snapshot)
            elif snapshot.state == "restoring_llm":
                self._restore(snapshot, "resumed ArcServe restore after gateway restart")
            else:
                self._stop_transition(
                    snapshot, False, "resumed image drain after gateway restart"
                )
        except Exception as exc:
            self._fault_and_restore(snapshot, "transition resume failed: %s" % exc)

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=5)

    def _event(self, event: str, snapshot, *, reason: Optional[str] = None) -> None:
        record = {
            "schema": "imagegen.session.v1", "event": event,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "resource": POOL, "session_id": snapshot.session_id,
            "epoch": snapshot.epoch, "owner": snapshot.owner,
            "state": snapshot.state, "reason": reason or snapshot.reason,
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def _default_gate_probe():
        calibration = media_lanes.load_calibration()
        lanes = calibration.lanes if calibration is not None else []
        return media_gate.probe(lanes)

    def status(self) -> dict:
        snapshot = self.store.get(POOL)
        agent = self._agent_status()
        return {
            "ok": True,
            "session": snapshot.to_dict() if snapshot is not None else {
                "resource": POOL, "owner": "arcserve", "state": "llm",
                "active": False, "epoch": 0,
            },
            "agent": agent.to_dict(),
            "queued": len(handoff.list_queued()),
            "running": len(handoff.list_claims()),
            "idle_restore_seconds": self._idle_seconds,
            # The worker and the scheduled recovery both live in a SEPARATE repo at
            # E:\omen\imagegen, reached by hardcoded path. Surface its presence here so a
            # missing runtime is visible at status time rather than as a 90 s launch
            # timeout and a recovery that silently stopped running.
            "runtime": agent_control.runtime_preflight(),
        }

    def start(self, *, reason: str = "operator requested art session") -> dict:
        with self._lock:
            current = self.store.active_image_session(POOL)
            if current is not None:
                return {"ok": True, "already_active": True, **self.status()}
            gate = self._gate_probe()
            if getattr(gate, "active", False):
                return {"ok": False, "error": "BF6 or OBS recording is active",
                        "blocker": gate.to_dict()}
            agent = self._agent_status()
        if not agent.available:
            # OUTSIDE the lock on purpose: _agent_start blocks for up to 90 s waiting for
            # the worker to report ready. Holding the controller lock across it stalled
            # every concurrent stop() and every event write for that whole window.
            agent = self._agent_start()
            if not agent.available:
                return {"ok": False, "error": "interactive image agent unavailable",
                        "agent": agent.to_dict()}
        with self._lock:
            # Re-check under the lock: another caller may have won the race while we were
            # launching the worker.
            if self.store.active_image_session(POOL) is not None:
                return {"ok": True, "already_active": True, **self.status()}
            session_id = "imgsess_" + secrets.token_hex(16)
            try:
                snapshot = self.store.acquire(
                    resource=POOL, session_id=session_id,
                    ttl_seconds=SESSION_TTL_SECONDS, reason=reason,
                )
            except TenancyConflict as exc:
                return {"ok": False, "error": str(exc)}
            self._event("session.requested", snapshot, reason=reason)
            self._transition = threading.Thread(
                target=self._start_transition, args=(snapshot,),
                name="hearth-imagegen-start", daemon=True,
            )
            self._transition.start()
            return {"ok": True, "already_active": False, **self.status()}

    def stop(self, *, force: bool = False, reason: str = "operator requested restore") -> dict:
        with self._lock:
            snapshot = self.store.active_image_session(POOL)
            if snapshot is None:
                return {"ok": True, "already_stopped": True, **self.status()}
            if self._transition is not None and self._transition.is_alive():
                return {"ok": False, "error": "a tenancy transition is already running",
                        **self.status()}
            self._transition = threading.Thread(
                target=self._stop_transition, args=(snapshot, force, reason),
                name="hearth-imagegen-stop", daemon=True,
            )
            self._transition.start()
            return {"ok": True, "already_stopped": False, **self.status()}

    def _transition_state(self, snapshot, state: str, reason: Optional[str] = None):
        changed = self.store.transition(
            resource=POOL, session_id=snapshot.session_id, epoch=snapshot.epoch,
            state=state, ttl_seconds=SESSION_TTL_SECONDS, reason=reason,
        )
        self._event("session.transition", changed, reason=reason)
        return changed

    def _start_transition(self, snapshot) -> None:
        try:
            deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
            idle_samples = 0
            while time.monotonic() < deadline and idle_samples < 2:
                self.store.renew(resource=POOL, session_id=snapshot.session_id,
                                 epoch=snapshot.epoch, ttl_seconds=SESSION_TTL_SECONDS)
                state = self._arc_slots_state()
                if state == "idle":
                    idle_samples += 1
                elif state == "down" and not self._arc_process_running():
                    idle_samples = 2
                else:
                    idle_samples = 0
                if idle_samples < 2:
                    time.sleep(2.5)
            if idle_samples < 2:
                raise ImageSessionError("ArcServe did not drain within 15 minutes")
            snapshot = self._transition_state(snapshot, "starting_imagegen")
            ARC_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
            ARC_SENTINEL.write_text(
                "owned by %s epoch %d\n" % (snapshot.session_id, snapshot.epoch),
                encoding="utf-8",
            )
            self._run_restart_task()
            deadline = time.monotonic() + 140
            while self._arc_process_running() and time.monotonic() < deadline:
                time.sleep(2)
            if self._arc_process_running():
                raise ImageSessionError("llama-server remained alive after guarded stop")
            snapshot = self._transition_state(snapshot, "imagegen")
            self._empty_since = time.monotonic() if handoff.active_count() == 0 else None
        except Exception as exc:
            self._fault_and_restore(snapshot, "start failed: %s" % exc)

    def _stop_transition(self, snapshot, force: bool, reason: str) -> None:
        try:
            snapshot = self._transition_state(snapshot, "draining_imagegen", reason)
            if force:
                if self._cancel_all is not None:
                    self._cancel_all("forced session stop: " + reason)
                else:
                    for path in [*handoff.list_queued(), *handoff.list_claims()]:
                        handoff.request_cancel(path.stem, reason="forced session stop")
            deadline = time.monotonic() + (120 if force else DRAIN_TIMEOUT_SECONDS)
            while handoff.active_count() and time.monotonic() < deadline:
                self.store.renew(resource=POOL, session_id=snapshot.session_id,
                                 epoch=snapshot.epoch, ttl_seconds=SESSION_TTL_SECONDS)
                time.sleep(1)
            if handoff.active_count():
                # A claim only protects ArcServe while a worker could still finish it.
                # Reap the ones nothing is advancing, then insist on a genuinely empty
                # queue before restoring.
                self._reap_abandoned_claims(snapshot)
                if handoff.active_count():
                    raise ImageSessionError(
                        "image jobs did not drain; ArcServe remains stopped")
            self._restore(snapshot, reason)
        except Exception as exc:
            try:
                failed = self._transition_state(snapshot, "faulted", str(exc))
                self._event("session.faulted", failed, reason=str(exc))
            except TenancyConflict:
                pass

    def _restore(self, snapshot, reason: str) -> None:
        snapshot = self._transition_state(snapshot, "restoring_llm", reason)
        ARC_SENTINEL.unlink(missing_ok=True)
        self._run_restart_task()
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            self.store.renew(resource=POOL, session_id=snapshot.session_id,
                             epoch=snapshot.epoch, ttl_seconds=SESSION_TTL_SECONDS)
            if self._verify_arcserve():
                if not self.store.release(resource=POOL, session_id=snapshot.session_id,
                                          epoch=snapshot.epoch, reason=reason):
                    raise TenancyConflict("lost fence while releasing image session")
                released = self.store.get(POOL)
                assert released is not None
                self._event("session.restored", released, reason=reason)
                self._empty_since = None
                return
            time.sleep(5)
        raise ImageSessionError("ArcServe failed its warm serviceability probe")

    def _fault_and_restore(self, snapshot, reason: str) -> None:
        try:
            snapshot = self._transition_state(snapshot, "faulted", reason)
            self._event("session.faulted", snapshot, reason=reason)
            if not handoff.list_claims() or self._reap_abandoned_claims(snapshot):
                self._restore(snapshot, reason)
        except Exception:
            pass

    def _reap_abandoned_claims(self, snapshot) -> bool:
        """Clear claims no live worker can finish. True when nothing is left holding on.

        Without this, one stuck claim file left ArcServe stopped indefinitely: the fault
        path refused to restore while claims existed, and the scheduled fx99 recovery
        deferred for the same reason. Two mechanisms, both correct in isolation, that
        together had no exit.
        """
        abandoned = handoff.abandoned_claims()
        if not abandoned:
            return False
        remaining = [path for path in handoff.list_claims() if path not in abandoned]
        if remaining:
            return False
        for path in abandoned:
            self._event("session.claim_abandoned", snapshot,
                        reason="no live worker for claim %s; releasing it so ArcServe can "
                               "be restored" % path.stem)
            handoff.clear_claim(path.stem)
        return True

    # ---------------------------------------------------------------- public surface
    # hearth.imagegen.recovery runs OUT OF PROCESS, from fx99's timer, and used to reach
    # into the privates below. That made a rename in this file able to silently break
    # scheduled recovery with nothing to catch it. These three are the contract; the
    # provider-contract test asserts they stay.

    def restart_arcserve(self) -> None:
        """Bounce ArcServe via the elevated S4U task. Destructive: force-kills llama-server."""
        self._run_restart_task()

    def verify_arcserve(self) -> bool:
        """True only on idle slots PLUS a real completion round-trip -- never a port probe."""
        return self._verify_arcserve()

    def record_event(self, event: str, snapshot, *, reason: Optional[str] = None) -> None:
        """Append one `imagegen.session.v1` record to the session event log."""
        self._event(event, snapshot, reason=reason)

    @staticmethod
    def _run_restart_task() -> None:
        completed = subprocess.run(
            ["schtasks", "/Run", "/TN", ARC_RESTART_TASK],
            capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0:
            raise ImageSessionError("ArcServeRestart could not be started: %s" %
                                    (completed.stderr or completed.stdout).strip())

    @staticmethod
    def _arc_process_running() -> bool:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/NH"],
            capture_output=True, text=True, timeout=15, errors="replace",
        )
        return "llama-server.exe" in (completed.stdout or "").lower()

    @staticmethod
    def _request_json(url: str, *, payload: Optional[dict] = None, timeout: int = 8):
        headers = {"Accept": "application/json"}
        token = os.environ.get("OMEN_ARC_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _arc_slots_state(self) -> str:
        try:
            slots = self._request_json(ARC_SLOTS)
        except (OSError, ValueError, urllib.error.URLError):
            return "down"
        if not isinstance(slots, list) or not slots:
            return "unknown"
        for slot in slots:
            if not isinstance(slot, dict):
                return "unknown"
            if slot.get("is_processing") or slot.get("state") in {"processing", "running"}:
                return "busy"
        return "idle"

    def _verify_arcserve(self) -> bool:
        if self._arc_slots_state() != "idle":
            return False
        payload = {
            "model": "qwen3-30b-a3b",
            "messages": [{"role": "user", "content": "Reply OK."}],
            "temperature": 0, "max_tokens": 2,
        }
        try:
            result = self._request_json(ARC_CHAT, payload=payload, timeout=120)
        except (OSError, ValueError, urllib.error.URLError):
            return False
        return isinstance(result, dict) and bool(result.get("choices"))

    def _monitor_loop(self) -> None:
        while not self._stop.wait(30):
            snapshot = self.store.active_image_session(POOL)
            if snapshot is None:
                self._empty_since = None
                continue
            if not self.store.renew(resource=POOL, session_id=snapshot.session_id,
                                    epoch=snapshot.epoch, ttl_seconds=SESSION_TTL_SECONDS):
                continue
            if snapshot.state != "imagegen":
                continue
            if not self._agent_status().available:
                if handoff.list_claims():
                    self._agent_missing_since = None
                    continue
                if self._agent_missing_since is None:
                    self._agent_missing_since = time.monotonic()
                    continue
                if time.monotonic() - self._agent_missing_since >= handoff.AGENT_STALE_SECONDS:
                    self.stop(force=True, reason="interactive image agent heartbeat lost")
                    self._agent_missing_since = None
                continue
            self._agent_missing_since = None
            if handoff.active_count():
                self._empty_since = None
                continue
            if self._empty_since is None:
                self._empty_since = time.monotonic()
            elif time.monotonic() - self._empty_since >= self._idle_seconds:
                self.stop(reason="imagegen idle timeout (%.0f minutes with no active jobs)"
                                 % (self._idle_seconds / 60.0))
