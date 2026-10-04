"""Edges observed while porting Banked Fire to the Linux lane (2026-09-27, omen-linux).

Each test names the observation that earned it:
  * a brief's local-work front block must parse into submit_local_work arguments, and a
    malformed block must fail loudly before any dispatch;
  * a slot adopted from the Windows era names a conductor plan id; the Linux status hook
    must resolve it as no-winner (so the drain frees the slot) instead of "unreachable";
  * presence fails closed: any unreadable signal reads as "present";
  * the omen-vllm probe reads "unknown" (= busy) when a seat's metrics are unreadable.
"""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fleet import bankedfire_linux as lane
from fleet import presence_linux as presence
from hearth.toolsurface import occupancy as occ

_REAL_AM4_SWITCH = lane.am4_switch   # the mixin replaces lane.am4_switch with a mock


class ProfileIsolation:
    """Integration 2026-10-03: tick() and am4_switch() write the host file ~/.config/omen-vllm/am4-profile (which the
    live gateway reads on every route) and am4_profile() reads AM4 over ssh. A test that reaches either gets a temp
    file and a stub: the profile reads None (unreadable: nothing to reconcile, nothing wanted) and a switch is a mock."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile_file = Path(tmp.name) / "am4-profile"
        for patcher in (mock.patch.object(lane, "AM4_PROFILE_PATH", self.profile_file),
                        mock.patch.object(lane, "am4_profile", return_value=None),
                        mock.patch.object(lane, "am4_switch", return_value={"target": None, "rc": None})):
            patcher.start()
            self.addCleanup(patcher.stop)


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
    def setUp(self) -> None:
        # These pin the prod gate; the live environment file may say dev (ADR-0053).
        patcher = mock.patch.dict("os.environ", {"HEARTH_ENVIRONMENT_FILE": "/nonexistent/environment"})
        patcher.start()
        self.addCleanup(patcher.stop)

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
            self.assertEqual(lane.lane_slots(), {"fast": 3, "deep": 1, "experiment": 1, "deepagents": 1, "tool": 2})

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


class ExperimentExclusivityTests(ProfileIsolation, unittest.TestCase):
    """2026-09-27 20:56Z, first live tick: the experiment brief and a deepagents brief were
    dispatched in one loop; the seat swap removed the 27B mid-delivery. Once an experiment is
    dispatched the tick ends, and while its slot is held nothing else dispatches."""

    def _tick(self, slots, scans_next, caps=None):
        import json, tempfile
        from pathlib import Path
        from hearth.backlog.briefs import Brief
        exp = Brief(slug="e", title="t", body="seat: 0\ndropin: x.conf\ncampaign: true\n---\ngo", builders=None,
                    task_class="experiment", est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="e.md")
        lw = Brief(slug="w", title="t", body=BRIEF, builders=None, task_class="local-work", est_tokens=None,
                   requires=(), max_age_s=None, source="authored", source_ref="w.md")
        briefs = {"experiment": exp, "local-work": lw}
        calls = []
        def fake_run_tick(**kw):
            calls.append(kw)
            arm = json.loads(Path(kw["arm_state_path"]).read_text())
            arm["in_flight"] = {"plan_id": f"p{len(calls)}", "source": "authored", "source_ref": "x"}
            Path(kw["arm_state_path"]).write_text(json.dumps(arm))
            return {"reason": "dispatched:x"}
        with tempfile.TemporaryDirectory() as tmp:
            arm = Path(tmp) / "arm.json"; arm.write_text(json.dumps({"armed": True, "scope": "authored", "in_flight": None}))
            with mock.patch.object(lane.drain, "default_arm_state_path", return_value=arm), \
                 mock.patch.object(lane.drain, "run_tick", side_effect=fake_run_tick), \
                 mock.patch.object(lane.drain, "_record_tick", return_value=None), \
                 mock.patch.object(lane, "reconcile_slots", return_value=[]), \
                 mock.patch.object(lane, "load_slots", return_value=list(slots)), \
                 mock.patch.object(lane, "save_slots"), \
                 mock.patch.object(lane, "in_use_by_lane", side_effect=lambda s: {r["lane"]: 1 for r in s}), \
                 mock.patch.object(lane.backlog_sources, "authored_source", return_value=None), \
                 mock.patch.object(lane.backlog_sources, "refined_source", return_value=None), \
                 mock.patch.object(lane.backlog_sources, "candidate_source", return_value=None), \
                 mock.patch.object(lane.backlog_select, "iter_candidates", side_effect=lambda scope, scans: iter([briefs.get(scans_next.pop(0))] if scans_next else [])), \
                 mock.patch.dict("os.environ", {"BANKEDFIRE_SLOTS": caps or "fast=3,deep=1,experiment=1,deepagents=1"}):
                return lane.tick(), calls

    def test_an_experiment_dispatch_ends_the_tick(self) -> None:
        report, calls = self._tick(slots=[], scans_next=["experiment", "local-work"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["reason"], "dispatched:experiment-holds-the-seats")
        self.assertEqual([d["lane"] for d in report["dispatched"]], ["experiment"])

    def test_nothing_dispatches_while_an_experiment_slot_is_held(self) -> None:
        held = [{"plan_id": "exp_1", "lane": "experiment"}]
        report, calls = self._tick(slots=held, scans_next=["local-work"])
        self.assertEqual(calls, [])
        self.assertEqual(report["reason"], "experiment-in-flight")


class ProofingBriefTests(unittest.TestCase):
    """Slice B (2026-09-27): a priced candidate brief ("proofing") is the drain's own prose and used
    to fail the local-work parse on every tick; it now becomes a whole_file proposal on the deep lane."""

    CANDIDATES = {"candidates": [{"candidate_id": "prefer_validation:omen|qwen3.8-27b|omen-dense-27b",
                                  "experiment_type": "prefer_validation",
                                  "subject": {"builder_id": "omen", "model_id": "qwen3.8-27b", "backend": "omen-dense-27b"},
                                  "question": "Does the dense lane hold its rate?", "evidence_sought": "rate at depth",
                                  "gate": "g", "risk_accepted": "none", "confidence": 0.4, "last_observed": "2026-09-27"}]}

    def _brief_body(self, cid="prefer_validation:omen|qwen3.8-27b|omen-dense-27b"):
        from hearth.backlog import sources as src
        return src.candidate_prompt({"candidate_id": cid, "worth_points": 8, "reason": "dense lane matters"})

    def test_candidate_brief_becomes_whole_file_deep_submit_args(self) -> None:
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp) / "c.json"; cand.write_text(json.dumps(self.CANDIDATES))
            with mock.patch.object(lane, "resolve_commit", return_value="f" * 40):
                args = lane.proofing_args_from_brief(self._brief_body(), idempotency_key="k", candidates_path=cand, repo="/r")
        self.assertEqual((args["lane"], args["artifact_kind"], args["files"]), ("deep", "whole_file", ["knowledge/README.md"]))
        self.assertEqual(args["target_path"], "proposals/" + lane.backlog_sources.safe_slug("prefer_validation:omen|qwen3.8-27b|omen-dense-27b") + ".md")
        self.assertEqual(len(args["acceptance_criteria"]), 3)
        for needle in ("prefer_validation:omen|qwen3.8-27b|omen-dense-27b", "Does the dense lane hold its rate?", "rate at depth"):
            self.assertIn(needle, args["intent"])

    def test_stale_candidate_fails_before_dispatch(self) -> None:
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp) / "c.json"; cand.write_text(json.dumps(self.CANDIDATES))
            with self.assertRaises(ValueError):
                lane.proofing_args_from_brief(self._brief_body("known_bad_retest:omen-wsl|x|vllm"), idempotency_key="k", candidates_path=cand)
            with mock.patch.object(lane.backlog_sources, "DEFAULT_EXPERIMENT_CANDIDATES_PATH", cand):
                res = lane.submit_task(prompt=self._brief_body("known_bad_retest:omen-wsl|x|vllm"), plan_id_hint="h", task_class="proofing")
        self.assertFalse(res["ok"]); self.assertIn("stale candidate", res["error"])

    def test_brief_lane_counts_proofing_against_deep(self) -> None:
        from hearth.backlog.briefs import Brief
        b = Brief(slug="s", title="t", body=self._brief_body(), builders=None, task_class="proofing",
                  est_tokens=None, requires=("proposals/s.md",), max_age_s=None, source="candidate", source_ref="x")
        self.assertEqual(lane.brief_lane(b), "deep")


class SkipsTests(ProfileIsolation, unittest.TestCase):
    """A candidate whose dispatch failed must not be re-picked every 30 minutes; a priced id that no
    longer exists in the derived list is stale and excluded without a file row."""

    def test_failed_and_stale_candidates_are_excluded_and_expiry_is_honoured(self) -> None:
        import json, tempfile
        from datetime import datetime, timedelta
        from pathlib import Path
        now = datetime(2026, 9, 28, 1, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            skips = Path(tmp) / "skips.json"; worth = Path(tmp) / "w.json"; cand = Path(tmp) / "c.json"
            worth.write_text(json.dumps({"entries": [{"candidate_id": "live:1", "worth_points": 3},
                                                     {"candidate_id": "gone:2", "worth_points": 9},
                                                     {"candidate_id": "old:3", "worth_points": 1, "status": "retired"}]}))
            cand.write_text(json.dumps({"candidates": [{"candidate_id": "live:1"}, {"candidate_id": "failed:4"}]}))
            lane.add_skip("failed:4", "dispatch-failed: boom", path=skips, now=now)
            excl = lane.candidate_exclusions(now + timedelta(days=1), skips=skips, worth_path=worth, candidates_path=cand)
            self.assertEqual(set(excl), {"failed:4", "gone:2"})   # old:3 is retired, never offered, not "stale"
            expired = lane.candidate_exclusions(now + timedelta(days=lane.SKIP_DAYS, seconds=1), skips=skips, worth_path=worth, candidates_path=cand)
            self.assertEqual(set(expired), {"gone:2"})

    def test_scope_all_with_nothing_priced_reports_no_candidates_without_dispatch(self) -> None:
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            arm = Path(tmp) / "arm.json"; arm.write_text(json.dumps({"armed": True, "scope": "all", "in_flight": None}))
            empty = lane.backlog_sources.SourceScan((), ())
            with mock.patch.object(lane.drain, "default_arm_state_path", return_value=arm), \
                 mock.patch.object(lane.drain, "run_tick") as run_tick, \
                 mock.patch.object(lane.drain, "_record_tick", return_value=None), \
                 mock.patch.object(lane, "reconcile_slots", return_value=[]), \
                 mock.patch.object(lane, "load_slots", return_value=[]), \
                 mock.patch.object(lane, "candidate_exclusions", return_value=frozenset({"gone:2"})), \
                 mock.patch.object(lane.backlog_sources, "authored_source", return_value=empty), \
                 mock.patch.object(lane.backlog_sources, "refined_source", return_value=empty), \
                 mock.patch.object(lane.backlog_sources, "candidate_source", return_value=empty):
                report = lane.tick()
        self.assertEqual(report["reason"], "no-candidates"); self.assertEqual(report["excluded"], ["gone:2"])
        run_tick.assert_not_called()

    def test_an_early_exit_is_still_a_ledgered_tick(self) -> None:
        """2026-09-27 22:29Z..00:01Z: five no-candidates ticks left no ledger row because the Linux
        tick decided before drain.run_tick ran. ADR-0006: every tick is ledgered."""
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            arm = Path(tmp) / "arm.json"; arm.write_text(json.dumps({"armed": True, "scope": "authored", "in_flight": None}))
            empty = lane.backlog_sources.SourceScan((), ())
            with mock.patch.object(lane.drain, "default_arm_state_path", return_value=arm), \
                 mock.patch.object(lane.drain, "run_tick") as run_tick, \
                 mock.patch.object(lane.drain, "_record_tick", return_value="evt_x") as record, \
                 mock.patch.object(lane, "reconcile_slots", return_value=[]), \
                 mock.patch.object(lane, "load_slots", return_value=[]), \
                 mock.patch.object(lane, "candidate_exclusions", return_value=frozenset()), \
                 mock.patch.object(lane.backlog_sources, "authored_source", return_value=empty), \
                 mock.patch.object(lane.backlog_sources, "refined_source", return_value=empty), \
                 mock.patch.object(lane.backlog_sources, "candidate_source", return_value=empty):
                report = lane.tick()
        run_tick.assert_not_called()
        record.assert_called_once()
        self.assertEqual(record.call_args.args[0], "no-op:no-candidates")
        self.assertEqual(report["ledger_event_id"], "evt_x")


class Am4AliasProbeTests(unittest.TestCase):
    """2026-09-28, the AM4 tool-pair profile: a rung with no probe read "available" whatever profile
    AM4 was serving, so tag routes would have landed on a dead alias. The facade's /oxen/ready for
    the rung's own alias decides; HEARTH's active jobs on it make it busy."""

    def _ready(self, ready=True, alias="am4-tool-4070ti", error=None):
        return lambda a, env, t: (None, error) if error else ({"aliases": [{"alias": alias, "ready": ready, "model": "m"}]}, None)

    def test_not_ready_or_unreachable_reads_unknown(self) -> None:
        self.assertEqual(occ.probe_oxen_alias("am4-tool-4070ti", ready=self._ready(ready=False))["occupancy"], "unknown")
        self.assertEqual(occ.probe_oxen_alias("am4-tool-4070ti", ready=self._ready(error="URLError: refused"))["occupancy"], "unknown")
        # a payload for another alias is not this rung's readiness
        self.assertEqual(occ.probe_oxen_alias("am4-tool-5070", ready=self._ready(alias="am4-tool-4070ti"))["occupancy"], "unknown")

    def test_ready_reads_available_unless_hearth_has_a_job_on_it(self) -> None:
        with mock.patch.object(occ, "_hearth_active_jobs_for", return_value=(0, None)):
            self.assertEqual(occ.probe_oxen_alias("am4-tool-4070ti", ready=self._ready())["occupancy"], "available")
        with mock.patch.object(occ, "_hearth_active_jobs_for", return_value=(1, None)):
            self.assertEqual(occ.probe_oxen_alias("am4-tool-4070ti", ready=self._ready())["occupancy"], "busy")

    def test_the_registry_names_every_am4_alias_rung(self) -> None:
        for rung in ("am4-vllm", "am4-tool-4070ti", "am4-tool-5070"):
            self.assertIn(rung, occ._PROBES)


class ToolLaneTests(unittest.TestCase):
    """2026-09-28: deepagents briefs bound for the AM4 tool-pair seats count against their own
    `tool` lane (two seats, one job each) rather than the B70-bound deepagents slot."""

    def test_am4_tool_backends_take_the_tool_lane(self) -> None:
        from hearth.backlog.briefs import Brief
        mk = lambda body: Brief(slug="s", title="t", body=body, builders=None, task_class="deepagents",  # noqa: E731
                                est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="s.md")
        self.assertEqual(lane.brief_lane(mk("source: /x.py\nbackend: am4-tool-4070ti\n---\ngo")), "tool")
        self.assertEqual(lane.brief_lane(mk("source: /x.py\nbackend: am4-tool-5070\n---\ngo")), "tool")
        self.assertEqual(lane.brief_lane(mk("source: /x.py\nbackend: omen-dense\n---\ngo")), "deepagents")
        self.assertEqual(lane.brief_lane(mk("source: /x.py\n---\ngo")), "deepagents")


class Am4ProfileFollowsQueueTests(ProfileIsolation, unittest.TestCase):
    """T4 (2026-09-28): the tool-pair seats exist only under that AM4 profile, so the tick switches
    AM4 to tool-pair when tool-lane briefs are queued and nothing else is in flight on AM4. 2026-10-03T05:32Z: it
    used to switch back to dense-tp2 when the queue emptied and pulled the tool seats out from under a caller;
    tool-pair is the resting profile and the tick never returns it."""

    def test_wanted_profile(self) -> None:
        self.assertEqual(lane.am4_profile_wanted(2, [], "dense-tp2"), "tool-pair")
        self.assertIsNone(lane.am4_profile_wanted(2, [], "tool-pair"))
        self.assertIsNone(lane.am4_profile_wanted(2, [{"lane": "tool"}], "dense-tp2"))   # never mid-run
        self.assertIsNone(lane.am4_profile_wanted(0, [], "tool-pair"))   # no switch back to dense-tp2
        self.assertIsNone(lane.am4_profile_wanted(0, [{"lane": "tool"}], "tool-pair"))
        self.assertIsNone(lane.am4_profile_wanted(0, [], "dense-tp2"))
        self.assertIsNone(lane.am4_profile_wanted(2, [], None))   # unreadable profile: do nothing

    def test_tick_switches_only_when_armed_and_records_it(self) -> None:
        import json, tempfile
        from pathlib import Path
        from hearth.backlog.briefs import Brief
        tool_brief = Brief(slug="c", title="t", body="source: /x.py\nbackend: am4-tool-4070ti\n---\ngo", builders=None,
                           task_class="deepagents", est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="c.md")
        with tempfile.TemporaryDirectory() as tmp:
            arm = Path(tmp) / "arm.json"; arm.write_text(json.dumps({"armed": True, "scope": "authored", "in_flight": None}))
            scan = lane.backlog_sources.SourceScan((tool_brief,), ())
            with mock.patch.object(lane.drain, "default_arm_state_path", return_value=arm), \
                 mock.patch.object(lane.drain, "run_tick", return_value={"reason": "no-candidates"}), \
                 mock.patch.object(lane.drain, "_record_tick", return_value=None), \
                 mock.patch.object(lane, "reconcile_slots", return_value=[]), \
                 mock.patch.object(lane, "load_slots", return_value=[]), \
                 mock.patch.object(lane, "candidate_exclusions", return_value=frozenset()), \
                 mock.patch.object(lane, "am4_profile", return_value="dense-tp2"), \
                 mock.patch.object(lane, "am4_switch", return_value={"target": "tool-pair", "rc": 0}) as switch, \
                 mock.patch.object(lane.backlog_sources, "authored_source", return_value=scan), \
                 mock.patch.object(lane.backlog_sources, "refined_source", return_value=lane.backlog_sources.SourceScan((), ())), \
                 mock.patch.object(lane.backlog_sources, "candidate_source", return_value=lane.backlog_sources.SourceScan((), ())), \
                 mock.patch.object(lane.backlog_select, "select_next", return_value=None):
                report = lane.tick()
        switch.assert_called_once_with("tool-pair")
        self.assertEqual(report["am4_profile"]["tool_queued"], 1)
        self.assertEqual(report["am4_profile"]["switch"]["target"], "tool-pair")

    def test_wanted_profile_is_never_dense_tp2(self) -> None:
        """2026-10-03T05:32Z: the tick pulled the tool seats away by switching AM4 to dense-tp2."""
        for queued in (0, 1, 5):
            for ready in (None, 0, 3):
                for slots in ([], [{"lane": "tool"}], [{"lane": "deep"}]):
                    for live in ("tool-pair", "dense-tp2", None, "failed:tool-pair"):
                        self.assertNotEqual(lane.am4_profile_wanted(queued, slots, live, ready), "dense-tp2",
                                            (queued, ready, slots, live))

    def test_reconcile_copies_only_a_known_profile_name(self) -> None:
        """docs/rnd-log.md 2026-10-03T05:50Z: the host file said tool-pair while AM4 served dense-tp2 for 6 minutes,
        so the tick copies AM4's profile to it. Row 2026-09-28 01:55Z: after a failed switch AM4's file reads
        failed:<target> (and am4_profile() is None when ssh fails); neither may reach the host file the gateway
        reads."""
        self.profile_file.write_text("tool-pair\n", encoding="utf-8")
        for live in (None, "failed:dense-tp2", "failed:tool-pair", "garbage", ""):
            with self.subTest(live=live):
                self.assertIsNone(lane.reconcile_omen_profile(live))
                self.assertEqual(self.profile_file.read_text(encoding="utf-8"), "tool-pair\n")
        self.assertIsNone(lane.reconcile_omen_profile("tool-pair"))   # agrees: no rewrite
        self.assertEqual(lane.reconcile_omen_profile("dense-tp2"), {"was": "tool-pair", "now": "dense-tp2"})
        self.assertEqual(self.profile_file.read_text(encoding="utf-8"), "dense-tp2\n")
        self.profile_file.unlink()   # absent file: a known name is written, an unknown one still is not
        self.assertIsNone(lane.reconcile_omen_profile("failed:tool-pair"))
        self.assertFalse(self.profile_file.exists())
        self.assertEqual(lane.reconcile_omen_profile("tool-pair"), {"was": None, "now": "tool-pair"})

    def test_failed_switch_is_not_copied_to_the_host_file(self) -> None:
        """docs/rnd-log.md 2026-09-28 01:55Z (a failed switch leaves failed:<target> on AM4): am4_switch copies only
        a successful target to the OMEN-side file."""
        self.profile_file.write_text("dense-tp2\n", encoding="utf-8")
        ok = mock.Mock(returncode=0, stdout="ok", stderr="")
        bad = mock.Mock(returncode=1, stdout="", stderr="no")
        real_switch = _REAL_AM4_SWITCH
        with mock.patch.object(lane.subprocess, "run", return_value=bad):
            self.assertNotIn("omen_file", real_switch("tool-pair"))
        self.assertEqual(self.profile_file.read_text(encoding="utf-8"), "dense-tp2\n")
        with mock.patch.object(lane.subprocess, "run", return_value=ok):
            self.assertEqual(real_switch("tool-pair")["omen_file"], {"was": "dense-tp2", "now": "tool-pair"})
        self.assertEqual(self.profile_file.read_text(encoding="utf-8"), "tool-pair\n")


# --- the drain's delivery brief and the whole-file default (T2, lap 21 wave 6) ----------------

DELIVERY_BRIEF = {   # the shape of ~/work/delivery-plan/evidence/briefs/restore.brief.v2.json
    "schema": "brief.v2", "builders": ["omen-local-work"], "task_class": "local-work", "est_tokens": 5000,
    "requires": ["candidate"], "max_age_s": 172800,
    "substance": [
        {"id": "s1-restore-steps", "statement": "The report states what restore() does and in what order: which drop-in it removes, when it restarts the seat, and each thing it verifies afterwards."},
        {"id": "s2-signals", "statement": "The report states how SIGTERM or an interrupt received before restore is handled, and that both signals are ignored once restore has begun."},
        {"id": "s3-restore-failure", "statement": "The report states what happens when restore fails: how many attempts are made, what is kept, the outcome that is recorded and the exit code."},
    ],
    "form": {"words": {"min": 0, "max": 400, "enforce": "measure"}, "citations": "quote", "sections": [], "style": "markdown"},
    "sources": [{"path": "x.py", "commit": "0a8d6e26c710e9d3e0e0286efbd1a224459d165a"}],
    "aids": ["source_map", "quote_renderer", "constrained_output"],
}


class GitRepoCase(unittest.TestCase):
    """A temp directory with a temp git repository (small.py 100 bytes, edge.py 23,999, big.py 24,000 bytes) and a
    brief.v2 file; nothing outside the temp directory is read or written."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "small.py").write_text("x = 1\n" * 16, encoding="utf-8")
        (self.repo / "edge.py").write_text("#" * 23_998 + "\n", encoding="utf-8")
        (self.repo / "big.py").write_text("#" * 23_999 + "\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "one file set")
        self.head = self.git("rev-parse", "HEAD")
        self.brief_file = self.tmp / "report.brief.v2.json"
        self.brief_file.write_bytes(json.dumps(DELIVERY_BRIEF, indent=1).encode())
        self.sha = hashlib.sha256(self.brief_file.read_bytes()).hexdigest()
        envp = mock.patch.dict("os.environ", {"HEARTH_ROOT": str(self.tmp / "hearth-root")})
        envp.start()
        self.addCleanup(envp.stop)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def delivery_body(self, **over: str) -> str:
        f = {"repo": str(self.repo), "paths": "[small.py, big.py]", "task_family": "code_review",
             "delivery_brief": str(self.brief_file), "delivery_brief_sha256": self.sha}
        f.update(over)
        head = "\n".join(f"{k}: {v}" for k, v in f.items() if v is not None)
        return head + "\n---\n[goal:G-delivery] Report how restore() works.\n"

    def fix_body(self, **over: str) -> str:
        f = {"repo": str(self.repo), "commit": self.head, "paths": "[small.py]", "task_family": "code_fix"}
        f.update(over)
        return "\n".join(f"{k}: {v}" for k, v in f.items() if v is not None) + "\ncriteria:\n  - utc() still works\n---\nReplace utcnow().\n"


class DeliveryBriefTests(GitRepoCase):
    def test_arguments_come_from_the_brief_file_with_its_statements_as_criteria(self) -> None:
        """rehearsal/RESULT.md: "The drain built the delivery submit from the front block (delivery_brief,
        delivery_brief_sha256, task_family, paths), with the brief's substance statements as criteria"; the door, not the
        drain, chooses the procedure."""
        args = lane.submit_args_from_brief(self.delivery_body(), idempotency_key="bankedfire:slug")
        self.assertEqual(args["acceptance_criteria"], [c["statement"] for c in DELIVERY_BRIEF["substance"]])
        self.assertEqual(args["brief"], DELIVERY_BRIEF)
        self.assertEqual((args["artifact_kind"], args["task_family"], args["lane"], args["deadline_s"]), ("markdown", "code_review", "auto", 2400))
        self.assertEqual((args["files"], args["repo"], args["base_commit"]), (["small.py", "big.py"], str(self.repo), self.head))
        self.assertEqual(args["intent"], "[goal:G-delivery] Report how restore() works.")
        for absent in ("procedure", "max_tokens", "target_path"):
            self.assertNotIn(absent, args)
        self.assertEqual(lane.submit_args_from_brief(self.delivery_body(max_tokens="9000", lane="fast", deadline_s="600"))["max_tokens"], 9000)

    def test_idempotency_key_ends_with_the_first_twelve_hex_of_the_pin(self) -> None:
        """rehearsal/RESULT.md: "the brief's hash in the idempotency key"; docs/delivery.md "Night briefs that deliver"."""
        args = lane.submit_args_from_brief(self.delivery_body(), idempotency_key="bankedfire:slug")
        self.assertEqual(args["idempotency_key"], f"bankedfire:slug:{self.sha[:12]}")
        self.assertNotIn("idempotency_key", lane.submit_args_from_brief(self.delivery_body()))

    def test_a_changed_brief_file_refuses_the_dispatch(self) -> None:
        """rehearsal queue files pin the JSON's bytes (delivery_brief_sha256); a file that no longer matches must not be
        submitted, and the drain's hook must report it without calling the door."""
        self.brief_file.write_bytes(self.brief_file.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "changed since the brief was written"):
            lane.submit_args_from_brief(self.delivery_body())
        with mock.patch.object(lane, "call_tool") as door:
            result = lane.submit_task(prompt=self.delivery_body(), plan_id_hint="slug")
        self.assertFalse(result["ok"])
        self.assertIn("changed since", result["error"])
        door.assert_not_called()

    def test_a_missing_or_malformed_pin_is_refused(self) -> None:
        """docs/delivery.md: `delivery_brief_sha256` is required; criteria must be absent; task_family is required."""
        with self.assertRaisesRegex(ValueError, "lacks 'delivery_brief_sha256'"):
            lane.parse_local_work_block(self.delivery_body(delivery_brief_sha256=None))
        with self.assertRaisesRegex(ValueError, "64 lowercase hex"):
            lane.parse_local_work_block(self.delivery_body(delivery_brief_sha256=self.sha[:12]))
        with self.assertRaisesRegex(ValueError, "lacks 'task_family'"):
            lane.parse_local_work_block(self.delivery_body(task_family=None))
        with self.assertRaisesRegex(ValueError, "names delivery_brief and criteria"):
            lane.parse_local_work_block(self.delivery_body().replace("\n---\n", "\ncriteria:\n  - mine\n---\n"))
        with self.assertRaisesRegex(ValueError, "is not a file"):
            lane.submit_args_from_brief(self.delivery_body(delivery_brief=str(self.tmp / "gone.json")))

    def test_a_relative_brief_path_resolves_against_the_repo(self) -> None:
        """docs/delivery.md: `delivery_brief` is absolute or relative to `repo`."""
        (self.repo / "b.json").write_bytes(self.brief_file.read_bytes())
        args = lane.submit_args_from_brief(self.delivery_body(delivery_brief="b.json"))
        self.assertEqual(args["brief"], DELIVERY_BRIEF)

    def test_a_brief_without_delivery_brief_is_unchanged(self) -> None:
        """BriefBlockTests / the pre-delivery drain: criteria as written, unified_diff by default for two paths, no brief
        and no key suffix."""
        body = self.fix_body(paths="[small.py, big.py]")
        args = lane.submit_args_from_brief(body, idempotency_key="k")
        self.assertEqual(args, {
            "intent": "Replace utcnow().", "acceptance_criteria": ["utc() still works"], "repo": str(self.repo),
            "base_commit": self.head, "files": ["small.py", "big.py"], "artifact_kind": "unified_diff", "lane": "auto",
            "task_family": "code_fix", "deadline_s": 2400, "idempotency_key": "k"})

    def test_submit_task_sends_the_delivery_args_to_the_door(self) -> None:
        """rehearsal/RESULT.md (07:24:44Z tick): the drain's submit hook carried the pinned brief to submit_local_work."""
        with mock.patch.object(lane, "call_tool", return_value={"work_id": "work_" + "b" * 32, "status": "queued", "route": {}}) as door:
            result = lane.submit_task(prompt=self.delivery_body(), plan_id_hint="slug")
        tool, args = door.call_args.args
        self.assertEqual(tool, "submit_local_work")
        self.assertEqual((args["brief"], args["idempotency_key"]), (DELIVERY_BRIEF, f"bankedfire:slug:{self.sha[:12]}"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["work_id"], "work_" + "b" * 32)

    def test_a_delivery_brief_counts_against_the_deep_lane_unless_it_names_fast(self) -> None:
        """rehearsal/RESULT.md finding 4: "All four briefs counted against the drain's deep lane" (lane auto = the scarcer
        lane)."""
        from hearth.backlog.briefs import Brief
        mk = lambda body: Brief(slug="s", title="t", body=body, builders=None, task_class="local-work",  # noqa: E731
                                est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="s.md")
        self.assertEqual(lane.brief_lane(mk(self.delivery_body())), "deep")
        self.assertEqual(lane.brief_lane(mk(self.delivery_body(lane="fast"))), "fast")
        self.assertEqual(lane.brief_lane(mk("not a brief")), "deep")

    def test_an_items_delivery_brief_counts_against_the_fast_lane(self) -> None:
        """2026-10-04T11:58Z, Wave 5 of lap 21: three inventory briefs counted against deep=1 while the door seated them
        on fast (hearth/localwork/service.py: an items run takes the fast lane)."""
        from hearth.backlog.briefs import Brief
        mk = lambda body: Brief(slug="s", title="t", body=body, builders=None, task_class="local-work",  # noqa: E731
                                est_tokens=None, requires=(), max_age_s=None, source="authored", source_ref="s.md")
        items = self.tmp / "items.brief.v2.json"
        items.write_bytes(json.dumps({**DELIVERY_BRIEF, "items": {"kind": "env_reads"}}).encode())
        sha = hashlib.sha256(items.read_bytes()).hexdigest()
        body = lambda **o: self.delivery_body(**{"delivery_brief": str(items), "delivery_brief_sha256": sha, **o})  # noqa: E731
        self.assertEqual(lane.brief_lane(mk(body())), "fast")
        self.assertEqual(lane.brief_lane(mk(body(lane="deep"))), "deep")
        self.assertEqual(lane.brief_lane(mk(self.delivery_body())), "deep")   # no items key
        self.assertEqual(lane.brief_lane(mk(body(delivery_brief_sha256="0" * 64))), "deep")   # pin mismatch: submit refuses

    def test_dry_run_previews_the_delivery_args_without_the_intent(self) -> None:
        """The drain's preview (dry_run) of a queued delivery brief shows the submit args, minus the intent, and touches
        neither the door nor the host: arm file, queue and slots live in the temp root."""
        root = self.tmp / "hearth-root"
        (root / "var").mkdir(parents=True)
        arm = root / "var" / drain_arm_name()
        arm.write_text(json.dumps({"contract_version": "bankedfire-drain-arm.v2", "armed": True, "scope": "authored",
                                   "authored_by": "t", "reason": "t", "updated": None, "in_flight": None}), encoding="utf-8")
        queued = root / "var" / "backlog" / "queued"
        queued.mkdir(parents=True)
        (queued / "rehearsal.md").write_text('<!-- CCMETA\n{"builders": ["omen-local-work"], "task_class": "local-work", '
                                             '"est_tokens": 5000, "requires": ["candidate"], "max_age_s": 172800}\n-->\n'
                                             + self.delivery_body(), encoding="utf-8")
        nothing = self.tmp / "nothing"
        with mock.patch.object(lane.backlog_sources, "DEFAULT_QUEUED_DIR", queued), \
             mock.patch.object(lane.backlog_sources, "DEFAULT_REFINE_DIR", nothing), \
             mock.patch.object(lane.backlog_sources, "DEFAULT_CANDIDATE_WORTH_PATH", nothing / "w.json"), \
             mock.patch.object(lane.backlog_sources, "DEFAULT_EXPERIMENT_RESULTS_PATH", nothing / "r.json"), \
             mock.patch.object(lane, "candidate_exclusions", return_value=frozenset()), \
             mock.patch.object(lane, "queue_status", return_value={"ok": True, "queued": 0, "running": 0, "done": None}), \
             mock.patch.object(lane.occ_mod, "check_occupancy", return_value={"occupancy": "idle"}), \
             mock.patch.dict("os.environ", {"BANKEDFIRE_SLOTS": "fast=3,deep=1"}), \
             mock.patch.object(lane, "call_tool") as door:
            report = lane.dry_run()
        door.assert_not_called()
        self.assertEqual(report["scope"], "authored")
        self.assertNotIn("brief_error", report["next"])
        sub = report["next"]["submit_args"]
        self.assertEqual((sub["brief"], sub["artifact_kind"], sub["task_family"]), (DELIVERY_BRIEF, "markdown", "code_review"))
        self.assertNotIn("intent", sub)
        self.assertEqual(report["pick"]["lane"], "deep")


def drain_arm_name() -> str:
    from fleet import bankedfire_drain
    return bankedfire_drain.ARM_STATE_FILENAME


class WholeFileDefaultTests(GitRepoCase):
    def test_a_small_code_fix_on_one_file_defaults_to_whole_file(self) -> None:
        """rehearsal/RESULT.md: `work_2e663a07` as unified_diff failed `git apply --check` twice; the same change as
        whole_file (`work_4f36d04f`) came back in two minutes. M1: one path, no kind named, under 24,000 bytes."""
        args = lane.submit_args_from_brief(self.fix_body())
        self.assertEqual((args["artifact_kind"], args["target_path"], args["max_tokens"]), ("whole_file", "small.py", 12000))

    def test_the_size_boundary_is_24000_bytes_at_the_commit(self) -> None:
        """M1: "under 24,000 bytes": 23,999 is whole_file, 24,000 stays unified_diff with no target_path."""
        edge = lane.submit_args_from_brief(self.fix_body(paths="[edge.py]"))
        self.assertEqual((edge["artifact_kind"], edge["target_path"]), ("whole_file", "edge.py"))
        big = lane.submit_args_from_brief(self.fix_body(paths="[big.py]"))
        self.assertEqual(big["artifact_kind"], "unified_diff")
        self.assertNotIn("target_path", big)
        self.assertNotIn("max_tokens", big)

    def test_the_default_is_sized_at_the_commit_not_the_working_tree(self) -> None:
        """The brief pins a commit: a file that has since grown past the limit is still sized at the pinned commit."""
        (self.repo / "small.py").write_text("#" * 30_000, encoding="utf-8")
        self.assertEqual(lane.submit_args_from_brief(self.fix_body())["artifact_kind"], "whole_file")

    def test_a_named_kind_is_never_overridden(self) -> None:
        """M1: "a named kind is never overridden": a small code_fix that names unified_diff stays unified_diff, a named
        max_tokens and target_path win."""
        args = lane.submit_args_from_brief(self.fix_body(artifact_kind="unified_diff"))
        self.assertEqual(args["artifact_kind"], "unified_diff")
        self.assertNotIn("target_path", args)
        self.assertNotIn("max_tokens", args)
        named = lane.submit_args_from_brief(self.fix_body(artifact_kind="whole_file", target_path="small.py", max_tokens="5000"))
        self.assertEqual((named["artifact_kind"], named["target_path"], named["max_tokens"]), ("whole_file", "small.py", 5000))
        self.assertEqual(lane.submit_args_from_brief(self.fix_body(max_tokens="5000"))["max_tokens"], 5000)

    def test_only_a_one_path_code_fix_gets_the_default(self) -> None:
        """M1 scope: other families and several paths keep unified_diff."""
        self.assertEqual(lane.submit_args_from_brief(self.fix_body(task_family="code_review"))["artifact_kind"], "unified_diff")
        self.assertEqual(lane.submit_args_from_brief(self.fix_body(paths="[small.py, big.py]"))["artifact_kind"], "unified_diff")
        self.assertEqual(lane.submit_args_from_brief(self.fix_body(task_family=None))["artifact_kind"], "whole_file")   # family defaults to code_fix

    def test_an_unsizable_file_fails_loudly(self) -> None:
        """No silent fallback (H-2): a path absent at the commit raises, naming the path."""
        with self.assertRaisesRegex(ValueError, "cannot size 'gone.py'"):
            lane.submit_args_from_brief(self.fix_body(paths="[gone.py]"))
        result = lane.submit_task(prompt=self.fix_body(paths="[gone.py]"), plan_id_hint="x")
        self.assertFalse(result["ok"])
