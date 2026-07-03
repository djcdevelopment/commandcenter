"""Stream A4 (approved D-A3-5): fixture-taint guard tests.

The incident chain began when fixtures/workflow/runs was poured through the projectors into
the repo's own knowledge/ store. check_fixture_taint refuses exactly that combination:
fixture-component sources AND out == repo knowledge dir. Everything else — fixtures into
temp dirs (the whole existing suite), real runs into repo knowledge — passes untouched.

The repo-knowledge target is simulated inside a temp sandbox by patching the injectable
corpus_guard.REPO_ROOT (or passing repo_root directly), so no test ever writes to the real
repo knowledge/ directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase, mock

from tools.workflow import corpus_guard
from tools.workflow import (
    project_associations,
    project_capacity,
    project_coverage,
    project_experiments,
    project_findings,
    project_policy,
)
from tools.workflow.corpus_guard import AUDIT_FILE, FixtureTaintError, check_fixture_taint

ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"


class CheckFixtureTaintTests(TestCase):
    """Direct unit tests, with repo_root injected to a temp sandbox."""

    def setUp(self) -> None:
        self.sandbox = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.sandbox, True)
        self.repo_knowledge = self.sandbox / "knowledge"
        self.fixture_source = self.sandbox / "fixtures" / "workflow" / "runs" / "run_x" / "events.jsonl"

    def test_fixtures_into_repo_knowledge_raises(self) -> None:
        with self.assertRaises(FixtureTaintError) as ctx:
            check_fixture_taint([self.fixture_source], self.repo_knowledge, repo_root=self.sandbox)
        message = str(ctx.exception)
        self.assertIn(str(self.fixture_source.resolve()), message)  # names the offending source
        self.assertIn("A4/D-A3-5", message)                         # names the rule
        self.assertIn("--allow-fixture-sources", message)           # names the escape hatch

    def test_fixtures_into_temp_out_pass_untouched(self) -> None:
        elsewhere = self.sandbox / "some-temp-knowledge"
        check_fixture_taint([self.fixture_source], elsewhere, repo_root=self.sandbox)
        self.assertFalse((elsewhere / AUDIT_FILE).exists())  # no audit noise on the allowed path

    def test_real_runs_into_repo_knowledge_pass_untouched(self) -> None:
        real_source = self.sandbox / "runs" / "run_real" / "events.jsonl"
        check_fixture_taint([real_source], self.repo_knowledge, repo_root=self.sandbox)
        self.assertFalse((self.repo_knowledge / AUDIT_FILE).exists())

    def test_component_must_equal_fixtures_exactly(self) -> None:
        # "fixtures" as a substring of a component is NOT taint; the component must match exactly.
        near_miss = self.sandbox / "my-fixtures-archive" / "runs" / "events.jsonl"
        check_fixture_taint([near_miss], self.repo_knowledge, repo_root=self.sandbox)

    def test_allow_permits_and_audits(self) -> None:
        check_fixture_taint([self.fixture_source], self.repo_knowledge,
                            allow=True, repo_root=self.sandbox)
        audit_lines = (self.repo_knowledge / AUDIT_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(audit_lines), 1)
        record = json.loads(audit_lines[0])
        self.assertEqual(record["event"], "fixture_taint_permitted")
        self.assertEqual(record["sources"], [str(self.fixture_source.resolve())])
        self.assertEqual(record["tainted_sources"], [str(self.fixture_source.resolve())])
        self.assertEqual(record["out_dir"], str(self.repo_knowledge.resolve()))
        self.assertEqual(record["flag"], "--allow-fixture-sources")
        self.assertNotIn("timestamp", record)  # D18: no wall-clock stamps in the trail

    def test_allow_without_taint_audits_nothing(self) -> None:
        real_source = self.sandbox / "runs" / "run_real" / "events.jsonl"
        check_fixture_taint([real_source], self.repo_knowledge, allow=True, repo_root=self.sandbox)
        self.assertFalse((self.repo_knowledge / AUDIT_FILE).exists())


class ProjectorWiringTests(TestCase):
    """The guard is wired into all six projector mains, after source/out resolution."""

    def setUp(self) -> None:
        self.sandbox = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.sandbox, True)
        self.repo_knowledge = self.sandbox / "knowledge"
        patcher = mock.patch.object(corpus_guard, "REPO_ROOT", self.sandbox.resolve())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fixture_findings_file(self) -> Path:
        # A findings.json that genuinely lives under a fixtures/ component (built from the
        # fixture corpus in a plain temp dir first, where projection is legitimately allowed).
        staging = self.sandbox / "staging"
        event_files = project_capacity.collect_event_files([RUNS_FIXTURE_DIR])
        project_findings.materialize_findings(event_files, staging)
        fixture_dir = self.sandbox / "fixtures"
        fixture_dir.mkdir(parents=True)
        target = fixture_dir / "findings.json"
        shutil.copyfile(staging / "findings.json", target)
        return target

    def test_all_event_source_projectors_refuse_fixtures_into_repo_knowledge(self) -> None:
        argv = [str(RUNS_FIXTURE_DIR), "--out", str(self.repo_knowledge)]
        for module in (project_findings, project_capacity, project_associations,
                       project_coverage, project_experiments):
            with self.subTest(projector=module.__name__):
                with self.assertRaises(FixtureTaintError):
                    module.main(argv)
        self.assertFalse(self.repo_knowledge.exists())  # refused before any write

    def test_project_policy_refuses_fixture_findings_into_repo_knowledge(self) -> None:
        findings_path = self._fixture_findings_file()
        with self.assertRaises(FixtureTaintError):
            project_policy.main([str(findings_path), "--out", str(self.repo_knowledge)])
        self.assertFalse((self.repo_knowledge / "policy.json").exists())

    def test_fixtures_into_temp_out_still_projects(self) -> None:
        # The existing suite's shape: fixture sources, temp --out. Must stay untouched.
        temp_out = self.sandbox / "temp-out"
        exit_code = project_findings.main([str(RUNS_FIXTURE_DIR), "--out", str(temp_out)])
        self.assertEqual(exit_code, 0)
        self.assertTrue((temp_out / "findings.json").is_file())
        self.assertFalse((temp_out / AUDIT_FILE).exists())

    def test_real_runs_into_repo_knowledge_still_projects(self) -> None:
        # The same events living OUTSIDE any fixtures/ component are a real corpus: allowed.
        real_runs = self.sandbox / "runs"
        shutil.copytree(RUNS_FIXTURE_DIR, real_runs)
        exit_code = project_findings.main([str(real_runs), "--out", str(self.repo_knowledge)])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.repo_knowledge / "findings.json").is_file())
        self.assertFalse((self.repo_knowledge / AUDIT_FILE).exists())

    def test_allow_flag_permits_and_audits_through_main(self) -> None:
        exit_code = project_findings.main(
            [str(RUNS_FIXTURE_DIR), "--out", str(self.repo_knowledge), "--allow-fixture-sources"])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.repo_knowledge / "findings.json").is_file())
        records = [json.loads(line) for line in
                   (self.repo_knowledge / AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
        permits = [r for r in records if r.get("event") == "fixture_taint_permitted"]
        self.assertEqual(len(permits), 1)
        self.assertEqual(permits[0]["out_dir"], str(self.repo_knowledge.resolve()))
        self.assertIn(str(RUNS_FIXTURE_DIR), permits[0]["sources"])
        self.assertEqual(permits[0]["flag"], "--allow-fixture-sources")

    def test_allow_flag_permits_and_audits_project_policy(self) -> None:
        findings_path = self._fixture_findings_file()
        exit_code = project_policy.main(
            [str(findings_path), "--out", str(self.repo_knowledge), "--allow-fixture-sources"])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.repo_knowledge / "policy.json").is_file())
        records = [json.loads(line) for line in
                   (self.repo_knowledge / AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
        permits = [r for r in records if r.get("event") == "fixture_taint_permitted"]
        self.assertEqual(len(permits), 1)
        self.assertEqual(permits[0]["tainted_sources"], [str(findings_path.resolve())])
