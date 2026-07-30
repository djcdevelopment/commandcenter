from __future__ import annotations

import os
import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.occupancy import (
    _PROBES,
    Lease,
    OccupancyCache,
    acquire_lease,
    check_occupancy,
    probe_moe_slots,
    probe_oxen_facade,
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


def _moe_slot(is_processing: bool = False, n_past: int = 0, n_ctx: int = 16384) -> dict:
    return {"id": 0, "is_processing": is_processing, "n_past": n_past, "n_ctx": n_ctx}


class ProbeMoeSlotsTests(TestCase):
    def test_available_when_slots_idle(self) -> None:
        def fetch(url, timeout_s):
            return [_moe_slot(), _moe_slot(), _moe_slot(True), _moe_slot()], None
        result = probe_moe_slots(fetch=fetch)
        self.assertEqual(result["occupancy"], "available")
        self.assertEqual(result["detail"]["slots_total"], 4)
        self.assertEqual(result["detail"]["slots_busy"], 1)
        self.assertEqual(result["detail"]["slots_idle"], 3)

    def test_busy_when_all_slots_processing(self) -> None:
        def fetch(url, timeout_s):
            return [_moe_slot(True), _moe_slot(True)], None
        result = probe_moe_slots(fetch=fetch)
        self.assertEqual(result["occupancy"], "busy")

    def test_busy_under_kv_pressure_even_with_idle_slots(self) -> None:
        def fetch(url, timeout_s):
            return [_moe_slot(True, n_past=16000), _moe_slot(False, n_past=15500)], None
        result = probe_moe_slots(fetch=fetch)
        self.assertEqual(result["occupancy"], "busy")
        self.assertGreater(result["detail"]["kv_used_frac"], 0.9)

    def test_kv_frac_none_when_build_lacks_fields(self) -> None:
        def fetch(url, timeout_s):
            return [{"id": 0, "is_processing": False}], None
        result = probe_moe_slots(fetch=fetch)
        self.assertEqual(result["occupancy"], "available")
        self.assertIsNone(result["detail"]["kv_used_frac"])

    def test_older_builds_numeric_state_field(self) -> None:
        def fetch(url, timeout_s):
            return [{"id": 0, "state": 1}], None
        self.assertEqual(probe_moe_slots(fetch=fetch)["occupancy"], "busy")

        def fetch_idle(url, timeout_s):
            return [{"id": 0, "state": 0}], None
        self.assertEqual(probe_moe_slots(fetch=fetch_idle)["occupancy"], "available")

    def test_unknown_on_http_error(self) -> None:
        def fetch(url, timeout_s):
            return None, "HTTP 503"
        result = probe_moe_slots(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("503", result["detail"])

    def test_unknown_on_network_error(self) -> None:
        def fetch(url, timeout_s):
            return None, "URLError: connection refused"
        self.assertEqual(probe_moe_slots(fetch=fetch)["occupancy"], "unknown")

    def test_unknown_on_unexpected_shape(self) -> None:
        def fetch(url, timeout_s):
            return {"error": "not a list"}, None
        self.assertEqual(probe_moe_slots(fetch=fetch)["occupancy"], "unknown")


def _facade_health(backend: dict, status: str = "ok") -> dict:
    """A payload shaped like the real oxen-facade /health (verified 2026-07-30)."""
    return {
        "status": status,
        "node": "am4",
        "facade": "up",
        "backend": backend,
        "aliases": ["oxen-planner", "oxen"],
        "backend_base_url": "http://127.0.0.1:8080",
        "time": 1785422219,
    }


class ProbeOxenFacadeTests(TestCase):
    def test_available_when_backend_reports_ok(self) -> None:
        def fetch(url, timeout_s):
            return _facade_health({"ok": True, "status": 200, "body": {"status": "ok"}}), None
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "available")
        self.assertTrue(result["detail"]["backend_ok"])
        self.assertEqual(result["detail"]["backend_base_url"], "http://127.0.0.1:8080")

    def test_unknown_while_the_model_is_still_loading(self) -> None:
        """llama-server answers 503 "Loading model" during a load — the exact
        window the crash loop lived in on 2026-07-30."""
        def fetch(url, timeout_s):
            body = {"error": {"message": "Loading model", "code": 503}}
            return _facade_health({"ok": False, "status": 503, "body": body}), None
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertFalse(result["detail"]["backend_ok"])
        self.assertEqual(result["detail"]["backend_status"], 503)

    def test_unknown_when_facade_cannot_reach_its_backend(self) -> None:
        def fetch(url, timeout_s):
            return _facade_health({"ok": False, "error": "ConnectionRefusedError: [Errno 111]"}), None
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("ConnectionRefused", result["detail"]["reason"])

    def test_hardcoded_top_level_status_does_not_mask_a_dead_backend(self) -> None:
        """The facade sets {"status": "ok"} whenever the facade PROCESS is up, so
        a probe that trusted the top-level field would call a dead model ready."""
        def fetch(url, timeout_s):
            return _facade_health({"ok": False, "status": 503}, status="ok"), None
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")

    def test_unknown_on_transport_error(self) -> None:
        def fetch(url, timeout_s):
            return None, "URLError: connection refused"
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("URLError", result["detail"])

    def test_unknown_on_unexpected_shape(self) -> None:
        def fetch(url, timeout_s):
            return ["not", "a", "dict"], None
        self.assertEqual(probe_oxen_facade(fetch=fetch)["occupancy"], "unknown")

    def test_unknown_when_payload_carries_no_backend_readiness(self) -> None:
        def fetch(url, timeout_s):
            return {"status": "ok", "facade": "up"}, None
        result = probe_oxen_facade(fetch=fetch)
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("no backend readiness", result["detail"])


class ProbeRegistryTests(TestCase):
    def test_each_am4_rung_probes_a_signal_that_can_go_both_ways(self) -> None:
        """Regression guard (2026-07-30, B70-VERTICAL-TRACE.html): am4-oxen was
        registered against the render-node fuser probe, but every resident
        llama-server holds both render nodes by design — so it read busy forever
        and every ledger event carried a dead signal."""
        self.assertIs(_PROBES["am4-oxen"], probe_oxen_facade)
        self.assertIs(_PROBES["am4-moe"], probe_moe_slots)
        self.assertNotIn(probe_render_owners, _PROBES.values())


class RegistryResolutionTests(TestCase):
    """A cache with no probe installed must resolve the registry PER KEY.

    It used to fall back to a class-level default of probe_render_owners for
    every key, so the cache-carrying paths (acquire_lease, Lease.renew — the
    Banked Fire drain lane leases am4-oxen) probed AM4's render nodes no matter
    which backend they asked about.
    """

    def test_cache_without_a_probe_resolves_the_declared_probe(self) -> None:
        calls = []

        def fake():
            calls.append(1)
            return {"occupancy": "available", "detail": "fake"}

        with patch.dict(_PROBES, {"am4-oxen": fake}):
            result = check_occupancy("am4-oxen", cache=OccupancyCache(ttl_s=30.0))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["detail"], "fake")

    def test_acquire_lease_resolves_the_declared_probe(self) -> None:
        calls = []

        def fake():
            calls.append(1)
            return {"occupancy": "busy"}

        with patch.dict(_PROBES, {"am4-oxen": fake}):
            lease = acquire_lease("am4-oxen", pinned=False, cache=OccupancyCache(ttl_s=30.0))
        self.assertEqual(len(calls), 1)
        self.assertFalse(lease.granted)

    def test_an_installed_cache_probe_still_wins(self) -> None:
        """The Lease.renew/test contract: never override a probe a caller
        deliberately handed us."""
        def registry_probe():
            raise AssertionError("registry probe must not be consulted")

        cache = OccupancyCache(ttl_s=30.0, probe=lambda: {"occupancy": "busy"})
        with patch.dict(_PROBES, {"am4-oxen": registry_probe}):
            result = check_occupancy("am4-oxen", cache=cache)
        self.assertEqual(result["occupancy"], "busy")


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
    def test_am4_moe_uses_the_injected_cache_and_probe(self) -> None:
        cache = OccupancyCache(ttl_s=60.0, probe=lambda: {"occupancy": "busy", "detail": "x"})
        result = check_occupancy("am4-moe", cache=cache)
        self.assertEqual(result["occupancy"], "busy")

    def test_backend_without_a_declared_probe_reports_available(self) -> None:
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

    def test_real_facade_probe_reads_backend_readiness(self) -> None:
        """The am4-oxen probe against the live :8090 facade. "unknown" is a
        legitimate live answer (facade down, or a model mid-load), so it skips
        rather than fails — what this asserts is that a REACHABLE facade yields
        a parsed readiness detail, not a stuck reading."""
        if os.environ.get("HEARTH_SKIP_LIVE_PROBES"):
            self.skipTest("HEARTH_SKIP_LIVE_PROBES set")
        result = probe_oxen_facade(timeout_s=4)
        if result["occupancy"] == "unknown" and not isinstance(result["detail"], dict):
            self.skipTest(f"oxen facade unreachable from this environment: {result.get('detail')}")
        self.assertIn(result["occupancy"], ("available", "unknown"))
        self.assertIsInstance(result["detail"], dict)
        self.assertIn("backend_ok", result["detail"])
