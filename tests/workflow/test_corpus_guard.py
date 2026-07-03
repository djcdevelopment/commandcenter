"""Corpus regression guard tests (Stream A2).

Proves the guard blocks the 2026-07-02 incident shape — a re-projection over less evidence
than the previous run silently overwriting knowledge/*.json — while leaving normal advances
and diff-clean reruns untouched, and honouring an authored Clause-2 override with an audit
trail. jsonschema is not used; these exercise the real projector write paths.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.corpus_guard import (
    AUDIT_FILE,
    CorpusRegressionError,
    OVERRIDE_FILE,
    PROGRESS_FILE,
    guard_write,
    make_extractor,
)
from tools.workflow.project_capacity import collect_event_files
from tools.workflow.project_findings import FINDINGS_FILE, materialize_findings
from tools.workflow.project_policy import POLICY_FILE, materialize_policy

ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"


class CorpusGuardTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())
        self.knowledge_dir = self.temp_dir / "knowledge"
        self.event_files = collect_event_files([RUNS_FIXTURE_DIR])
        self.full = self.event_files            # 6 observations
        self.subset = self.event_files[:3]      # 3 observations

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------

    def _findings(self) -> dict:
        return json.loads((self.knowledge_dir / FINDINGS_FILE).read_text(encoding="utf-8"))

    def _override(self) -> dict:
        return json.loads((self.knowledge_dir / OVERRIDE_FILE).read_text(encoding="utf-8"))

    def _write_override(self, scope, active=True) -> None:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        (self.knowledge_dir / OVERRIDE_FILE).write_text(
            json.dumps({
                "active": active,
                "reason": "test: accepting a known reduced-corpus re-projection",
                "author": "tester",
                "scope": list(scope),
                "created": "2026-07-03",
            }, indent=2) + "\n",
            encoding="utf-8",
        )

    def _audit_records(self) -> list:
        path = self.knowledge_dir / AUDIT_FILE
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # -- (a) normal advance passes ---------------------------------------------------

    def test_advance_passes(self) -> None:
        materialize_findings(self.subset, self.knowledge_dir)
        self.assertEqual(self._findings()["observation_count"], 3)
        materialize_findings(self.full, self.knowledge_dir)  # more evidence -> allowed
        self.assertEqual(self._findings()["observation_count"], 6)

    # -- (b) the incident: less evidence is blocked, file unchanged -------------------

    def test_incident_regression_blocked_findings(self) -> None:
        materialize_findings(self.full, self.knowledge_dir)
        before = (self.knowledge_dir / FINDINGS_FILE).read_text(encoding="utf-8")
        with self.assertRaises(CorpusRegressionError) as ctx:
            materialize_findings(self.subset, self.knowledge_dir)
        self.assertIn("count 6 -> 3", str(ctx.exception))  # names both counts
        self.assertEqual((self.knowledge_dir / FINDINGS_FILE).read_text(encoding="utf-8"), before)
        self.assertEqual(self._findings()["observation_count"], 6)

    def test_incident_regression_blocked_policy(self) -> None:
        # project_policy takes a findings_path, not events: build policy from full findings,
        # then re-project from a findings.json derived from fewer events (source_findings drops).
        big_dir = self.temp_dir / "findings-full"
        small_dir = self.temp_dir / "findings-subset"
        materialize_findings(self.full, big_dir)
        materialize_findings(self.subset, small_dir)

        materialize_policy(big_dir / FINDINGS_FILE, self.knowledge_dir)
        policy_before = (self.knowledge_dir / POLICY_FILE).read_text(encoding="utf-8")
        with self.assertRaises(CorpusRegressionError):
            materialize_policy(small_dir / FINDINGS_FILE, self.knowledge_dir)
        self.assertEqual((self.knowledge_dir / POLICY_FILE).read_text(encoding="utf-8"), policy_before)

    # -- (c) override path: write succeeds, audited, override deactivated -------------

    def test_override_permits_and_deactivates(self) -> None:
        materialize_findings(self.full, self.knowledge_dir)
        self._write_override([FINDINGS_FILE])

        materialize_findings(self.subset, self.knowledge_dir)  # must NOT raise

        self.assertEqual(self._findings()["observation_count"], 3)      # write happened
        self.assertFalse(self._override()["active"])                    # deactivated post-batch
        self.assertFalse((self.knowledge_dir / PROGRESS_FILE).is_file())
        permits = [r for r in self._audit_records()
                   if r.get("event") == "corpus_regression_permitted" and r.get("file") == FINDINGS_FILE]
        self.assertEqual(len(permits), 1)
        self.assertEqual(permits[0]["old_count"], 6)
        self.assertEqual(permits[0]["new_count"], 3)
        self.assertEqual(permits[0]["author"], "tester")

    # -- (d) diff-clean rerun passes untouched ---------------------------------------

    def test_diff_clean_rerun_untouched(self) -> None:
        materialize_findings(self.full, self.knowledge_dir)
        before = (self.knowledge_dir / FINDINGS_FILE).read_text(encoding="utf-8")
        materialize_findings(self.full, self.knowledge_dir)  # equal count -> allowed, no-op diff
        self.assertEqual((self.knowledge_dir / FINDINGS_FILE).read_text(encoding="utf-8"), before)

    # -- per-batch (not per-file) deactivation ---------------------------------------

    def test_override_deactivates_only_after_whole_scope_written(self) -> None:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        ex = make_extractor("observation_count")
        a, b = self.knowledge_dir / "a.json", self.knowledge_dir / "b.json"
        guard_write(a, {"observation_count": 5}, ex)
        guard_write(b, {"observation_count": 5}, ex)
        self._write_override(["a.json", "b.json"])

        guard_write(a, {"observation_count": 2}, ex)  # first scoped regression
        self.assertTrue(self._override()["active"], "override must stay active until b.json is written")
        self.assertTrue((self.knowledge_dir / PROGRESS_FILE).is_file())
        self.assertEqual(json.loads(a.read_text(encoding="utf-8"))["observation_count"], 2)

        guard_write(b, {"observation_count": 1}, ex)  # scope now fully written
        self.assertFalse(self._override()["active"])
        self.assertFalse((self.knowledge_dir / PROGRESS_FILE).is_file())
        self.assertEqual(len([r for r in self._audit_records()
                              if r.get("event") == "corpus_regression_permitted"]), 2)

    def test_out_of_scope_file_still_blocked_under_active_override(self) -> None:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        ex = make_extractor("observation_count")
        target = self.knowledge_dir / "c.json"
        guard_write(target, {"observation_count": 5}, ex)
        self._write_override(["a.json"])  # scope does NOT include c.json
        with self.assertRaises(CorpusRegressionError):
            guard_write(target, {"observation_count": 1}, ex)
