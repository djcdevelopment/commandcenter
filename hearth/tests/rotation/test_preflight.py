"""Pre-flight gates: every one fails CLOSED, and unreadable is never a pass."""

import unittest
from datetime import datetime, timedelta, timezone

from hearth.rotation import preflight as pf


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class GateFenceTest(unittest.TestCase):
    def test_free_fence_passes(self):
        gate = pf.gate_fence(None)
        self.assertTrue(gate["ok"])
        self.assertIsNone(gate["remedy"])

    def test_held_fence_names_the_session(self):
        gate = pf.gate_fence("imgsess_c1972c5d")
        self.assertFalse(gate["ok"])
        self.assertIn("imgsess_c1972c5d", gate["detail"])

    def test_unreadable_fence_is_not_a_free_fence(self):
        gate = pf.gate_fence("unreadable")
        self.assertFalse(gate["ok"])
        self.assertIn("cannot read", gate["detail"])

    def test_held_but_idle_gets_the_drain_remedy(self):
        # 2026-09-04: the lease renewed every 30 s while the cards sat idle.
        # "Held" and "busy" are different facts; only one of them has a remedy.
        gate = pf.gate_fence("imgsess_abc", {"available": True, "queued": 0, "running": 0,
                                             "lease_age_s": 6.0})
        self.assertFalse(gate["ok"])
        self.assertIn("queued 0, running 0", gate["detail"])
        self.assertIn("IDLE", gate["remedy"])
        self.assertIn("stop_image_session", gate["remedy"])
        self.assertIn("do NOT kill", gate["remedy"])

    def test_held_and_busy_says_wait_or_drain(self):
        gate = pf.gate_fence("imgsess_abc", {"available": True, "queued": 1, "running": 1,
                                             "lease_age_s": 31.0})
        self.assertFalse(gate["ok"])
        self.assertIn("running 1", gate["detail"])
        self.assertIn("BUSY", gate["remedy"])

    def test_activity_unavailable_falls_back_to_the_plain_remedy(self):
        gate = pf.gate_fence("imgsess_abc", {"available": False, "error": "boom"})
        self.assertFalse(gate["ok"])
        self.assertNotIn("queued", gate["detail"])
        self.assertIn("wait for the imagegen lane", gate["remedy"])


class GateSiblingsTest(unittest.TestCase):
    def test_declared_entries_pass(self):
        gate = pf.gate_siblings(["phi4-vk0", "phi4-vk1", "qwen14b-vk1", "qwen3-30b-a3b"])
        self.assertTrue(gate["ok"])

    def test_missing_vk0_is_the_pre_restart_state(self):
        # A running llama-swap keeps the entries it booted with: the yaml on disk
        # already says -vk0, but until ArcServe restarts a pin gets a 404.
        gate = pf.gate_siblings(["phi4-vk1", "phi4-vk2", "qwen14b-vk1", "qwen3-30b-a3b"])
        self.assertFalse(gate["ok"])
        self.assertIn("phi4-vk0", gate["detail"])
        self.assertIn("restart ArcServe", gate["remedy"])

    def test_unreachable_swap_is_not_a_pass(self):
        gate = pf.gate_siblings(None)
        self.assertFalse(gate["ok"])
        self.assertIn("unreachable", gate["detail"])

    def test_required_entries_are_caller_supplied(self):
        gate = pf.gate_siblings(["gptoss20b-vk0"], required=("gptoss20b-vk0",))
        self.assertTrue(gate["ok"])


class GateProductionTest(unittest.TestCase):
    def test_at_rate_with_a_live_ping_passes(self):
        gate = pf.gate_production({"verdict": "at_rate", "observed_tok_s": 107.99,
                                   "baseline_tok_s": 106.0, "frac_of_baseline": 1.0187,
                                   "last_ping_ok": True})
        self.assertTrue(gate["ok"])
        self.assertIn("107.99", gate["detail"])

    def test_degraded_refuses(self):
        gate = pf.gate_production({"verdict": "degraded", "observed_tok_s": 74.36,
                                   "baseline_tok_s": 106.0, "frac_of_baseline": 0.7015,
                                   "last_ping_ok": True})
        self.assertFalse(gate["ok"])
        self.assertIn("degraded", gate["detail"])

    def test_at_rate_from_a_stale_tail_with_a_dead_ping_refuses(self):
        # The verdict comes off the sample tail; the rung can be gone since.
        gate = pf.gate_production({"verdict": "at_rate", "observed_tok_s": 83.92,
                                   "baseline_tok_s": 106.0, "last_ping_ok": False})
        self.assertFalse(gate["ok"])
        self.assertIn("last ping failed", gate["detail"])

    def test_missing_state_refuses(self):
        self.assertFalse(pf.gate_production(None)["ok"])


