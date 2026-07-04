from __future__ import annotations

import os
import subprocess
from unittest import TestCase

from hearth.toolsurface.occupancy import (
    Lease,
    OccupancyCache,
    acquire_lease,
    check_occupancy,
    probe_render_owners,
    resolve_for_lane,
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


_BUSY_OUTPUT = (
    "--- /dev/dri/renderD128\n"
    "                     USER        PID ACCESS COMMAND\n"
    "/dev/dri/renderD128: derek     2040581 F...m llama-server\n"
    "--- /dev/dri/renderD129\n"
)

_FREE_OUTPUT = "--- /dev/dri/renderD128\n--- /dev/dri/renderD129\n"


class ProbeRenderOwnersTests(TestCase):
    def test_busy_when_llama_holds_render_node(self) -> None:
        def runner(*a, **kw):
            return _completed(stdout=_BUSY_OUTPUT)
        result = probe_render_owners(runner=runner)
        self.assertEqual(result["occupancy"], "busy")

    def test_available_when_no_process_holds_render_node(self) -> None:
        def runner(*a, **kw):
            return _completed(stdout=_FREE_OUTPUT)
        result = probe_render_owners(runner=runner)
        self.assertEqual(result["occupancy"], "available")

    def test_unknown_on_ssh_timeout(self) -> None:
        def runner(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=8)
        result = probe_render_owners(runner=runner)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("TimeoutExpired", result["detail"])

    def test_unknown_on_ssh_unreachable(self) -> None:
        def runner(*a, **kw):
            raise OSError("no route to host")
        result = probe_render_owners(runner=runner)
        self.assertEqual(result["occupancy"], "unknown")

    def test_unknown_on_nonzero_exit_with_no_output(self) -> None:
        def runner(*a, **kw):
            return _completed(stdout="", stderr="ssh: connect failed", returncode=255)
        result = probe_render_owners(runner=runner)
        self.assertEqual(result["occupancy"], "unknown")


class OccupancyCacheTests(TestCase):
    def test_caches_within_ttl(self) -> None:
        calls = []

        def probe():
            calls.append(1)
            return {"occupancy": "busy"}

        clock = {"t": 0.0}
        cache = OccupancyCache(ttl_s=30.0, probe=probe, clock=lambda: clock["t"])
        first = cache.get("am4-oxen")
        clock["t"] = 10.0
        second = cache.get("am4-oxen")
        self.assertEqual(len(calls), 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])

    def test_reprobes_after_ttl_expires(self) -> None:
        calls = []

        def probe():
            calls.append(1)
            return {"occupancy": "available"}

        clock = {"t": 0.0}
        cache = OccupancyCache(ttl_s=30.0, probe=probe, clock=lambda: clock["t"])
        cache.get("am4-oxen")
        clock["t"] = 31.0
        cache.get("am4-oxen")
        self.assertEqual(len(calls), 2)

    def test_invalidate_forces_reprobe(self) -> None:
        calls = []

        def probe():
            calls.append(1)
            return {"occupancy": "available"}

        cache = OccupancyCache(ttl_s=30.0, probe=probe)
        cache.get("am4-oxen")
        cache.invalidate("am4-oxen")
        cache.get("am4-oxen")
        self.assertEqual(len(calls), 2)

    def test_keys_are_independent(self) -> None:
        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "busy"})
        cache.get("am4-oxen")
        # A different key has no cached entry yet -> probes fresh (cached=False)
        result = cache.get("other-backend")
        self.assertFalse(result["cached"])


class CheckOccupancyTests(TestCase):
    def test_only_am4_oxen_has_a_live_probe(self) -> None:
        result = check_occupancy("omen-ollama")
        self.assertEqual(result["occupancy"], "available")
        self.assertIn("no occupancy probe declared", result["detail"])

    def test_am4_oxen_uses_the_injected_cache_and_probe(self) -> None:
        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "busy"})
        result = check_occupancy("am4-oxen", cache=cache)
        self.assertEqual(result["occupancy"], "busy")


class ResolveForLaneTests(TestCase):
    def test_available_always_usable(self) -> None:
        self.assertTrue(resolve_for_lane("available", pinned=False))
        self.assertTrue(resolve_for_lane("available", pinned=True))

    def test_busy_never_usable(self) -> None:
        self.assertFalse(resolve_for_lane("busy", pinned=False))
        self.assertFalse(resolve_for_lane("busy", pinned=True))

    def test_unknown_busy_for_opportunistic_available_for_pinned(self) -> None:
        self.assertFalse(resolve_for_lane("unknown", pinned=False))
        self.assertTrue(resolve_for_lane("unknown", pinned=True))


class LeaseTests(TestCase):
    def test_acquire_lease_opportunistic_busy_refused(self) -> None:
        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "busy"})
        lease = acquire_lease("am4-oxen", pinned=False, cache=cache)
        self.assertIsInstance(lease, Lease)
        self.assertFalse(lease.granted)

    def test_acquire_lease_available_granted(self) -> None:
        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "available"})
        lease = acquire_lease("am4-oxen", pinned=False, cache=cache)
        self.assertTrue(lease.granted)

    def test_acquire_lease_pinned_unknown_still_granted(self) -> None:
        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "unknown"})
        lease = acquire_lease("am4-oxen", pinned=True, cache=cache)
        self.assertTrue(lease.granted)

    def test_renew_reprobes_and_can_flip_to_refused(self) -> None:
        state = {"occupancy": "available"}

        def probe():
            return {"occupancy": state["occupancy"]}

        cache = OccupancyCache(ttl_s=30.0, probe=probe)
        lease = acquire_lease("am4-oxen", pinned=False, cache=cache)
        self.assertTrue(lease.granted)

        state["occupancy"] = "busy"
        still_good = lease.renew()
        self.assertFalse(still_good)
        self.assertFalse(lease.granted)
        self.assertEqual(lease.occupancy_at_grant, "busy")

    def test_renew_bypasses_cache_ttl(self) -> None:
        """A renewal must see fresh truth even inside the cache TTL window —
        that's the whole point of a lease renewal (P5 opportunistic work)."""
        state = {"occupancy": "available"}
        clock = {"t": 0.0}

        def probe():
            return {"occupancy": state["occupancy"]}

        cache = OccupancyCache(ttl_s=300.0, probe=probe, clock=lambda: clock["t"])
        lease = acquire_lease("am4-oxen", pinned=False, cache=cache)
        self.assertTrue(lease.granted)

        state["occupancy"] = "busy"
        clock["t"] = 1.0  # well within the 300s TTL
        self.assertFalse(lease.renew())


class LiveProbeTests(TestCase):
    """Exercises the REAL SSH probe against AM4 (Banked Fire P2 acceptance:
    "one live test with the real probe"). Skips cleanly (not a failure) when
    AM4 is unreachable from wherever the suite runs, e.g. off the tailnet —
    mirrors the skip pattern in test_client.py for an environment-gated test.

    Set HEARTH_SKIP_LIVE_PROBES=1 to skip deliberately (e.g. a sandboxed CI
    runner with no network egress at all).
    """

    def test_real_ssh_probe_returns_a_definite_occupancy_reading(self) -> None:
        if os.environ.get("HEARTH_SKIP_LIVE_PROBES"):
            self.skipTest("HEARTH_SKIP_LIVE_PROBES set")
        result = probe_render_owners(timeout_s=8)
        if result["occupancy"] == "unknown":
            self.skipTest(f"AM4 unreachable from this environment: {result.get('detail')}")
        self.assertIn(result["occupancy"], ("available", "busy"))
        self.assertIsInstance(result["detail"], str)
