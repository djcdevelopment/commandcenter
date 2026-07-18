"""HEARTH kernel timers (ADR-0015 slice 1): the gateway becomes the scheduler
for the repeating ops loops, replacing Windows Task Scheduler as the trigger.

Each timer fires a subprocess of the SAME venv interpreter (``sys.executable``)
running exactly the module invocation the retired .cmd wrapper used to run,
appends the same ``... tick starting`` / ``... exited with <rc>`` log framing
to the same log file under ``hearth/var/``, and never touches the loop module
itself (fleet/mechnet_watchdog.py, fleet/bankedfire_drain.py are unmodified —
this is a trigger swap, not a behavior change).

Design:
- One daemon thread per timer, sleeping an initial (staggered) offset before
  its first tick, then firing on a fixed ``interval_s`` cadence.
- Scheduling is decoupled from tick completion: launching a subprocess and
  waiting on it (with a timeout) happens on a short-lived per-tick monitor
  thread, so a slow tick never delays the next scheduled firing. That is what
  makes skip-if-running meaningful: if the previous tick's monitor/subprocess
  is still alive when the next interval elapses, the new firing is skipped
  (logged) rather than started concurrently — a timer never overlaps itself.
- A tick that fails (nonzero exit, timeout kill, or a spawn exception) is
  logged and never raises out of a thread or stops future ticks (visibility +
  resilience, per repo CLAUDE.md's build values).
- Threads are daemon=True: gateway process shutdown is never blocked on them.

Public API: ``TIMERS`` (the registry, importable for inspection) and
``start_timers(enabled) -> list[TimerHandle]``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
VAR_DIR = REPO_ROOT / "hearth" / "var"

MAX_TIMEOUT_S = 600


def _ts() -> str:
    """Timestamp matching the .cmd wrappers' bracketed log framing."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class TimerSpec:
    """One repeating ops loop: name, cadence, the argv it runs, and where its
    tick log lives. ``argv_builder`` is a callable (not a fixed list) so tests
    can substitute a stub without touching the real interpreter/module path."""

    name: str
    interval_s: float
    argv_builder: Callable[[], list[str]]
    log_path: Path
    stagger_s: float = 0.0
    timeout_s: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            object.__setattr__(self, "timeout_s", min(self.interval_s, MAX_TIMEOUT_S))


def _stagger_for(index: int) -> float:
    """Name-indexed initial-fire offset (15-60s) so a gateway boot doesn't
    fire every timer at once."""
    return 15.0 + (index % 4) * 15.0


TIMERS: list[TimerSpec] = [
    TimerSpec(
        name="patrol",
        interval_s=300.0,
        argv_builder=lambda: [sys.executable, "-m", "fleet.mechnet_watchdog",
                              "--patrol-only", "--json"],
        log_path=VAR_DIR / "watchdog-patrol-task.log",
        stagger_s=_stagger_for(0),
    ),
    TimerSpec(
        name="watchdog",
        interval_s=900.0,
        argv_builder=lambda: [sys.executable, "-m", "fleet.mechnet_watchdog", "--json"],
        log_path=VAR_DIR / "watchdog-task.log",
        stagger_s=_stagger_for(1),
    ),
    TimerSpec(
        name="drain",
        interval_s=1800.0,
        argv_builder=lambda: [sys.executable, "-m", "fleet.bankedfire_drain", "--json"],
        log_path=VAR_DIR / "bankedfire-drain-task.log",
        stagger_s=_stagger_for(2),
    ),
    TimerSpec(
        name="ollama-sentinel",
        interval_s=120.0,
        argv_builder=lambda: [sys.executable, "-m", "fleet.ollama_sentinel",
                              "--json", "--exclude-pid", str(os.getpid())],
        log_path=VAR_DIR / "ollama-sentinel-task.log",
        stagger_s=_stagger_for(3),
    ),
    TimerSpec(
        name="knowledge_rebuild",
        interval_s=21600.0,
        argv_builder=lambda: [sys.executable, "-m", "hearth.projection.rebuild"],
        log_path=VAR_DIR / "knowledge-rebuild-task.log",
        stagger_s=_stagger_for(4),
    ),
    TimerSpec(
        name="fleet_harvest",
        interval_s=1800.0,
        argv_builder=lambda: [sys.executable, "-m", "hearth.toolsurface.fleet_harvest",
                              "--sweep", "--json"],
        log_path=VAR_DIR / "fleet-harvest-task.log",
        stagger_s=_stagger_for(5),
    ),
]


