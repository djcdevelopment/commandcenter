from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from hearth.health.gaps import (PHANTOM_AGE_S, Gap, scan_knowledge, scan_rung_state, scan_runs,
                                summarize)
from hearth.health.rungstate import NOTE


def _kinds(gaps):
    return sorted(g.kind for g in gaps)


class ScanRunsTests(TestCase):
    def test_clean_completed_run_has_no_gaps(self):
        rec = {"plan_id": "pour-x", "age_s": 4000, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "promoted": True,
               "n_questions": 0, "questions_text": "", "winner_files": 205,
               "winner_grade": "B"}
        self.assertEqual(scan_runs([rec]), [])

    def test_phantom_in_flight_fires_only_after_threshold(self):
        young = {"plan_id": "hearth-young", "age_s": 60, "has_result": False}
        old = {"plan_id": "hearth-old", "age_s": PHANTOM_AGE_S + 1, "has_result": False}
        self.assertEqual(scan_runs([young]), [])
        gaps = scan_runs([old])
        self.assertEqual(_kinds(gaps), ["phantom_in_flight"])
        self.assertEqual(gaps[0].severity, "warn")
        self.assertEqual(gaps[0].plan_id, "hearth-old")

    def test_undispatched_phantom_fires_and_names_the_stage(self):
        # A run dir the conductor made but never pinned a builder graph for
        # (its "no build workers available" abort) is still a phantom — same
        # kind, same heal; the detail says which stage it died at (ADR-0033).
        rec = {"plan_id": "spine-hello", "age_s": PHANTOM_AGE_S + 1,
               "dispatched": False, "has_result": False}
        gaps = scan_runs([rec])
        self.assertEqual(_kinds(gaps), ["phantom_in_flight"])
        self.assertIn("never dispatched", gaps[0].detail)

    def test_dispatched_phantom_keeps_the_stalled_wording(self):
        rec = {"plan_id": "hearth-x", "age_s": PHANTOM_AGE_S + 1,
               "dispatched": True, "has_result": False}
        gaps = scan_runs([rec])
        self.assertIn("stalled/errored", gaps[0].detail)
        # A record with no `dispatched` key (older payload) keeps the old wording.
        legacy = scan_runs([{"plan_id": "y", "age_s": PHANTOM_AGE_S + 1, "has_result": False}])
        self.assertIn("stalled/errored", legacy[0].detail)

    def test_crashed_isolated_from_stub_and_from_error(self):
        stub = {"plan_id": "a", "age_s": 10, "has_result": True, "stub": True,
                "status": "errored", "error": "FanOutEdgeGroup ..."}
        errd = {"plan_id": "b", "age_s": 10, "has_result": True,
                "error": "workflow errored (isolated): boom"}
        self.assertIn("crashed_isolated", _kinds(scan_runs([stub])))
        gaps = scan_runs([errd])
        self.assertEqual(gaps[0].kind, "crashed_isolated")
        self.assertEqual(gaps[0].severity, "high")

    def test_stale_checkout_detected_from_question_text(self):
        rec = {"plan_id": "hearth-retro", "age_s": 10, "has_result": True,
               "winner": "am4-worker-1", "promoted": False, "n_questions": 1,
               "questions_text": "The hearth directory does not exist in the checkout."}
        gaps = scan_runs([rec])
        # stale_checkout wins over the generic false_success(blocked) branch.
        self.assertIn("stale_checkout", _kinds(gaps))
        self.assertNotIn("false_success", [g.kind for g in gaps if g.severity == "warn" and "pending" in g.detail])
        self.assertEqual([g for g in gaps if g.kind == "stale_checkout"][0].severity, "high")

    def test_false_success_when_graded_pass_but_pending_questions(self):
        rec = {"plan_id": "c", "age_s": 10, "has_result": True,
               "winner": "cc-builder-2", "winner_grade": "B", "promoted": False,
               "n_questions": 2, "questions_text": "please clarify the scope"}
        gaps = scan_runs([rec])
        self.assertIn("false_success", _kinds(gaps))
        self.assertTrue(any("pending" in g.detail for g in gaps))

    def test_false_success_when_winner_produced_no_files(self):
        rec = {"plan_id": "d", "age_s": 10, "has_result": True,
               "winner": "cc-builder-2", "winner_grade": "B", "promoted": True,
               "n_questions": 0, "questions_text": "", "winner_files": 0}
        gaps = scan_runs([rec])
        self.assertIn("false_success", _kinds(gaps))
        self.assertTrue(any("empty deliverable" in g.detail for g in gaps))

    def test_watchfire_heal_stub_is_resolved_not_a_fresh_crash(self):
        # A healed phantom (status "abandoned") must produce NO gap — a heal
        # resolves, it must not re-flag as crashed_isolated just because it's a stub.
        healed = {"plan_id": "soak-x", "age_s": 10, "has_result": True,
                  "status": "abandoned", "stub": True, "_stub_reason": "watchfire-phantom-heal",
                  "error": "auto-healed by watchfire: phantom_in_flight - occupancy released",
                  "winner": None, "n_questions": 0, "questions_text": ""}
        self.assertEqual(scan_runs([healed]), [])

    def test_schedule_divergence_fires_when_actual_far_exceeds_p90(self):
        capacity = {"contract_version": "capacity.v1", "buckets": [
            {"task_class": "build", "node": "am4-worker-1", "tool": "submit_task",
             "calls": 20, "duration_ms": {"p50": 60000, "p90": 120000}},
        ]}
        rec = {"plan_id": "js6-slow", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 300}  # 300_000ms > 2x120_000ms
        gaps = scan_runs([rec], capacity=capacity)
        div = [g for g in gaps if g.kind == "schedule_divergence"]
        self.assertEqual(len(div), 1)
        self.assertEqual(div[0].severity, "info")
        self.assertIn("300000ms", div[0].detail)
        self.assertIn("120000ms", div[0].detail)

    def test_schedule_divergence_silent_when_within_envelope(self):
        capacity = {"contract_version": "capacity.v1", "buckets": [
            {"task_class": "build", "node": "am4-worker-1", "tool": "submit_task",
             "calls": 20, "duration_ms": {"p50": 60000, "p90": 120000}},
        ]}
        rec = {"plan_id": "js6-normal", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 100}  # well under p90
        gaps = scan_runs([rec], capacity=capacity)
        self.assertNotIn("schedule_divergence", _kinds(gaps))

    def test_schedule_divergence_boundary_exactly_2x_does_not_fire(self):
        capacity = {"contract_version": "capacity.v1", "buckets": [
            {"task_class": "build", "node": "am4-worker-1", "tool": "submit_task",
             "calls": 20, "duration_ms": {"p50": 60000, "p90": 120000}},
        ]}
        rec = {"plan_id": "js6-boundary", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 240}  # exactly 2x120_000ms == 240_000ms
        gaps = scan_runs([rec], capacity=capacity)
        self.assertNotIn("schedule_divergence", _kinds(gaps))

    def test_schedule_divergence_missing_capacity_document_is_a_silent_noop(self):
        rec = {"plan_id": "js6-nocap", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 999999}
        gaps = scan_runs([rec], capacity=None)
        self.assertNotIn("schedule_divergence", _kinds(gaps))
        gaps2 = scan_runs([rec], capacity={})
        self.assertNotIn("schedule_divergence", _kinds(gaps2))

    def test_schedule_divergence_missing_matching_bucket_is_a_silent_noop(self):
        capacity = {"contract_version": "capacity.v1", "buckets": [
            {"task_class": "other_class", "node": "some-other-node",
             "tool": "other_tool", "calls": 5, "duration_ms": {"p90": 1000}},
        ]}
        rec = {"plan_id": "js6-nobucket", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 999999}
        gaps = scan_runs([rec], capacity=capacity)
        self.assertNotIn("schedule_divergence", _kinds(gaps))

    def test_schedule_divergence_null_p90_is_skipped(self):
        # When a matching bucket has null p90 (all events were failures),
        # it should not match — the spell stays silent. This is "no evidence either way".
        capacity = {"contract_version": "capacity.v1", "buckets": [
            {"task_class": "build", "node": "am4-worker-1", "tool": "submit_task",
             "calls": 20, "ok_rate": 0.0,
             "duration_ms": {"p50": None, "p90": None, "mean": None, "max": None}},
        ]}
        rec = {"plan_id": "js6-null-p90", "age_s": 10, "has_result": True,
               "status": "ok", "winner": "am4-worker-1", "task_class": "build",
               "promoted": True, "n_questions": 0, "questions_text": "",
               "winner_files": 20, "winner_grade": "A",
               "duration_s": 999999}  # far over any real p90, but null p90 doesn't match
        gaps = scan_runs([rec], capacity=capacity)
        # No gap should fire: bucket matches but p90 is null, so it doesn't count
        self.assertNotIn("schedule_divergence", _kinds(gaps))

    def test_summarize_counts_by_severity_and_kind(self):
        gaps = [Gap("phantom_in_flight", "warn", "a", "x"),
                Gap("crashed_isolated", "high", "b", "y"),
                Gap("crashed_isolated", "high", "c", "z")]
        s = summarize(gaps)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_severity"], {"warn": 1, "high": 2})
        self.assertEqual(s["by_kind"], {"phantom_in_flight": 1, "crashed_isolated": 2})


