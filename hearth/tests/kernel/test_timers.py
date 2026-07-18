"""hearth.kernel.timers (ADR-0015 slice 1): fast unit tests over the
threading-based periodic runner. Stubs the subprocess layer entirely — no
real subprocesses, network, or the real log dir. Tiny intervals (fractions
of a second) keep the suite fast."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.kernel import timers as timers_mod
from hearth.kernel.timers import TimerHandle, TimerSpec, start_timers


class _FakeProc:
    """Stand-in for subprocess.Popen: controllable poll()/wait() so tests can
    simulate a still-running process, a normal exit, or a timeout."""

    def __init__(self, returncode=0, wait_event: threading.Event = None,
                hang: bool = False):
        self._returncode = returncode
        self._wait_event = wait_event or threading.Event()
        if not hang:
            self._wait_event.set()
        self._killed = False
        self.returncode = None

    def poll(self):
        if self._wait_event.is_set():
            self.returncode = self._returncode
            return self._returncode
        return None

    def wait(self, timeout=None):
        if self._wait_event.wait(timeout=timeout):
            self.returncode = self._returncode
            return self._returncode
        raise subprocess.TimeoutExpired(cmd="stub", timeout=timeout)

    def kill(self):
        self._killed = True
        self._wait_event.set()


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _stop_and_join(handle: TimerHandle, timeout: float = 1.0) -> None:
    """Stop a timer and join its schedule + monitor threads so any open log
    handle is closed before the test's TemporaryDirectory is torn down."""
    handle.stop()
    handle.thread.join(timeout=timeout)
    monitor = handle._monitor_thread
    if monitor is not None:
        monitor.join(timeout=timeout)


class TimerTickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log_path = self.root / "fake-task.log"

    def _spec(self, **overrides) -> TimerSpec:
        kwargs = dict(
            name="fake",
            interval_s=0.05,
            argv_builder=lambda: ["fake", "argv"],
            log_path=self.log_path,
            stagger_s=0.0,
            timeout_s=0.5,
        )
        kwargs.update(overrides)
        return TimerSpec(**kwargs)

    def test_fires_repeatedly_at_interval(self):
        with patch.object(timers_mod.subprocess, "Popen",
                          return_value=_FakeProc(returncode=0)):
            handle = TimerHandle(self._spec(), cwd=self.root, stop_event=threading.Event())
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.tick_count >= 3, timeout=2.0))
            _stop_and_join(handle)
        self.assertGreaterEqual(handle.tick_count, 3)
        self.assertEqual(handle.fail_count, 0)

    def test_disabled_flag_starts_nothing(self):
        handles = start_timers(False)
        self.assertEqual(handles, [])

    def test_log_lines_have_expected_framing(self):
        with patch.object(timers_mod.subprocess, "Popen",
                          return_value=_FakeProc(returncode=0)):
            handle = TimerHandle(self._spec(), cwd=self.root, stop_event=threading.Event())
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.tick_count >= 1))
            _stop_and_join(handle)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("fake tick starting", content)
        self.assertIn("fake exited with 0", content)

    def test_skip_when_previous_still_running(self):
        hang_event = threading.Event()
        proc = _FakeProc(returncode=0, wait_event=hang_event, hang=True)
        with patch.object(timers_mod.subprocess, "Popen", return_value=proc):
            handle = TimerHandle(self._spec(interval_s=0.05, timeout_s=5.0),
                                 cwd=self.root, stop_event=threading.Event())
            handle.start()
            # First tick starts and hangs (still running); wait for a skip.
            self.assertTrue(_wait_until(lambda: handle.skip_count >= 1, timeout=2.0))
            tick_count_while_hung = handle.tick_count
            handle.stop()
            hang_event.set()  # release the hung wait() inside the thread
            _stop_and_join(handle)  # let it close its log handle before teardown
        self.assertEqual(tick_count_while_hung, 0)
        self.assertGreaterEqual(handle.skip_count, 1)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("tick skipped (previous still running)", content)

    def test_timeout_kills_and_logs(self):
        hang_event = threading.Event()
        proc = _FakeProc(returncode=0, wait_event=hang_event, hang=True)
        with patch.object(timers_mod.subprocess, "Popen", return_value=proc):
            handle = TimerHandle(self._spec(interval_s=5.0, timeout_s=0.05),
                                 cwd=self.root, stop_event=threading.Event())
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.fail_count >= 1, timeout=2.0))
            _stop_and_join(handle)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("exited with timeout", content)
        self.assertTrue(proc._killed)

    def test_failing_tick_does_not_stop_subsequent_ticks(self):
        calls = {"n": 0}

        def popen_side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("spawn failed")
            return _FakeProc(returncode=0)

        with patch.object(timers_mod.subprocess, "Popen", side_effect=popen_side_effect):
            handle = TimerHandle(self._spec(interval_s=0.05), cwd=self.root,
                                 stop_event=threading.Event())
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.tick_count >= 1, timeout=2.0))
            _stop_and_join(handle)
        self.assertGreaterEqual(handle.fail_count, 1)
        self.assertGreaterEqual(handle.tick_count, 1)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("spawn error", content)

    def test_nonzero_return_code_counts_as_failure_but_keeps_ticking(self):
        with patch.object(timers_mod.subprocess, "Popen",
                          return_value=_FakeProc(returncode=1)):
            handle = TimerHandle(self._spec(interval_s=0.05), cwd=self.root,
                                 stop_event=threading.Event())
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.tick_count >= 2, timeout=2.0))
            _stop_and_join(handle)
        self.assertGreaterEqual(handle.fail_count, 2)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("exited with 1", content)

    def test_stagger_delays_first_fire(self):
        with patch.object(timers_mod.subprocess, "Popen",
                          return_value=_FakeProc(returncode=0)):
            handle = TimerHandle(self._spec(interval_s=1.0, stagger_s=0.2),
                                 cwd=self.root, stop_event=threading.Event())
            start = time.monotonic()
            handle.start()
            self.assertTrue(_wait_until(lambda: handle.tick_count >= 1, timeout=2.0))
            elapsed = time.monotonic() - start
            _stop_and_join(handle)
        self.assertGreaterEqual(elapsed, 0.18)  # small slack for scheduling jitter


class StartTimersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_start_timers_enabled_starts_one_thread_per_spec(self):
        specs = [
            TimerSpec(name="a", interval_s=10.0, argv_builder=lambda: ["a"],
                      log_path=self.root / "a.log", stagger_s=0.0, timeout_s=1.0),
            TimerSpec(name="b", interval_s=10.0, argv_builder=lambda: ["b"],
                      log_path=self.root / "b.log", stagger_s=0.0, timeout_s=1.0),
        ]
        hang_event = threading.Event()
        with patch.object(timers_mod.subprocess, "Popen",
                          return_value=_FakeProc(returncode=0, wait_event=hang_event,
                                                 hang=True)):
            handles = start_timers(True, timers=specs, cwd=self.root)
            try:
                self.assertEqual(len(handles), 2)
                self.assertTrue(all(h.thread.is_alive() for h in handles))
                self.assertEqual({h.spec.name for h in handles}, {"a", "b"})
            finally:
                for h in handles:
                    h.stop()
                hang_event.set()  # release any in-flight monitor threads promptly
                for h in handles:
                    _stop_and_join(h)

    def test_registry_argv_matches_adr_contract(self):
        names = {t.name: t for t in timers_mod.TIMERS}
        self.assertEqual(set(names), {"patrol", "watchdog", "drain", "ollama-sentinel",
                                      "knowledge_rebuild", "fleet_harvest"})
        self.assertEqual(names["patrol"].interval_s, 300.0)
        self.assertEqual(names["watchdog"].interval_s, 900.0)
        self.assertEqual(names["drain"].interval_s, 1800.0)
        self.assertEqual(names["ollama-sentinel"].interval_s, 120.0)
        self.assertEqual(names["knowledge_rebuild"].interval_s, 21600.0)
        self.assertEqual(names["fleet_harvest"].interval_s, 1800.0)
        self.assertEqual(names["patrol"].argv_builder()[1:],
                         ["-m", "fleet.mechnet_watchdog", "--patrol-only", "--json"])
        self.assertEqual(names["watchdog"].argv_builder()[1:],
                         ["-m", "fleet.mechnet_watchdog", "--json"])
        self.assertEqual(names["drain"].argv_builder()[1:],
                         ["-m", "fleet.bankedfire_drain", "--json"])
        self.assertEqual(names["knowledge_rebuild"].argv_builder()[1:],
                         ["-m", "hearth.projection.rebuild"])
        self.assertEqual(names["fleet_harvest"].argv_builder()[1:],
                         ["-m", "hearth.toolsurface.fleet_harvest", "--sweep", "--json"])
        self.assertEqual(names["fleet_harvest"].log_path.name, "fleet-harvest-task.log")
        # The sentinel's exclude-pid is the gateway's own pid, resolved at
        # tick time — assert the shape, then the dynamic tail.
        sentinel_argv = names["ollama-sentinel"].argv_builder()
        self.assertEqual(sentinel_argv[1:5],
                         ["-m", "fleet.ollama_sentinel", "--json", "--exclude-pid"])
        self.assertEqual(sentinel_argv[5], str(os.getpid()))


if __name__ == "__main__":
    unittest.main()
