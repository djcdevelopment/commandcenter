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


class LaneSlotsTests(unittest.TestCase):
    """S4 (2026-09-27): the drain holds one in_flight record; the Linux tick keeps its own
    per-lane slots so a night can run several candidates on the fast lane while the deep lane
    stays at one, and an experiment never overlaps anything."""

    def test_caps_parse_and_default(self) -> None:
        with mock.patch.dict("os.environ", {"BANKEDFIRE_SLOTS": "fast=2,deep=1,experiment=0,junk"}):
            self.assertEqual(lane.lane_slots(), {"fast": 2, "deep": 1, "experiment": 0})
        with mock.patch.dict("os.environ", {}, clear=False):
            import os; os.environ.pop("BANKEDFIRE_SLOTS", None)
            self.assertEqual(lane.lane_slots(), {"fast": 3, "deep": 1, "experiment": 1, "deepagents": 1})

    def test_brief_lane_from_block_and_class(self) -> None:
        from hearth.backlog.briefs import Brief
        mk = lambda body, tc: Brief(slug="s", title="t", body=body, builders=None, task_class=tc,  # noqa: E731
                                    est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="s.md")
        self.assertEqual(lane.brief_lane(mk(BRIEF, "local-work")), "deep")
        self.assertEqual(lane.brief_lane(mk(BRIEF.replace("lane: deep", "lane: fast"), "local-work")), "fast")
        self.assertEqual(lane.brief_lane(mk(BRIEF.replace("lane: deep", "lane: auto"), "local-work")), "deep")
        self.assertEqual(lane.brief_lane(mk("seat: 0\ndropin: x.conf\ncampaign: true\n---\ngo", "experiment")), "experiment")

    def test_experiment_spec_requires_seat_dropin_campaign(self) -> None:
        spec = lane.experiment_spec_from_brief("seat: 1\ndropin: stage5.conf\ncampaign: echo hi\nexpect_model: m\n---\nwhy", "id1")
        self.assertEqual((spec["seat"], spec["dropin"], spec["campaign"], spec["expect_model"]), (1, "stage5.conf", "echo hi", "m"))
        with self.assertRaises(ValueError):
            lane.experiment_spec_from_brief("seat: 1\n---\nwhy", "id2")

    def test_experiment_status_maps_outcome(self) -> None:
        with mock.patch("fleet.experiment_linux.status", return_value={"phase": "campaign_running", "outcome": None}):
            self.assertEqual(lane.task_status("exp_abc")["done"], False)
        with mock.patch("fleet.experiment_linux.status", return_value={"phase": "done", "outcome": "restore_failed"}):
            s = lane.task_status("exp_abc")
            self.assertEqual((s["done"], s["result"]["ok"]), (True, False))
            self.assertTrue(s["result"]["winner"].startswith("experiment:"))


class HumanWaitsTests(unittest.TestCase):
    def test_only_human_submitted_local_work_counts_as_queue(self) -> None:
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs" / "operator"
            for wid, who, st in (("work_" + "1" * 32, "claude-frontier", "running"),
                                 ("work_" + "2" * 32, lane.DRAIN_CALLER_ID, "running"),
                                 ("work_" + "3" * 32, "codex-cli", "accepted")):
                d = root / wid; d.mkdir(parents=True)
                (d / "work-manifest.json").write_text(json.dumps({"work_id": wid, "status": st, "caller": {"submitted_by": who},
                                                                   "route": {"selected_lane": "fast"}}))
            with mock.patch.object(lane, "_REPO_ROOT", Path(tmp)):
                self.assertEqual(lane.queue_status()["running"], 1)
                self.assertEqual(lane.in_use_by_lane([]), {"fast": 1})


class DeepAgentsLaneTests(unittest.TestCase):
    """A `deepagents` brief runs the existing bounded runner and its idempotent accounting;
    the candidate stays review_required (research/deepagents.md §5)."""

    def test_spec_needs_source_and_task(self) -> None:
        spec = lane.deepagents_spec_from_brief("source: /tmp/x.py\nbackend: omen-dense\nreport: true\nmax_report_words: 200\n---\nExplain x.", "r1")
        self.assertEqual((spec["source"], spec["backend"], spec["report"], spec["max_report_words"], spec["task"]),
                         ("/tmp/x.py", "omen-dense", True, 200, "Explain x."))
        with self.assertRaises(ValueError):
            lane.deepagents_spec_from_brief("backend: omen\n---\nno source", "r2")
        with self.assertRaises(ValueError):
            lane.deepagents_spec_from_brief("source: /tmp/x.py\n---\n", "r3")

    def test_status_maps_outcome_and_default_cap(self) -> None:
        with mock.patch("fleet.deepagents_linux.status", return_value={"phase": "running", "outcome": None}):
            self.assertFalse(lane.task_status("da_r1")["done"])
        with mock.patch("fleet.deepagents_linux.status", return_value={"phase": "done", "outcome": "succeeded"}):
            s = lane.task_status("da_r1")
            self.assertEqual((s["done"], s["result"]["ok"], s["result"]["winner"]), (True, True, "deepagents:r1"))
        with mock.patch.dict("os.environ", {}, clear=False):
            import os; os.environ.pop("BANKEDFIRE_SLOTS", None)
            self.assertEqual(lane.lane_slots().get("deepagents"), 1)
