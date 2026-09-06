from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from hearth.health.gaps import (PHANTOM_AGE_S, Gap, apply_expectations, phantom_threshold_s,
                                scan_knowledge, scan_rung_state, scan_runs, spared_as_dicts,
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


class LongRunSparingTests(TestCase):
    """The four-quadrant proof: "long and alive" must be distinguishable from
    "dead", and the distinction must be VISIBLE.

    The bug this closes: masters_pet(apply=True) stubs any run older than 30 min
    with no result.json, so a deliberate multi-hour build was indistinguishable
    from a crashed one and had its occupancy released out from under it.
    """

    _SIX_HOURS = 21600

    def _rec(self, plan_id, age_s, **over):
        rec = {"plan_id": plan_id, "age_s": age_s, "has_result": False, "dispatched": True}
        rec.update(over)
        return rec

    # (a) long-lived and still inside its declared lifetime -> not a gap
    def test_a_run_inside_its_declared_lifetime_is_not_a_phantom(self):
        rec = self._rec("long-alive", 3 * 3600, max_age_s=self._SIX_HOURS)
        self.assertEqual(scan_runs([rec]), [])
        applied = apply_expectations([rec], {})
        self.assertEqual(applied[0]["spared_by"], "max_age_s")
        self.assertEqual(applied[0]["phantom_threshold_s"], self._SIX_HOURS)
        self.assertIn("360 min", applied[0]["spared_detail"])

    # (b) past its declared lifetime with no activity -> phantom, threshold named
    def test_b_run_past_its_declared_lifetime_is_a_phantom_naming_the_threshold(self):
        rec = self._rec("long-dead", 7 * 3600, max_age_s=self._SIX_HOURS)
        gaps = scan_runs([rec])
        self.assertEqual(_kinds(gaps), ["phantom_in_flight"])
        # The detail names the threshold actually exceeded (6 h), not the flat
        # 30-minute default it replaced.
        self.assertIn("over the 360 min threshold", gaps[0].detail)
        self.assertIn("no result after 420 min", gaps[0].detail)
        self.assertNotIn("spared_by", apply_expectations([rec], {})[0])

    # (c) no expectation at all, but the run dir is being written -> not a gap
    def test_c_recent_file_activity_spares_a_run_with_no_expectation(self):
        rec = self._rec("busy", 2 * 3600, last_activity_s=60)
        self.assertEqual(scan_runs([rec]), [])
        applied = apply_expectations([rec], {})
        self.assertEqual(applied[0]["spared_by"], "activity")
        self.assertIn("60s ago", applied[0]["spared_detail"])

    # (d) no expectation, no activity -> today's behaviour, unchanged
    def test_d_stale_activity_still_reads_as_a_phantom(self):
        rec = self._rec("stale", 2 * 3600, last_activity_s=7200)
        gaps = scan_runs([rec])
        self.assertEqual(_kinds(gaps), ["phantom_in_flight"])
        self.assertIn(f"over the {PHANTOM_AGE_S // 60} min threshold", gaps[0].detail)

    # (e) below the flat threshold -> not a gap and NOT "spared"
    def test_e_young_run_is_not_a_gap_and_is_not_reported_as_spared(self):
        rec = self._rec("young", 1200)
        self.assertEqual(scan_runs([rec]), [])
        applied = apply_expectations([rec], {})
        self.assertNotIn("spared_by", applied[0])
        self.assertEqual(applied[0]["phantom_threshold_s"], PHANTOM_AGE_S)

    def test_activity_spares_a_run_that_has_overrun_its_declared_lifetime(self):
        # A heartbeat is evidence about the present; an estimate is not. A run
        # past its own max_age_s but still writing files is alive.
        rec = self._rec("overrun-but-writing", 7 * 3600,
                        max_age_s=self._SIX_HOURS, last_activity_s=30)
        self.assertEqual(scan_runs([rec]), [])
        self.assertEqual(apply_expectations([rec], {})[0]["spared_by"], "activity")

    def test_an_expectation_can_only_raise_the_threshold_never_lower_it(self):
        # A 5-minute max_age_s must not make the watchdog stub healthy runs.
        rec = self._rec("short-claim", 1200, max_age_s=300)
        self.assertEqual(scan_runs([rec]), [])
        self.assertEqual(phantom_threshold_s(rec), PHANTOM_AGE_S)

    def test_nonsense_max_age_values_fall_back_to_the_flat_threshold(self):
        for bad in (0, -5, "6h", True, None, 12.5):
            with self.subTest(bad=bad):
                rec = self._rec("junk", PHANTOM_AGE_S + 1, max_age_s=bad)
                self.assertEqual(phantom_threshold_s(rec), PHANTOM_AGE_S)
                self.assertEqual(_kinds(scan_runs([rec])), ["phantom_in_flight"])

    def test_nonsense_activity_values_do_not_spare(self):
        for bad in ("just now", True, None, [1]):
            with self.subTest(bad=bad):
                rec = self._rec("junk", PHANTOM_AGE_S + 1, last_activity_s=bad)
                self.assertEqual(_kinds(scan_runs([rec])), ["phantom_in_flight"])

    def test_caller_supplied_phantom_age_still_governs_the_floor(self):
        rec = self._rec("x", 400, last_activity_s=100)
        self.assertEqual(_kinds(scan_runs([rec], phantom_age_s=300)), [])  # activity spares
        self.assertEqual(_kinds(scan_runs([{"plan_id": "y", "age_s": 400,
                                            "has_result": False}], phantom_age_s=300)),
                         ["phantom_in_flight"])


class ApplyExpectationsTests(TestCase):
    def _rec(self, plan_id="p", age_s=7200, **over):
        rec = {"plan_id": plan_id, "age_s": age_s, "has_result": False}
        rec.update(over)
        return rec

    def test_hearth_sidecar_supplies_max_age_when_the_run_has_none(self):
        applied = apply_expectations([self._rec()], {"p": {"max_age_s": 21600}})
        self.assertEqual(applied[0]["effective_max_age_s"], 21600)
        self.assertEqual(applied[0]["expectation_source"], "hearth")
        self.assertEqual(applied[0]["spared_by"], "max_age_s")
        self.assertNotIn("expectation_conflict", applied[0])
        # The resolved value never overwrites the run's own (absent) key.
        self.assertNotIn("max_age_s", applied[0])

    def test_run_attached_value_wins_and_the_disagreement_is_flagged(self):
        # Precedence: the run's own value beats HEARTH's memory of the submit,
        # and the disagreement is surfaced rather than silently resolved.
        rec = self._rec(max_age_s=7200)
        applied = apply_expectations([rec], {"p": {"max_age_s": 3600}})
        self.assertEqual(applied[0]["effective_max_age_s"], 7200)
        self.assertEqual(applied[0]["max_age_s"], 7200, "the run's own claim is preserved")
        self.assertTrue(applied[0]["expectation_conflict"])
        self.assertEqual(applied[0]["max_age_s_run"], 7200)
        self.assertEqual(applied[0]["max_age_s_hearth"], 3600)
        self.assertEqual(applied[0]["expectation_source"], "run")
        self.assertEqual(applied[0]["phantom_threshold_s"], 7200)

    def test_a_hearth_only_expectation_stays_hearth_sourced_when_reapplied(self):
        # The idempotency trap this design avoids: writing the resolved value
        # over `max_age_s` would make the SECOND pass read HEARTH's own
        # annotation as a claim the run made, flipping the provenance to "run"
        # and hiding any later genuine conflict.
        expectations = {"p": {"max_age_s": 21600}}
        once = apply_expectations([self._rec()], expectations)
        twice = apply_expectations(once, expectations)
        self.assertEqual(twice[0]["expectation_source"], "hearth")
        self.assertEqual(once, twice)

    def test_agreeing_values_are_not_flagged_as_a_conflict(self):
        applied = apply_expectations([self._rec(max_age_s=3600)], {"p": {"max_age_s": 3600}})
        self.assertNotIn("expectation_conflict", applied[0])
        self.assertEqual(applied[0]["expectation_source"], "run")

    def test_requires_follows_the_same_precedence_and_conflict_rule(self):
        rec = self._rec(requires=["out/a.json"])
        applied = apply_expectations([rec], {"p": {"requires": ["out/b.json"]}})
        self.assertEqual(applied[0]["effective_requires"], ["out/a.json"])
        self.assertTrue(applied[0]["expectation_conflict"])
        self.assertEqual(applied[0]["requires_run"], ["out/a.json"])
        self.assertEqual(applied[0]["requires_hearth"], ["out/b.json"])

    def test_inputs_are_never_mutated(self):
        import json as _json
        records = [self._rec()]
        expectations = {"p": {"max_age_s": 21600}}
        rec_snapshot = _json.loads(_json.dumps(records))
        exp_snapshot = _json.loads(_json.dumps(expectations))
        applied = apply_expectations(records, expectations)
        self.assertEqual(records, rec_snapshot)
        self.assertEqual(expectations, exp_snapshot)
        self.assertIsNot(applied[0], records[0])

    def test_applying_twice_is_idempotent(self):
        records = [self._rec(), self._rec("q", 100),
                   {"plan_id": "done", "age_s": 5, "has_result": True, "status": "ok"}]
        expectations = {"p": {"max_age_s": 21600}}
        once = apply_expectations(records, expectations)
        twice = apply_expectations(once, expectations)
        self.assertEqual(once, twice)

    def test_finished_records_pass_through_unannotated(self):
        rec = {"plan_id": "done", "age_s": 99999, "has_result": True, "status": "ok"}
        applied = apply_expectations([rec], {"done": {"max_age_s": 60}})
        self.assertEqual(applied[0], rec)
        self.assertIsNot(applied[0], rec)

    def test_empty_expectations_still_annotate_the_threshold(self):
        applied = apply_expectations([self._rec()], {})
        self.assertEqual(applied[0]["phantom_threshold_s"], PHANTOM_AGE_S)
        self.assertNotIn("expectation_source", applied[0])

    def test_malformed_expectation_entry_is_ignored(self):
        applied = apply_expectations([self._rec()], {"p": "not a dict"})
        self.assertNotIn("expectation_source", applied[0])
        self.assertEqual(applied[0]["phantom_threshold_s"], PHANTOM_AGE_S)

    def test_non_dict_records_are_skipped(self):
        self.assertEqual(apply_expectations(["junk", None, self._rec()], {})[0]["plan_id"], "p")
        self.assertEqual(len(apply_expectations(["junk", None], {})), 0)

    def test_spared_as_dicts_reports_plan_id_rule_and_detail(self):
        records = [self._rec("spared-a", 3 * 3600, max_age_s=21600),
                   self._rec("spared-c", 2 * 3600, last_activity_s=60),
                   self._rec("phantom", 2 * 3600),
                   {"plan_id": "done", "age_s": 5, "has_result": True}]
        spared = spared_as_dicts(apply_expectations(records, {}))
        self.assertEqual([s["plan_id"] for s in spared], ["spared-a", "spared-c"])
        self.assertEqual([s["rule"] for s in spared], ["max_age_s", "activity"])
        self.assertTrue(all(s["detail"] for s in spared))

    def test_spared_as_dicts_is_empty_when_nothing_was_spared(self):
        self.assertEqual(spared_as_dicts(apply_expectations([self._rec()], {})), [])
        self.assertEqual(spared_as_dicts([]), [])