class ScanKnowledgeTests(TestCase):
    @patch("hearth.toolsurface._scope.resolve_in_scope")
    def test_knowledge_stale_fires_when_capacity_is_old(self, mock_resolve):
        import os
        import tempfile
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            cap_path = Path(tmpdir) / "capacity.json"
            cap_path.write_text("{}")
            old_time = time.time() - 86405
            os.utime(cap_path, (old_time, old_time))
            mock_resolve.return_value = cap_path

            gaps = scan_knowledge("fake/path")
            self.assertEqual(_kinds(gaps), ["knowledge_stale"])
            self.assertEqual(gaps[0].severity, "warn")
            self.assertIn("stale (24h old)", gaps[0].detail)

    @patch("hearth.toolsurface._scope.resolve_in_scope")
    def test_knowledge_stale_fires_when_capacity_is_missing(self, mock_resolve):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            cap_path = Path(tmpdir) / "capacity.json"
            mock_resolve.return_value = cap_path

            gaps = scan_knowledge("fake/path")
            self.assertEqual(_kinds(gaps), ["knowledge_stale"])
            self.assertEqual(gaps[0].severity, "warn")
            self.assertIn("missing", gaps[0].detail)

    @patch("hearth.toolsurface._scope.resolve_in_scope")
    def test_knowledge_stale_silent_when_capacity_is_fresh(self, mock_resolve):
        import os
        import tempfile
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            cap_path = Path(tmpdir) / "capacity.json"
            cap_path.write_text("{}")
            fresh_time = time.time() - 3600  # 1h old
            os.utime(cap_path, (fresh_time, fresh_time))
            mock_resolve.return_value = cap_path

            self.assertEqual(scan_knowledge("fake/path"), [])


