from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import patch

from hearth.scheduler.hindsight import build_jobs_from_history, render_table, replay
from hearth.scheduler.ontology import Machine
from hearth.toolsurface.scheduler import get_tools, schedule_hindsight

REPO_ROOT = Path(__file__).resolve().parents[3]


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather_payload(records, scanned=None):
    return json.dumps({"records": records, "scanned": scanned if scanned is not None else len(records)})


def _machines() -> list[Machine]:
    return [
        Machine(name="am4-worker-1", kind="local", token_cost_weight=0.0, tags=["local"]),
        Machine(name="frontier-builder", kind="frontier", token_cost_weight=1.0, tags=["frontier"]),
    ]


class BuildJobsFromHistoryTests(TestCase):
    def test_only_completed_ok_runs_with_a_winner_are_included(self) -> None:
        records = [
            {"plan_id": "ok-1", "status": "ok", "winner": "am4-worker-1",
             "task_class": "build", "duration_s": 100},
            {"plan_id": "errored-1", "status": "errored", "winner": "am4-worker-1",
             "duration_s": 50},
            {"plan_id": "no-winner", "status": "ok", "winner": None, "duration_s": 10},
            {"plan_id": "in-flight", "duration_s": 10},
        ]
        jobs = build_jobs_from_history(records, machines=_machines())
        self.assertEqual([j["plan_id"] for j in jobs], ["ok-1"])

    def test_local_winner_has_zero_actual_tokens(self) -> None:
        records = [{"plan_id": "local-run", "status": "ok", "winner": "am4-worker-1",
                    "task_class": "build", "duration_s": 200}]
        jobs = build_jobs_from_history(records, machines=_machines())
        self.assertEqual(jobs[0]["actual_tokens"], 0)
        self.assertEqual(jobs[0]["actual_machine"], "am4-worker-1")
        self.assertEqual(jobs[0]["actual_s"], 200.0)

    def test_frontier_winner_falls_back_to_default_tokens_when_unknown(self) -> None:
        records = [{"plan_id": "frontier-run", "status": "ok", "winner": "frontier-builder",
                    "task_class": "build", "duration_s": 60}]
        jobs = build_jobs_from_history(records, machines=_machines(), capacity=None)
        self.assertGreater(jobs[0]["actual_tokens"], 0)

    def test_explicit_tokens_out_is_used_when_present(self) -> None:
        records = [{"plan_id": "frontier-run", "status": "ok", "winner": "frontier-builder",
                    "task_class": "build", "duration_s": 60, "tokens_out": 4321}]
        jobs = build_jobs_from_history(records, machines=_machines())
        self.assertEqual(jobs[0]["actual_tokens"], 4321)

    def test_unrecognized_winner_name_defaults_to_local_builder_name_set(self) -> None:
        # No Machine objects passed -> falls back to the declared local-builder name set.
        records = [{"plan_id": "r1", "status": "ok", "winner": "cc-builder-1",
                    "task_class": "build", "duration_s": 30}]
        jobs = build_jobs_from_history(records)
        self.assertEqual(jobs[0]["actual_tokens"], 0)


class ReplayHandComputedTests(TestCase):
    """A 3-run case whose regret numbers can be verified by hand."""

    def test_three_run_regret(self) -> None:
        # Two local runs (free) + one frontier run that actually cost 5000 tokens,
        # replayed as a batch. All same task_class="build" with DEFAULT durations
        # (1800s) unless a capacity bucket overrides; use tiny explicit durations
        # via a capacity doc so this stays a fast, exact test.
        capacity = {
            "contract_version": "capacity.v1",
            "buckets": [
                {"task_class": "build", "node": None, "duration_ms": {"p90": 100000}},
            ],
        }
        records = [
            {"plan_id": "run-a", "status": "ok", "winner": "am4-worker-1",
             "task_class": "build", "duration_s": 100},
            {"plan_id": "run-b", "status": "ok", "winner": "am4-worker-1",
             "task_class": "build", "duration_s": 150},
            {"plan_id": "run-c", "status": "ok", "winner": "frontier-builder",
             "task_class": "build", "duration_s": 80, "tokens_out": 5000},
        ]
        machines = _machines()
        report = replay(records, machines, capacity)

        self.assertEqual(report["n_runs"], 3)
        # Actual: sum of each run's own duration + actual metered tokens (only run-c).
        self.assertEqual(report["actual"]["span_s"], 100 + 150 + 80)
        self.assertEqual(report["actual"]["metered_tokens"], 5000)
        # Proposed: the solver has 2 local machines available... wait, fixture only
        # has 1 local (am4-worker-1) + 1 frontier. With no deadlines, the token
        # objective dominates: all 3 jobs should land on the free local machine
        # (sequential on that one machine is still cheaper than any frontier token
        # spend), giving proposed metered_tokens == 0.
        self.assertEqual(report["proposed"]["solver_status"], "OPTIMAL")
        self.assertEqual(report["proposed"]["metered_tokens"], 0)
        # tokens_saved = actual(5000) - proposed(0) = 5000.
        self.assertEqual(report["regret"]["tokens_saved"], 5000)
        # span_delta_s = actual total (330) - proposed makespan (sequential on the
        # single local machine over 3 x 100s capacity-bucket durations = 300s).
        self.assertEqual(report["proposed"]["span_s"], 300.0)
        self.assertEqual(report["regret"]["span_delta_s"], 330 - 300.0)

        plan_ids = {row["plan_id"] for row in report["per_run"]}
        self.assertEqual(plan_ids, {"run-a", "run-b", "run-c"})
        for row in report["per_run"]:
            self.assertEqual(row["proposed_machine"], "am4-worker-1")