class GateDoorFreshTest(unittest.TestCase):
    COMMIT = ("0cb5275", _at("2026-09-03T18:20:00+00:00"))

    def test_gateway_started_after_the_commit_passes(self):
        gate = pf.gate_door_fresh(_at("2026-09-03T18:30:00+00:00"), self.COMMIT)
        self.assertTrue(gate["ok"])

    def test_gateway_older_than_the_commit_refuses_with_the_remedy(self):
        # The 2026-09-03 shape exactly: a 17:50 door, an 18:08 provider commit.
        gate = pf.gate_door_fresh(_at("2026-09-03T17:50:00+00:00"), self.COMMIT)
        self.assertFalse(gate["ok"])
        self.assertIn("0cb5275", gate["detail"])
        self.assertIn("HearthGatewayRestart", gate["remedy"])

    def test_comparison_is_tz_correct_across_offsets(self):
        # A local-time gateway stamp and a UTC commit stamp must not be compared naively:
        # 11:50-07:00 is 18:50Z, which IS after an 18:20Z commit.
        gate = pf.gate_door_fresh(_at("2026-09-03T11:50:00-07:00"), self.COMMIT)
        self.assertTrue(gate["ok"])

    def test_unknown_start_time_refuses(self):
        self.assertFalse(pf.gate_door_fresh(None, self.COMMIT)["ok"])

    def test_unknown_commit_refuses(self):
        self.assertFalse(pf.gate_door_fresh(_at("2026-09-03T18:30:00+00:00"), None)["ok"])


class PreflightAggregateTest(unittest.TestCase):
    def test_go_requires_every_gate(self):
        ok = [{"id": "G0", "ok": True}, {"id": "G1", "ok": True}]
        self.assertTrue(pf.preflight(ok)["go"])
        self.assertFalse(pf.preflight(ok + [{"id": "G2", "ok": False}])["go"])

    def test_no_gates_is_not_a_go(self):
        self.assertFalse(pf.preflight([])["go"])

    def test_report_prints_the_remedy_for_a_failed_gate(self):
        report = pf.format_report(pf.preflight([
            pf.gate_fence("imgsess_abc"),
        ]))
        self.assertIn("NO-GO", report)
        self.assertIn("imgsess_abc", report)
        self.assertIn("->", report)


class ReadGatewayStartTest(unittest.TestCase):
    LINE = ("2026-09-03 17:50:12,345 mcp.server.streamable_http_manager INFO "
            "StreamableHTTP session manager started")

    def test_reads_the_last_start_not_the_first(self, ):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "gateway-task.log"
            log.write_text(
                self.LINE + "\n"
                + "2026-09-04 02:05:00,001 mcp.server.streamable_http_manager INFO "
                  "StreamableHTTP session manager started\n"
                + "INFO:     127.0.0.1:1 - \"POST /mcp HTTP/1.1\" 200 OK\n",
                encoding="utf-8")
            started = pf.read_gateway_start(log)
        self.assertIsNotNone(started)
        self.assertEqual((started.year, started.month, started.day, started.hour), (2026, 9, 4, 2))

    def test_binary_noise_does_not_break_the_read(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "gateway-task.log"
            log.write_bytes(b"\xff\xfe garbage \x00\n" + self.LINE.encode("utf-8") + b"\n")
            self.assertIsNotNone(pf.read_gateway_start(log))

    def test_missing_file_is_none_not_an_exception(self):
        from pathlib import Path
        self.assertIsNone(pf.read_gateway_start(Path("does-not-exist.log")))


if __name__ == "__main__":
    unittest.main()