class ScanRungStateTests(TestCase):
    """The rung-state spell (ADR-0044): verdict -> gap kind/severity, plan_id = rung.

    Fixtures are the dict shape ``hearth.health.rungstate.rung_state`` returns;
    the spell is pure, so no keep-alive file is read here.
    """

    @staticmethod
    def _state(verdict, **over):
        st = {"rung": "omen-arc", "port": 8082, "verdict": verdict,
              "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
              "envelope": {"fail_below": 0.8, "warn_below": 0.9},
              "observed_tok_s": 65.0, "observed_at": "2026-09-03T02:28:20-07:00",
              "observed_age_s": 100.0, "frac_of_baseline": round(65.0 / 106.0, 4),
              "prefill_stall_recent": False, "last_ping_ok": True, "deep_samples": 3,
              "excluded_windows": [], "note": NOTE}
        st.update(over)
        return st

    def test_correct_but_degraded_is_a_high_gap(self):
        # The lab's failure mode: pings answer, the deep probe decodes at 65 of 106.
        gaps = scan_rung_state(self._state("degraded"))
        self.assertEqual(_kinds(gaps), ["rung_degraded"])
        g = gaps[0]
        self.assertEqual(g.severity, "high")
        self.assertEqual(g.plan_id, "omen-arc")
        self.assertIn("65.0/106.0 tok/s", g.detail)
        self.assertIn("61% of epoch", g.detail)

    def test_stalled_is_a_high_gap(self):
        gaps = scan_rung_state(self._state("stalled", prefill_stall_recent=True))
        self.assertEqual([(g.kind, g.severity) for g in gaps], [("rung_stalled", "high")])

    def test_warn_band_is_a_warn_gap(self):
        gaps = scan_rung_state(self._state("warn", observed_tok_s=92.0,
                                           frac_of_baseline=round(92 / 106, 4)))
        self.assertEqual([(g.kind, g.severity) for g in gaps], [("rung_warn", "warn")])

    def test_liveness_as_health_is_a_warn_gap(self):
        # Pings fine, no deep sample for 20 min: stale is a gap — the rung is
        # unmeasured, and unmeasured must never read as at_rate.
        gaps = scan_rung_state(self._state("stale", observed_age_s=1200.0))
        self.assertEqual([(g.kind, g.severity) for g in gaps], [("rung_stale", "warn")])
        self.assertIn("age 1200s", gaps[0].detail)

    def test_at_rate_is_no_gap(self):
        self.assertEqual(scan_rung_state(self._state("at_rate", observed_tok_s=107.5,
                                                     frac_of_baseline=1.0142)), [])

    def test_unreachable_is_liveness_not_a_coherence_gap(self):
        # The watchdog's inventory probe (omen/llama-server :8082) owns "down";
        # the spell must not double-report it under a coherence name.
        self.assertEqual(scan_rung_state(self._state("unreachable", last_ping_ok=False)), [])

    def test_no_baseline_and_unknown_are_silent(self):
        self.assertEqual(scan_rung_state(self._state("no_baseline", baseline_tok_s=None)), [])
        self.assertEqual(scan_rung_state(self._state("unknown")), [])
        self.assertEqual(scan_rung_state({"verdict": "unknown", "error": "OSError: x"}), [])

    def test_non_dict_state_is_silent(self):
        self.assertEqual(scan_rung_state(None), [])
        self.assertEqual(scan_rung_state("degraded"), [])
        self.assertEqual(scan_rung_state([]), [])

    def test_plan_id_follows_the_rung_and_defaults_to_omen_arc(self):
        gaps = scan_rung_state(self._state("degraded", rung="omen-arc-27b", port=8084))
        self.assertEqual(gaps[0].plan_id, "omen-arc-27b")
        gaps = scan_rung_state(self._state("degraded", rung=None))
        self.assertEqual(gaps[0].plan_id, "omen-arc")

    def test_detail_repeats_the_epoch_note_and_names_no_regime(self):
        g = scan_rung_state(self._state("degraded"))[0]
        self.assertIn("not of capacity", g.detail)
        self.assertIn("restart discriminator not applied", g.detail)
        low = g.detail.lower()
        for regime in ("cold", "warm", "thermal", "throttl", "idle-degraded"):
            self.assertNotIn(regime, low, g.detail)

    def test_window_exclusion_is_named_in_the_detail(self):
        g = scan_rung_state(self._state("degraded", excluded_windows=["cutover-0429"]))[0]
        self.assertIn("excl cutover-0429", g.detail)

    def test_rung_gaps_count_in_summarize(self):
        gaps = scan_rung_state(self._state("degraded")) + scan_rung_state(self._state("stale"))
        s = summarize(gaps)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["by_severity"], {"high": 1, "warn": 1})
        self.assertEqual(s["by_kind"], {"rung_degraded": 1, "rung_stale": 1})