class TimerHandle:
    """A running (or stopped) timer thread plus its stop flag, for tests and
    for the gateway to hold a reference to (keeps the thread reachable)."""

    def __init__(self, spec: TimerSpec, cwd: Path, stop_event: threading.Event,
                stagger_s: Optional[float] = None) -> None:
        self.spec = spec
        self.cwd = cwd
        self._stop_event = stop_event
        self._proc_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self.tick_count = 0
        self.skip_count = 0
        self.fail_count = 0
        self._stagger_s = spec.stagger_s if stagger_s is None else stagger_s
        self.thread = threading.Thread(
            target=self._run, name=f"hearth-timer-{spec.name}", daemon=True,
        )

    def start(self) -> "TimerHandle":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()

    def _log(self, line: str) -> None:
        with self._log_lock:
            self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.spec.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        print(f"[hearth-timer] {self.spec.name}: {line}")

    def _is_previous_still_running(self) -> bool:
        with self._proc_lock:
            proc = self._proc
            monitor = self._monitor_thread
        if proc is not None and proc.poll() is None:
            return True
        return monitor is not None and monitor.is_alive()

    def _monitor(self, proc: subprocess.Popen, out_fh) -> None:
        """Runs on its own thread: wait for the subprocess (with timeout),
        kill-on-timeout, close the shared log handle, then log the outcome.
        Never raises — a tick failure must never take down the timer thread."""
        try:
            try:
                rc: Optional[int] = proc.wait(timeout=self.spec.timeout_s)
                timed_out = False
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                rc, timed_out = None, True
        except Exception as exc:  # defensive: unexpected wait()/kill() failure
            try:
                out_fh.close()
            except Exception:
                pass
            self.fail_count += 1
            self._log(f"[{_ts()}] {self.spec.name} exited with monitor error: {exc}")
            return

        try:
            out_fh.close()
        except Exception:
            pass

        if timed_out:
            self.fail_count += 1
            self._log(f"[{_ts()}] {self.spec.name} exited with timeout "
                      f"(killed after {self.spec.timeout_s}s)")
            return

        self.tick_count += 1
        if rc != 0:
            self.fail_count += 1
        self._log(f"[{_ts()}] {self.spec.name} exited with {rc}")

    def _launch_tick(self) -> None:
        self._log(f"[{_ts()}] {self.spec.name} tick starting")
        try:
            argv = self.spec.argv_builder()
        except Exception as exc:
            self.fail_count += 1
            self._log(f"[{_ts()}] {self.spec.name} exited with spawn error: {exc}")
            return

        try:
            out_fh = open(self.spec.log_path, "a", encoding="utf-8")
        except Exception as exc:
            self.fail_count += 1
            self._log(f"[{_ts()}] {self.spec.name} exited with spawn error: {exc}")
            return

        try:
            proc = subprocess.Popen(argv, cwd=str(self.cwd), stdout=out_fh,
                                    stderr=subprocess.STDOUT)
        except Exception as exc:
            out_fh.close()
            self.fail_count += 1
            self._log(f"[{_ts()}] {self.spec.name} exited with spawn error: {exc}")
            return

        monitor_thread = threading.Thread(
            target=self._monitor, args=(proc, out_fh),
            name=f"hearth-timer-{self.spec.name}-tick", daemon=True,
        )
        with self._proc_lock:
            self._proc = proc
            self._monitor_thread = monitor_thread
        monitor_thread.start()

    def _run(self) -> None:
        if self._stop_event.wait(self._stagger_s):
            return
        while not self._stop_event.is_set():
            if self._is_previous_still_running():
                self.skip_count += 1
                self._log(f"[{_ts()}] {self.spec.name} tick skipped (previous still running)")
            else:
                try:
                    self._launch_tick()
                except Exception as exc:  # never let a tick kill the schedule thread
                    self.fail_count += 1
                    self._log(f"[{_ts()}] {self.spec.name} exited with unexpected error: {exc}")
            if self._stop_event.wait(self.spec.interval_s):
                return


def start_timers(enabled: bool, timers: Optional[list[TimerSpec]] = None,
                 cwd: Optional[Path] = None) -> list[TimerHandle]:
    """Start one daemon thread per registered timer spec. Returns the started
    handles (empty list if disabled). ``timers``/``cwd`` are injectable for
    tests; production callers (the gateway) use the defaults."""
    if not enabled:
        return []
    specs = TIMERS if timers is None else timers
    repo_root = REPO_ROOT if cwd is None else cwd
    handles: list[TimerHandle] = []
    for spec in specs:
        handle = TimerHandle(spec, cwd=repo_root, stop_event=threading.Event())
        handle.start()
        handles.append(handle)
    return handles
