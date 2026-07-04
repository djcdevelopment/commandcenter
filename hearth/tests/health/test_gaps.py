from __future__ import annotations

from unittest import TestCase

from hearth.health.gaps import PHANTOM_AGE_S, Gap, scan_runs, summarize


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

    def test_summarize_counts_by_severity_and_kind(self):
        gaps = [Gap("phantom_in_flight", "warn", "a", "x"),
                Gap("crashed_isolated", "high", "b", "y"),
                Gap("crashed_isolated", "high", "c", "z")]
        s = summarize(gaps)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_severity"], {"warn": 1, "high": 2})
        self.assertEqual(s["by_kind"], {"phantom_in_flight": 1, "crashed_isolated": 2})