class RenderTableTests(TestCase):
    def test_table_contains_header_and_summary_lines(self) -> None:
        capacity = None
        machines = _machines()
        records = [{"plan_id": "run-a", "status": "ok", "winner": "am4-worker-1",
                    "task_class": "build", "duration_s": 100}]
        report = replay(records, machines, capacity)
        table = render_table(report)
        self.assertIn("plan_id", table)
        self.assertIn("run-a", table)
        self.assertIn("n_runs=1", table)
        self.assertIn("regret:", table)


class ScheduleHindsightToolOfflineTests(TestCase):
    """Fully offline path: records passed in directly, no SSH."""

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        import shutil
        (self.scope / "fleet").mkdir(parents=True, exist_ok=True)
        (self.scope / "hearth" / "etc").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "fleet" / "inventory.toml", self.scope / "fleet" / "inventory.toml")
        shutil.copy(REPO_ROOT / "hearth" / "etc" / "backends.toml",
                    self.scope / "hearth" / "etc" / "backends.toml")

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous
        import shutil
        shutil.rmtree(self.scope, ignore_errors=True)

    def test_offline_records_produce_a_report_without_ssh(self) -> None:
        records = [
            {"plan_id": "run-a", "status": "ok", "winner": "am4-worker-1",
             "task_class": "build", "duration_s": 100},
            {"plan_id": "run-b", "status": "errored", "winner": "am4-worker-1",
             "duration_s": 50},
        ]
        with patch("subprocess.run", side_effect=AssertionError("must not touch SSH")):
            out = schedule_hindsight(records=records)
        self.assertTrue(out["ok"])
        self.assertEqual(out["report"]["n_runs"], 1)
        self.assertIn("regret:", out["table"])

    def test_limit_applied_to_offline_records(self) -> None:
        records = [{"plan_id": f"run-{i}", "status": "ok", "winner": "am4-worker-1",
                    "task_class": "build", "duration_s": 10} for i in range(5)]
        out = schedule_hindsight(records=records, limit=2)
        self.assertEqual(out["report"]["n_runs"], 2)


class ScheduleHindsightToolGatherTests(TestCase):
    """SSH gather path, mocked exactly like test_patrol.py."""

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous
        import shutil
        shutil.rmtree(self.scope, ignore_errors=True)

    def test_gathers_completed_runs_over_ssh(self) -> None:
        records = [{"plan_id": "gathered-run", "status": "ok", "winner": "am4-worker-1",
                    "task_class": "build", "duration_s": 42}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))):
            out = schedule_hindsight()
        self.assertTrue(out["ok"])
        self.assertEqual(out["report"]["n_runs"], 1)
        self.assertEqual(out["report"]["per_run"][0]["plan_id"], "gathered-run")

    def test_ssh_failure_is_a_clean_result(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = schedule_hindsight()
        self.assertFalse(out["ok"])
        self.assertIn("TimeoutExpired", out["error"])

    def test_non_json_gather_output_reported(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="not json")):
            out = schedule_hindsight()
        self.assertFalse(out["ok"])
        self.assertIn("non-JSON", out["error"])


class GetToolsTests(TestCase):
    def test_get_tools_exposes_both_scheduler_tools(self) -> None:
        tools = get_tools()
        self.assertEqual([t.__name__ for t in tools], ["propose_schedule", "schedule_hindsight"])
