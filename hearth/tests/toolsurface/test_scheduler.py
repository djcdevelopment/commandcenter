from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.scheduler.decision import validate_decision
from hearth.scheduler.ontology import (
    DEFAULT_DURATIONS_S,
    Job,
    Machine,
    load_capacity,
    load_machines,
    lookup_duration_s,
)
from hearth.scheduler.solve import solve_schedule
from hearth.toolsurface.scheduler import get_tools, propose_schedule

REPO_ROOT = Path(__file__).resolve().parents[3]


def _machine(name: str, kind: str, weight: float, available: bool = True) -> Machine:
    return Machine(name=name, kind=kind, token_cost_weight=weight, tags=[kind],
                   available=available)


# Three-machine fixture: two free local builders + one metered frontier.
def _three_machines() -> list[Machine]:
    return [
        _machine("local-a", "local", 0.0),
        _machine("local-b", "local", 0.0),
        _machine("frontier-x", "frontier", 1.0),
    ]


def _no_overlap_holds(assignments: list[dict]) -> bool:
    by_machine: dict[str, list[tuple[float, float]]] = {}
    for a in assignments:
        by_machine.setdefault(a["machine"], []).append((a["start_s"], a["end_s"]))
    for spans in by_machine.values():
        spans.sort()
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            if s2 < e1:
                return False
    return True


