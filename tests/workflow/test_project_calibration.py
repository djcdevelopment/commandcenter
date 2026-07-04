"""Tests for project_calibration — confidence-curve calibration (Gc).

Curated addition. NEITHER gc lap was landable: am4-worker-1's "winning" lap (assay 70)
was a bare module with no wiring/tests; cc-builder-1's (assay 64) CALLED
_calibration_candidates() but never defined it (NameError). Synthesis here completed the
candidate synthesizer and wrote the DoD-required tests.

Hand-computed anchor: a combo observed as 8 consecutive successes yields 7 scored
predictions (obs #1 excluded — no prior). Prior-sample confidences are
_confidence(s)=round(s/(s+2),2) for s=1..7 -> 0.33,0.50,0.60,0.67,0.71,0.75,0.78, so the
[0.6,0.8) bucket gets exactly 5 predictions (0.60,0.67,0.71,0.75,0.78) = CALIBRATION_MIN_BUCKET_N,
all matched -> observed_rate 1.0, and a POSITIVE bias (the curve under-predicts an
always-success low-sample known_good combo — the finding this tool exists to surface).
"""
import unittest

from tools.workflow.project_calibration import (
    calibrate, count_scored_predictions, CALIBRATION_MIN_TOTAL_PREDICTIONS,
)
from tools.workflow.project_experiments import _calibration_candidates


def _obs(oid, ts, outcome, combo="b|m|k"):
    builder, model, backend = combo.split("|")
    return {"observation_id": oid, "timestamp": ts, "outcome": outcome,
            "builder_id": builder, "model_id": model, "backend": backend}


def _run(outcomes, combo="b|m|k"):
    return [_obs(f"o{i}", f"2026-07-01T00:{i:02d}:00Z", oc, combo) for i, oc in enumerate(outcomes)]


class TestCalibrate(unittest.TestCase):
    def test_first_observation_excluded_no_prior(self):
        r = calibrate(_run(["success", "success"]))
        self.assertEqual(r["excluded_by_reason"].get("no_prior_finding"), 1)

    def test_always_success_bucket_and_positive_bias(self):
        r = calibrate(_run(["success"] * 8))
        self.assertEqual(r["total_scored_predictions"], 7)
        b = next(x for x in r["buckets"] if x["bucket"] == "[0.6,0.8)")
        self.assertEqual(b["n"], 5)
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["observed_rate"], 1.0)   # always-success known_good, all matched
        self.assertGreater(b["bias"], 0.0)          # curve under-predicts -> positive bias
        self.assertAlmostEqual(b["curve_implied_rate"], round((0.60+0.67+0.71+0.75+0.78)/5, 4))

    def test_known_bad_predicts_failure_and_matches(self):
        # 8 failures: obs1 no-prior, obs2 uncertain (1 fail < KNOWN_BAD_MIN_FAILURES), obs3+ known_bad
        r = calibrate(_run(["error"] * 8))
        self.assertEqual(r["total_scored_predictions"], 6)
        b = next(x for x in r["buckets"] if x["bucket"] == "[0.6,0.8)")
        self.assertEqual(b["observed_rate"], 1.0)   # known_bad predicts failure, actual failure -> matched

    def test_thin_corpus_is_insufficient_not_extrapolated(self):
        r = calibrate(_run(["success"] * 3))  # only 2 scored predictions
        for b in r["buckets"]:
            self.assertIn(b["status"], ("insufficient_evidence",))
            self.assertIsNone(b["observed_rate"])
            self.assertIsNone(b["bias"])

    def test_determinism(self):
        obs = _run(["success", "error", "success"] * 3)
        self.assertEqual(calibrate(obs), calibrate(list(reversed(obs))))  # sort makes order irrelevant

    def test_report_shape_and_watermark(self):
        obs = _run(["success"] * 4)
        r = calibrate(obs)
        self.assertEqual(r["contract_version"], "calibration-report.v1")
        self.assertEqual(r["evidence_watermark"], max(o["timestamp"] for o in obs))
        self.assertEqual(r["total_observations"], 4)

    def test_count_scored_predictions_matches(self):
        obs = _run(["success"] * 8)
        self.assertEqual(count_scored_predictions(obs), 7)


class TestCalibrationCandidateGating(unittest.TestCase):
    def test_proposed_above_threshold_when_report_stale(self):
        c = _calibration_candidates(CALIBRATION_MIN_TOTAL_PREDICTIONS, "2026-07-02T00:00:00Z", None)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["experiment_type"], "confidence_calibration")
        self.assertIn("AUTHORED-VALUE-REQUIRED", c[0]["worth"])

    def test_not_proposed_below_threshold(self):
        self.assertEqual(_calibration_candidates(CALIBRATION_MIN_TOTAL_PREDICTIONS - 1, "wm", None), [])

    def test_not_proposed_when_report_watermark_current(self):
        self.assertEqual(_calibration_candidates(CALIBRATION_MIN_TOTAL_PREDICTIONS, "wm", "wm"), [])

    def test_proposed_when_watermarks_differ(self):
        self.assertEqual(len(_calibration_candidates(CALIBRATION_MIN_TOTAL_PREDICTIONS, "wm1", "wm0")), 1)


if __name__ == "__main__":
    unittest.main()
