"""Tests for project_learning — Stream Ga worth-realized-per-hour projector.

Curated addition: the Ga winning lap shipped project_learning.py without the
DoD-required test file. These hand-compute the denominator (dispatch durations),
grouping, excluded-run reporting, worth overlay, and belief movement, and verify the
report contract + determinism + the honest all-zero case the current corpus produces.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tools.workflow.project_learning import (
    worth_realized, load_worth_overlay, _extract_durations_from_events,
    extract_all_durations, _belief_movement, _in_window,
)


def _write_corpus(rows_by_run: dict) -> Path:
    """Write {run_id: [event dicts]} to a temp dir as <run>/events.jsonl; return the dir."""
    root = Path(tempfile.mkdtemp())
    for run_id, events in rows_by_run.items():
        d = root / run_id
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return root


def _ev(run_id, ts, et, node=None):
    e = {"run_id": run_id, "timestamp": ts, "event_type": et}
    if node:
        e["actor"] = {"id": node}
    return e


class TestWorthOverlay(unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(load_worth_overlay(Path(tempfile.gettempdir()) / "nope_worth.json"), {})

    def test_empty_entries_is_empty(self):
        p = Path(tempfile.mktemp(suffix=".json"))
        p.write_text(json.dumps({"contract_version": "candidate-worth.v1", "entries": []}), encoding="utf-8")
        self.assertEqual(load_worth_overlay(p), {})

    def test_keyed_by_candidate_id(self):
        p = Path(tempfile.mktemp(suffix=".json"))
        p.write_text(json.dumps({"entries": [
            {"candidate_id": "c1", "worth_points": 5, "reason": "r", "author": "derek"}]}), encoding="utf-8")
        got = load_worth_overlay(p)
        self.assertEqual(got["c1"]["worth_points"], 5)


class TestDurations(unittest.TestCase):
    def test_assigned_to_promotion_duration(self):
        events = [
            _ev("r1", "2026-07-01T00:00:00Z", "builder.assigned", node="cc-builder-1"),
            _ev("r1", "2026-07-01T01:00:00Z", "promotion.approved"),
        ]
        recs = _extract_durations_from_events(events)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["duration_s"], 3600.0)
        self.assertEqual(recs[0]["node_id"], "cc-builder-1")
        self.assertEqual(recs[0]["close_reason"], "promotion.approved")

    def test_first_close_event_wins(self):
        events = [
            _ev("r1", "2026-07-01T00:00:00Z", "builder.assigned", node="n"),
            _ev("r1", "2026-07-01T00:30:00Z", "promotion.held"),
            _ev("r1", "2026-07-01T01:00:00Z", "promotion.approved"),
        ]
        recs = _extract_durations_from_events(events)
        self.assertEqual(recs[0]["duration_s"], 1800.0)  # closed at the FIRST close event
        self.assertEqual(recs[0]["close_reason"], "promotion.held")

    def test_no_close_falls_back_to_last_event(self):
        events = [
            _ev("r1", "2026-07-01T00:00:00Z", "builder.assigned", node="n"),
            _ev("r1", "2026-07-01T00:45:00Z", "work.accepted"),
        ]
        recs = _extract_durations_from_events(events)
        self.assertEqual(recs[0]["close_reason"], "last_event_fallback")
        self.assertEqual(recs[0]["duration_s"], 2700.0)

    def test_run_without_assigned_not_yielded(self):
        events = [_ev("r1", "2026-07-01T00:00:00Z", "work.accepted")]
        self.assertEqual(_extract_durations_from_events(events), [])

    def test_extract_all_durations_reports_excluded(self):
        root = _write_corpus({
            "r_has": [_ev("r_has", "2026-07-01T00:00:00Z", "builder.assigned", node="n"),
                      _ev("r_has", "2026-07-01T01:00:00Z", "promotion.approved")],
            "r_none": [_ev("r_none", "2026-07-01T00:00:00Z", "work.accepted")],
        })
        dispatches, excluded = extract_all_durations(list(root.glob("*/events.jsonl")))
        self.assertEqual(len(dispatches), 1)
        self.assertEqual([e["run_id"] for e in excluded], ["r_none"])


class TestBeliefMovement(unittest.TestCase):
    def test_sum_abs_delta_and_null_skip(self):
        results = [
            {"belief_before": {"confidence_score": 0.3}, "belief_after": {"confidence_score": 0.6}},
            {"belief_before": {"confidence_score": 0.5}, "belief_after": {"confidence_score": 0.4}},
            {"belief_before": {"confidence_score": None}, "belief_after": {"confidence_score": 0.9}},
        ]
        m = _belief_movement(results)
        self.assertAlmostEqual(m["total_abs_delta"], 0.4)  # 0.3 + 0.1
        self.assertEqual(m["experiment_count"], 2)
        self.assertEqual(m["null_skipped"], 1)


class TestInWindow(unittest.TestCase):
    def test_bounds(self):
        self.assertTrue(_in_window("2026-07-01T12:00:00Z", None, None))
        self.assertFalse(_in_window("2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", None))
        self.assertFalse(_in_window("2026-07-03T00:00:00Z", None, "2026-07-02T00:00:00Z"))
        self.assertFalse(_in_window(None, None, None))


class TestWorthRealizedReport(unittest.TestCase):
    def test_unsupported_group_by_errors_with_tripwire(self):
        r = worth_realized([], group_by="economy")
        self.assertIn("error", r)
        self.assertTrue(r["dispatch_use_forbidden"])

    def test_denominator_grouping_and_tripwire(self):
        root = _write_corpus({
            "r1": [_ev("r1", "2026-07-01T00:00:00Z", "builder.assigned", node="cc-builder-1"),
                   _ev("r1", "2026-07-01T01:00:00Z", "promotion.approved")],
            "r2": [_ev("r2", "2026-07-01T02:00:00Z", "builder.assigned", node="cc-builder-1"),
                   _ev("r2", "2026-07-01T04:00:00Z", "retrospective.created")],
        })
        overlay = Path(tempfile.mktemp(suffix=".json"))
        overlay.write_text(json.dumps({"entries": []}), encoding="utf-8")
        r = worth_realized([root], worth_overlay_path=overlay)
        # cc-builder-1: 1h + 2h = 3h across 2 dispatches; no priced worth -> per_hour 0-or-None
        node = next(n for n in r["per_node"] if n["node_id"] == "cc-builder-1")
        self.assertEqual(node["dispatch_hours"], 3.0)
        self.assertEqual(node["dispatch_count"], 2)
        self.assertEqual(r["fleet"]["worth_points_realized"], 0)
        # honest unavailables + tripwire present
        self.assertTrue(r["dispatch_use_forbidden"])
        self.assertIn("requires snapshot-diff", r["companion_metrics"]["qualifications_renewed"])
        self.assertIn("requires economy_influence", r["companion_metrics"]["economy_grouping"])

    def test_empty_sources_are_zeros_not_error(self):
        r = worth_realized([])
        self.assertNotIn("error", r)
        self.assertEqual(r["fleet"]["worth_points_realized"], 0)
        self.assertEqual(r["per_node"], [])

    def test_determinism(self):
        root = _write_corpus({
            "r1": [_ev("r1", "2026-07-01T00:00:00Z", "builder.assigned", node="n"),
                   _ev("r1", "2026-07-01T01:00:00Z", "promotion.approved")],
        })
        a = worth_realized([root])
        b = worth_realized([root])
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