class SolveTests(TestCase):
    """Deterministic 3-job x 3-machine CP-SAT fixtures."""

    def test_no_overlap_per_machine(self) -> None:
        jobs = [Job(plan_id=f"j{i}", task_class="build", est_tokens=1000) for i in range(3)]
        proposal = solve_schedule(jobs, _three_machines(), capacity=None)
        self.assertIn(proposal.solver_status, ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(len(proposal.assignments), 3)
        self.assertTrue(_no_overlap_holds(proposal.assignments))

    def test_slack_deadline_prefers_local_token_objective_wins(self) -> None:
        # One job, both-capable, generous deadline -> must land on a free local machine.
        jobs = [Job(plan_id="j1", task_class="build", est_tokens=5000, deadline_s=100000)]
        proposal = solve_schedule(jobs, _three_machines(), capacity=None)
        self.assertEqual(len(proposal.assignments), 1)
        self.assertNotEqual(proposal.assignments[0]["machine"], "frontier-x")
        self.assertEqual(proposal.est_metered_tokens, 0)

    def test_tight_deadline_forces_frontier_parallelism(self) -> None:
        # Two independent build jobs, deadline shorter than 2x build duration on one
        # machine. Only 1 local machine + 1 frontier -> must use frontier to parallelize.
        machines = [_machine("local-a", "local", 0.0), _machine("frontier-x", "frontier", 1.0)]
        dur = DEFAULT_DURATIONS_S["build"]
        jobs = [
            Job(plan_id="j1", task_class="build", est_tokens=1000, deadline_s=dur + 10),
            Job(plan_id="j2", task_class="build", est_tokens=1000, deadline_s=dur + 10),
        ]
        proposal = solve_schedule(jobs, machines, capacity=None)
        self.assertIn(proposal.solver_status, ("OPTIMAL", "FEASIBLE"))
        used = {a["machine"] for a in proposal.assignments}
        self.assertIn("frontier-x", used)
        self.assertGreater(proposal.est_metered_tokens, 0)

    def test_precedence_respected(self) -> None:
        jobs = [
            Job(plan_id="j1", task_class="test", est_tokens=100),
            Job(plan_id="j2", task_class="test", est_tokens=100, precedence=["j1"]),
        ]
        proposal = solve_schedule(jobs, _three_machines(), capacity=None)
        ends = {a["plan_id"]: a["end_s"] for a in proposal.assignments}
        starts = {a["plan_id"]: a["start_s"] for a in proposal.assignments}
        self.assertLessEqual(ends["j1"], starts["j2"])

    def test_empty_jobs_yields_trivial_proposal(self) -> None:
        proposal = solve_schedule([], _three_machines(), capacity=None)
        self.assertEqual(proposal.assignments, [])
        self.assertEqual(proposal.makespan_s, 0.0)

    def test_deterministic_across_runs(self) -> None:
        jobs = [Job(plan_id=f"j{i}", task_class="build", est_tokens=1000) for i in range(3)]
        p1 = solve_schedule(jobs, _three_machines(), capacity=None)
        p2 = solve_schedule(jobs, _three_machines(), capacity=None)
        self.assertEqual(p1.assignments, p2.assignments)
        self.assertEqual(p1.objective_value, p2.objective_value)


class CapacityLookupTests(TestCase):
    def test_absent_capacity_uses_defaults(self) -> None:
        self.assertIsNone(load_capacity("/no/such/capacity.json"))
        job = Job(plan_id="j1", task_class="build")
        machine = _machine("local-a", "local", 0.0)
        self.assertEqual(lookup_duration_s(job, machine, None),
                         DEFAULT_DURATIONS_S["build"])

    def test_capacity_task_class_node_p90_wins(self) -> None:
        capacity = {
            "contract_version": "capacity.v1",
            "evidence_watermark": None,
            "buckets": [
                {"task_class": "build", "node": "local-a", "runner_class": "local",
                 "model": None, "tool": "run_build", "calls": 3, "ok_rate": 1.0,
                 "duration_ms": {"p50": 1000, "p90": 2000, "mean": 1200, "max": 3000},
                 "tokens_out_per_s_p50": None, "last_seen": None},
            ],
        }
        job = Job(plan_id="j1", task_class="build")
        machine = _machine("local-a", "local", 0.0)
        self.assertEqual(lookup_duration_s(job, machine, capacity), 2.0)  # 2000ms -> 2s

    def test_unknown_task_class_falls_to_default_bucket(self) -> None:
        job = Job(plan_id="j1", task_class="mystery")
        machine = _machine("local-a", "local", 0.0)
        self.assertEqual(lookup_duration_s(job, machine, None),
                         DEFAULT_DURATIONS_S["default"])

    def test_est_duration_s_overrides_capacity(self) -> None:
        # U1: a caller-supplied per-job duration wins over every lookup path.
        capacity = {
            "contract_version": "capacity.v1",
            "evidence_watermark": None,
            "buckets": [
                {"task_class": "build", "node": "local-a", "runner_class": "local",
                 "model": None, "tool": "run_build", "calls": 3, "ok_rate": 1.0,
                 "duration_ms": {"p50": 1000, "p90": 2000, "mean": 1200, "max": 3000},
                 "tokens_out_per_s_p50": None, "last_seen": None},
            ],
        }
        job = Job(plan_id="j1", task_class="build", est_duration_s=42.0)
        machine = _machine("local-a", "local", 0.0)
        self.assertEqual(lookup_duration_s(job, machine, capacity), 42.0)

    def test_est_duration_s_none_falls_through(self) -> None:
        job = Job(plan_id="j1", task_class="build", est_duration_s=None)
        machine = _machine("local-a", "local", 0.0)
        self.assertEqual(lookup_duration_s(job, machine, None),
                         DEFAULT_DURATIONS_S["build"])

    def test_null_p90_bucket_skips_to_fallback(self) -> None:
        # When a matching bucket has null p90 (all events were failures),
        # it should be skipped in favor of the fallback chain.
        capacity = {
            "contract_version": "capacity.v1",
            "evidence_watermark": None,
            "buckets": [
                # First bucket: matches task_class+node but has null p90 (all failures)
                {"task_class": "build", "node": "local-a", "runner_class": "local",
                 "model": None, "tool": "run_build", "calls": 5, "ok_rate": 0.0,
                 "duration_ms": {"p50": None, "p90": None, "mean": None, "max": None},
                 "tokens_out_per_s_p50": None, "last_seen": None},
                # Fallback bucket: no node, has valid p90
                {"task_class": "build", "node": None, "runner_class": None,
                 "model": None, "tool": "run_build", "calls": 10, "ok_rate": 1.0,
                 "duration_ms": {"p50": 800, "p90": 1500, "mean": 1000, "max": 2000},
                 "tokens_out_per_s_p50": None, "last_seen": None},
            ],
        }
        job = Job(plan_id="j1", task_class="build")
        machine = _machine("local-a", "local", 0.0)
        # Should skip the null-p90 bucket and use the fallback (1500ms = 1.5s)
        self.assertEqual(lookup_duration_s(job, machine, capacity), 1.5)


class LoadMachinesTests(TestCase):
    def test_missing_inventory_yields_defaults(self) -> None:
        machines = load_machines("/no/inventory.toml", "/no/backends.toml")
        names = {m.name for m in machines}
        self.assertIn("frontier-builder", names)
        self.assertTrue(any(m.kind == "local" for m in machines))

    def test_real_inventory_loads_local_builders_plus_frontier(self) -> None:
        machines = load_machines(str(REPO_ROOT / "fleet" / "inventory.toml"),
                                 str(REPO_ROOT / "hearth" / "etc" / "backends.toml"))
        names = {m.name for m in machines}
        self.assertIn("am4-worker-1", names)
        self.assertIn("frontier-builder", names)
        frontier = next(m for m in machines if m.kind == "frontier")
        self.assertGreater(frontier.token_cost_weight, 0)


class DecisionRecordTests(TestCase):
    def test_decision_record_validates_against_schema(self) -> None:
        jobs = [Job(plan_id=f"j{i}", task_class="build", est_tokens=1000) for i in range(3)]
        proposal = solve_schedule(jobs, _three_machines(), capacity=None)
        from hearth.scheduler.decision import build_scheduler_decision
        record = build_scheduler_decision(jobs, _three_machines(), proposal)
        validate_decision(record)  # raises on failure
        self.assertEqual(record["contract_version"], "scheduler-decision.v1")
        self.assertTrue(record["candidates_considered"])
        self.assertEqual(record["economy_influence"]["objective_selected"], "cost_per_outcome")


class ProposeScheduleToolTests(TestCase):
    """The provider entry point, run under a HEARTH_SCOPE sandbox (no capacity.json)."""

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        # Provide the inventory + backends so machine loading exercises real files.
        (self.scope / "fleet").mkdir(parents=True, exist_ok=True)
        (self.scope / "hearth" / "etc").mkdir(parents=True, exist_ok=True)
        import shutil
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

    def test_absent_capacity_json_defaults_path(self) -> None:
        # No knowledge/capacity.json in the sandbox -> defaults, but still solves.
        jobs = [{"plan_id": "j1", "task_class": "build", "est_tokens": 1000},
                {"plan_id": "j2", "task_class": "test", "est_tokens": 200}]
        result = propose_schedule(jobs)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["proposal"]["assignments"]), 2)
        validate_decision(result["decision_record"])
        self.assertTrue(_no_overlap_holds(result["proposal"]["assignments"]))
        self.assertTrue(result["machines_considered"])

    def test_capacity_json_present_is_used(self) -> None:
        knowledge = self.scope / "knowledge"
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "capacity.json").write_text(json.dumps({
            "contract_version": "capacity.v1", "evidence_watermark": None,
            "buckets": [{"task_class": "build", "node": None, "runner_class": None,
                         "model": None, "tool": "run_build", "calls": 1, "ok_rate": 1.0,
                         "duration_ms": {"p50": 500, "p90": 500, "mean": 500, "max": 500},
                         "tokens_out_per_s_p50": None, "last_seen": None}]}), encoding="utf-8")
        result = propose_schedule([{"plan_id": "j1", "task_class": "build", "est_tokens": 10}])
        self.assertTrue(result["ok"])
        # 500ms p90 -> 1s duration -> makespan 1s (not the 1800s default).
        self.assertEqual(result["proposal"]["makespan_s"], 1.0)

    def test_slack_deadline_stays_local(self) -> None:
        jobs = [{"plan_id": "j1", "task_class": "build", "est_tokens": 9999,
                 "deadline_s": 100000}]
        result = propose_schedule(jobs)
        machine = result["proposal"]["assignments"][0]["machine"]
        kinds = {m["name"]: m["kind"] for m in result["machines_considered"]}
        self.assertEqual(kinds[machine], "local")
        self.assertEqual(result["proposal"]["est_metered_tokens"], 0)

    def test_bad_jobs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            propose_schedule([{"task_class": "build"}])  # no plan_id
        with self.assertRaises(ValueError):
            propose_schedule("not a list")  # type: ignore[arg-type]


class GetToolsTests(TestCase):
    def test_get_tools_exposes_propose_schedule(self) -> None:
        tools = get_tools()
        self.assertIn("propose_schedule", [t.__name__ for t in tools])
