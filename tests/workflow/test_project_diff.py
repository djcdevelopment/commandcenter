"""Tests for project_diff — deterministic snapshot-diff (F1).

Curated addition: the F1 winning lap shipped project_diff.py without the DoD-required
test file. These verify the transition contract over the real fixture corpus without
brittle exact-value coupling, plus the two semantics the module hinges on (t1==t2 is
empty; empty->full shows everything forming) derived independently of the diff code.
"""
import unittest
from pathlib import Path

from tools.workflow.project_diff import diff_projections, _transition_id
from tools.workflow.project_capacity import (
    collect_event_files, extract_observations, extract_scheduler_decisions,
)
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_associations import (
    synthesize_associations, synthesize_capabilities,
)
from tools.workflow.project_coverage import synthesize_coverage
from tools.workflow.project_state import read_events

FIXTURES = [Path("fixtures/workflow/runs")]
VOCAB = {
    "association_formed", "association_retired", "capability_appeared",
    "qualification_transition", "gap_opened", "gap_closed", "finding_confidence_moved",
}
BEFORE_CORPUS = "2000-01-01T00:00:00Z"
AFTER_CORPUS = "2100-01-01T00:00:00Z"
STRADDLE = "2026-06-30T23:59:59Z"


def _full_projection():
    """Independently synthesize the full-corpus entity sets (no diff code involved)."""
    obs, dec = [], []
    for ef in collect_event_files(FIXTURES):
        events = read_events(ef)
        o, _ = extract_observations(events, ef)
        d, _ = extract_scheduler_decisions(events, ef)
        obs.extend(o)
        dec.extend(d)
    findings = synthesize_findings(obs, dec)
    associations = synthesize_associations(obs, findings)
    capabilities = synthesize_capabilities(associations, findings, obs)
    gaps = synthesize_coverage(obs, dec, capabilities)
    return findings, associations, capabilities, gaps


class TestDiffContract(unittest.TestCase):
    def test_t1_equals_t2_is_empty(self):
        d = diff_projections(FIXTURES, STRADDLE, STRADDLE)
        self.assertEqual(d["transition_count"], 0)
        self.assertEqual(d["transitions"], [])

    def test_determinism_byte_identical(self):
        a = diff_projections(FIXTURES, STRADDLE, AFTER_CORPUS)
        b = diff_projections(FIXTURES, STRADDLE, AFTER_CORPUS)
        self.assertEqual(a, b)

    def test_by_type_sums_to_count(self):
        d = diff_projections(FIXTURES, BEFORE_CORPUS, AFTER_CORPUS)
        self.assertEqual(sum(d["by_type"].values()), d["transition_count"])

    def test_transition_ids_stable_and_wellformed(self):
        d = diff_projections(FIXTURES, BEFORE_CORPUS, AFTER_CORPUS)
        for t in d["transitions"]:
            self.assertIn(t["transition_type"], VOCAB)
            recomputed = _transition_id(t["transition_type"], t["subject_id"], BEFORE_CORPUS, AFTER_CORPUS)
            self.assertEqual(t["transition_id"], recomputed)
            self.assertEqual(len(t["transition_id"]), 16)
            int(t["transition_id"], 16)  # raises if not hex

    def test_empty_to_full_shows_everything_forming(self):
        # Diffing from before-the-corpus to after-the-corpus: every entity that exists
        # in the full projection must appear as formed/appeared/opened, and NOTHING may
        # be retired/closed (the corpus only accretes across this window).
        _findings, associations, capabilities, gaps = _full_projection()
        d = diff_projections(FIXTURES, BEFORE_CORPUS, AFTER_CORPUS)
        self.assertGreater(d["transition_count"], 0, "fixture corpus should have activity")

        by = {}
        for t in d["transitions"]:
            by.setdefault(t["transition_type"], set()).add(t["subject_id"])

        self.assertEqual(by.get("association_formed", set()),
                         {a["association_id"] for a in associations})
        self.assertEqual(by.get("capability_appeared", set()),
                         {c["capability_id"] for c in capabilities})
        self.assertEqual(by.get("gap_opened", set()),
                         {g["gap_id"] for g in gaps})
        # nothing retires/closes when going empty -> full
        self.assertEqual(by.get("association_retired", set()), set())
        self.assertEqual(by.get("gap_closed", set()), set())
        self.assertNotIn("qualification_transition", by)  # no prior state to transition from

    def test_full_to_full_no_change(self):
        d = diff_projections(FIXTURES, AFTER_CORPUS, AFTER_CORPUS)
        self.assertEqual(d["transition_count"], 0)

    def test_document_shape(self):
        d = diff_projections(FIXTURES, BEFORE_CORPUS, AFTER_CORPUS)
        self.assertEqual(d["contract_version"], "snapshot-diff.v1")
        self.assertEqual(d["t1"], BEFORE_CORPUS)
        self.assertEqual(d["t2"], AFTER_CORPUS)
        self.assertEqual(d["observation_count_at_t1"], 0)
        self.assertGreater(d["observation_count_at_t2"], 0)


if __name__ == "__main__":
    unittest.main()
