"""Edges observed while porting Banked Fire to the Linux lane (2026-09-27, omen-linux).

Each test names the observation that earned it:
  * a brief's local-work front block must parse into submit_local_work arguments, and a
    malformed block must fail loudly before any dispatch;
  * a slot adopted from the Windows era names a conductor plan id; the Linux status hook
    must resolve it as no-winner (so the drain frees the slot) instead of "unreachable";
  * presence fails closed: any unreadable signal reads as "present";
  * the omen-vllm probe reads "unknown" (= busy) when a seat's metrics are unreadable.
"""
import unittest
from unittest import mock

from fleet import bankedfire_linux as lane
from fleet import presence_linux as presence
from hearth.toolsurface import occupancy as occ

BRIEF = """repo: /tmp/repo
commit: 0123456789abcdef0123456789abcdef01234567
lane: deep
artifact_kind: whole_file
target_path: tests/test_x.py
paths: [tests/test_x.py, x.py]
criteria:
  - one
  - two
---
Do the thing.
"""


class BriefBlockTests(unittest.TestCase):
    def test_block_parses_to_submit_args(self) -> None:
        args = lane.submit_args_from_brief(BRIEF, idempotency_key="k")
        self.assertEqual(args["files"], ["tests/test_x.py", "x.py"])
        self.assertEqual(args["acceptance_criteria"], ["one", "two"])
        self.assertEqual((args["lane"], args["artifact_kind"], args["target_path"]), ("deep", "whole_file", "tests/test_x.py"))
        self.assertEqual(args["base_commit"], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(args["intent"], "Do the thing.")
        self.assertEqual(args["idempotency_key"], "k")

    def test_missing_block_or_criteria_fails_before_dispatch(self) -> None:
        with self.assertRaises(ValueError):
            lane.parse_local_work_block("no front block here")
        with self.assertRaises(ValueError):
            lane.parse_local_work_block("repo: /tmp/repo\npaths: [a]\n---\nintent")

    def test_submit_hook_reports_a_bad_brief_as_ok_false(self) -> None:
        result = lane.submit_task(prompt="garbage", plan_id_hint="x")
        self.assertFalse(result["ok"])
        self.assertIn("ValueError", result["error"])


class StatusHookTests(unittest.TestCase):
    def test_legacy_conductor_plan_resolves_as_no_winner(self) -> None:
        status = lane.task_status("hearth-drain-known-bad-retest-omen-wsl-92f2f933")
        self.assertTrue(status["ok"] and status["done"])
        self.assertIsNone(status["result"]["winner"])

    def test_local_work_statuses_map_to_the_drain_shape(self) -> None:
        with mock.patch.object(lane, "call_tool", return_value={"status": "running"}):
            self.assertEqual(lane.task_status("work_" + "a" * 32)["done"], False)
        with mock.patch.object(lane, "call_tool", return_value={"status": "awaiting_review", "route": {"provider": "omen-dense-27b"}}):
            s = lane.task_status("work_" + "a" * 32)
            self.assertEqual((s["done"], s["result"]["ok"], s["result"]["winner"]), (True, True, "omen-local-work:omen-dense-27b"))
        with mock.patch.object(lane, "call_tool", return_value={"status": "failed", "route": {"provider": "omen-vllm"}, "failure": "x"}):
            s = lane.task_status("work_" + "a" * 32)
            self.assertEqual((s["done"], s["result"]["ok"]), (True, False))
        with mock.patch.object(lane, "call_tool", side_effect=RuntimeError("door down")):
            self.assertFalse(lane.task_status("work_" + "a" * 32)["ok"])


class PresenceTests(unittest.TestCase):
    def test_unreadable_signals_read_as_present(self) -> None:
        with mock.patch.object(presence, "idle_ms", return_value=None), \
             mock.patch.object(presence, "rdp_sessions", return_value=None), \
             mock.patch.object(presence, "ai_mode", return_value="online"), \
             mock.patch.object(presence, "PAUSE_FILE", presence.Path("/nonexistent/pause.dispatch")):
            rep = presence.report(idle_min=20)
        self.assertFalse(rep["away"])
        self.assertIn("idle:unreadable", rep["present_reasons"])
        self.assertIn("rdp:unreadable", rep["present_reasons"])

    def test_idle_and_alone_and_online_reads_as_away(self) -> None:
        with mock.patch.object(presence, "idle_ms", return_value=25 * 60_000), \
             mock.patch.object(presence, "rdp_sessions", return_value=0), \
             mock.patch.object(presence, "ai_mode", return_value="online"), \
             mock.patch.object(presence, "PAUSE_FILE", presence.Path("/nonexistent/pause.dispatch")):
            rep = presence.report(idle_min=20)
        self.assertTrue(rep["away"])
        self.assertEqual(rep["mode"], "deep-research")


class OmenVllmProbeTests(unittest.TestCase):
    def test_unreadable_seat_metrics_read_unknown(self) -> None:
        with mock.patch.object(occ, "_tenancy_fence", return_value=None), \
             mock.patch.object(occ, "_fetch_vllm_metrics", return_value=(None, "HTTP 401")):
            self.assertEqual(occ.probe_omen_vllm()["occupancy"], "unknown")

    def test_idle_seats_but_operator_present_reads_busy_with_reasons(self) -> None:
        gauges = {"vllm:num_requests_running": 0.0, "vllm:num_requests_waiting": 0.0}
        with mock.patch.object(occ, "_tenancy_fence", return_value=None), \
             mock.patch.object(occ, "_fetch_vllm_metrics", return_value=(gauges, None)), \
             mock.patch.object(occ, "_hearth_active_jobs", return_value=(0, None)), \
             mock.patch("fleet.presence_linux.report", return_value={"away": False, "mode": "frontier-collab", "present_reasons": ["idle:1m<20m"]}):
            res = occ.probe_omen_vllm()
        self.assertEqual(res["occupancy"], "busy")
        self.assertEqual(res["detail"]["presence"]["present_reasons"], ["idle:1m<20m"])

    def test_everything_idle_reads_available(self) -> None:
        gauges = {"vllm:num_requests_running": 0.0, "vllm:num_requests_waiting": 0.0}
        with mock.patch.object(occ, "_tenancy_fence", return_value=None), \
             mock.patch.object(occ, "_fetch_vllm_metrics", return_value=(gauges, None)), \
             mock.patch.object(occ, "_hearth_active_jobs", return_value=(0, None)), \
             mock.patch("fleet.presence_linux.report", return_value={"away": True, "mode": "deep-research", "present_reasons": []}):
            self.assertEqual(occ.probe_omen_vllm()["occupancy"], "available")


if __name__ == "__main__":
    unittest.main()
