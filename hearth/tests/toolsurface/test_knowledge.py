from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface.knowledge import (
    project,
    project_capacity_knowledge,
    query_beliefs_summary,
    query_capabilities,
    query_capacity,
    query_findings,
    record_event,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_RUNS = REPO_ROOT / "fixtures" / "workflow" / "runs"
SINGLE_EVENT = REPO_ROOT / "fixtures" / "workflow" / "single-event.question-answered.json"


class KnowledgeScopedTestCase(TestCase):
    """Every test runs against a temp sandbox — the real knowledge/ is never written.

    Fixture run trees (events + artifact refs) are copied into the sandbox, which also
    strips the 'fixtures' path component so the fixture-taint guard judges these sources
    exactly as it would judge real ones aimed at a non-repo out dir: pass.
    """

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        shutil.copytree(FIXTURE_RUNS, self.scope / "runs")

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)


class RecordEventTests(KnowledgeScopedTestCase):
    def test_valid_event_appends_via_existing_machinery(self) -> None:
        event = json.loads(SINGLE_EVENT.read_text(encoding="utf-8"))
        result = record_event(event, events_path="runs/hearth/events.jsonl")
        self.assertTrue(result["appended"])
        self.assertEqual(result["event_type"], "question.answered")

        ledger = self.scope / "runs" / "hearth" / "events.jsonl"
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event_id"], event["event_id"])

    def test_invalid_event_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            record_event({"event_type": "not.a.real.type"})

    def test_events_path_outside_scope_rejected(self) -> None:
        event = json.loads(SINGLE_EVENT.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            record_event(event, events_path="../escape.jsonl")


class ProjectTests(KnowledgeScopedTestCase):
    def test_capacity_and_findings_project_from_fixture_corpus(self) -> None:
        result = project(kinds=["capacity", "findings"])
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["event_files"], 0)

        knowledge_dir = self.scope / "knowledge"
        self.assertTrue((knowledge_dir / "capacity_estimates.json").is_file())
        self.assertTrue((knowledge_dir / "findings.json").is_file())

        findings_summary = result["kinds"]["findings"]["summary"]
        self.assertGreater(findings_summary["observation_count"], 0)

    def test_project_all_runs_every_kind_in_dependency_order(self) -> None:
        result = project()
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            list(result["kinds"].keys()),
            ["capacity", "findings", "associations", "coverage", "experiments", "policy"],
        )
        for file_name in ("capabilities.json", "coverage.json", "policy.json",
                          "experiment_candidates.json"):
            self.assertTrue((self.scope / "knowledge" / file_name).is_file(), file_name)

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project(kinds=["telemetry"])

    def test_out_dir_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project(kinds=["capacity"], out="../leaky-knowledge")

    def test_projector_fault_is_reported_not_raised(self) -> None:
        # policy without a findings.json upstream: the per-kind digest carries the error
        result = project(kinds=["policy"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["kinds"]["policy"]["ok"])
        self.assertTrue(result["kinds"]["policy"]["error"])


class QueryTests(KnowledgeScopedTestCase):
    def test_queries_before_projection_report_unavailable(self) -> None:
        self.assertFalse(query_capabilities()["available"])
        self.assertFalse(query_findings()["available"])

    def test_queries_return_content_and_mtime_after_projection(self) -> None:
        project(kinds=["capacity", "findings", "associations"])

        capabilities = query_capabilities()
        self.assertTrue(capabilities["available"])
        self.assertIsNotNone(capabilities["mtime"])
        self.assertEqual(capabilities["content"]["contract_version"], "capabilities.v1")

        findings = query_findings()
        self.assertTrue(findings["available"])
        self.assertGreater(len(findings["content"]["findings"]), 0)

    def test_beliefs_summary_is_a_digest_with_mtimes(self) -> None:
        project(kinds=["capacity", "findings", "associations"])
        summary = query_beliefs_summary()
        files = summary["files"]
        self.assertTrue(files["capabilities.json"]["available"])
        self.assertIn("capability_count", files["capabilities.json"])
        self.assertIsNotNone(files["findings.json"]["mtime"])
        self.assertIn("finding_counts", files["findings.json"])
        self.assertFalse(files["policy.json"]["available"])  # not projected in this test


class CapacityKnowledgeTests(KnowledgeScopedTestCase):
    """JS2: project_capacity_knowledge / query_capacity round-trip via the ledger."""

    def _write_ledger(self, relative_path: str = "hearth/var/ledger/events.ndjson") -> Path:
        ledger = self.scope / relative_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "schema": "hearth-event.v1",
                "event_id": "he_a",
                "ts": "2026-07-04T00:00:00+00:00",
                "caller": {"id": "local-1", "runner_class": "local", "node": "omen"},
                "tool": "run_tests",
                "ok": True,
                "duration_ms": 1000,
                "cost": {"tokens_in": 10, "tokens_out": 50, "watt_s": None},
                "task_id": None,
            },
        ]
        with ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        return ledger

    def test_query_before_projection_reports_unavailable(self) -> None:
        self.assertFalse(query_capacity()["available"])

    def test_project_then_query_round_trips_capacity_json(self) -> None:
        self._write_ledger()
        result = project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 1)
        self.assertEqual(result["evidence_watermark"], "2026-07-04T00:00:00+00:00")

        queried = query_capacity()
        self.assertTrue(queried["available"])
        self.assertIsNotNone(queried["mtime"])
        content = queried["content"]
        self.assertEqual(content["contract_version"], "capacity.v1")
        self.assertEqual(len(content["buckets"]), 1)
        self.assertEqual(content["buckets"][0]["tool"], "run_tests")

    def test_missing_ledger_yields_empty_document(self) -> None:
        result = project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 0)
        self.assertEqual(result["evidence_watermark"], None)

    def test_ledger_path_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project_capacity_knowledge(ledger_path="../escape.ndjson")
