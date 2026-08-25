from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.scheduler.decision import validate_decision
from hearth.scheduler.ontology import (
    DEFAULT_DURATIONS_S,
    _SYNTHETIC_LOCAL,
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


# Declared inventory fixture for the provider tests. propose_schedule takes no
# inventory argument — it reads fleet/inventory.toml out of its HEARTH_SCOPE root —
# so the SANDBOX is the injection seam, and what we write into it is the pinned
# input. These tests used to copy the LIVE working-tree inventory in here instead,
# which made a fleet-config edit an input to a scheduler unit test: on 2026-08-24 the
# fleet hold parked the builders, test_slack_deadline_stays_local started asserting
# 'frontier' != 'local', and because the change arrived as a COMMIT the working tree
# was clean across both the failing and the passing runs — so it read as scheduler
# nondeterminism rather than as the config change it was.
#
# Shape mirrors the real inventory (two local builders + one frontier-runner
# builder), but every value the loader reads is declared here.
_PINNED_INVENTORY = """
[[node]]
name = "am4-worker-1"
kind = "logical-builder"
expect = "optional"
runner_class = "local"

[[node]]
name = "cc-builder-1"
kind = "vm"
expect = "up"
runner_class = "frontier"

[[node]]
name = "cc-builder-2"
kind = "vm"
expect = "optional"
runner_class = "local"
"""

# The same pool with every REAL local builder parked via the purpose-built exclusion
# key. cc-builder-1 stays schedulable so the pool is not empty — it is just
# frontier-only, which is precisely the shape that used to force metered spend.
_PINNED_INVENTORY_ALL_LOCALS_PARKED = _PINNED_INVENTORY.replace(
    'runner_class = "local"', 'runner_class = "local"\nschedulable = false')

_PINNED_BACKENDS = """
[[backend]]
name = "omen-arc"
tags = ["code"]
"""


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
    def _inventory(self, *body: str) -> str:
        """Write a throwaway inventory from TOML lines; return its path."""
        path = Path(mkdtemp()) / "inventory.toml"
        path.write_text(chr(10).join(body) + chr(10), encoding="utf-8")
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        return str(path)

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

    def test_real_inventory_kinds_cc_builder_1_as_frontier(self) -> None:
        # cc-builder-1 runs the metered claude/sonnet runner; the solver must
        # charge for it, not treat it as a free local machine (the pre-fix pool
        # hardcoded all three builders as kind="local").
        machines = load_machines(str(REPO_ROOT / "fleet" / "inventory.toml"),
                                 str(REPO_ROOT / "hearth" / "etc" / "backends.toml"))
        by_name = {m.name: m for m in machines}
        self.assertEqual(by_name["cc-builder-1"].kind, "frontier")
        self.assertGreater(by_name["cc-builder-1"].token_cost_weight, 0)
        self.assertEqual(by_name["cc-builder-2"].kind, "local")
        self.assertEqual(by_name["am4-worker-1"].kind, "local")

    def test_expect_optional_is_still_schedulable(self) -> None:
        # `expect` is fleet_ping's ALARM flag, not a dispatch gate: "optional" only
        # means "absence must not turn the health sweep red". Reading it as
        # availability emptied the local pool the moment the 2026-08-24 fleet hold
        # parked the VMs, leaving only the synthetic frontier-builder.
        inventory = self._inventory(
            '[[node]]',
            'name = "cc-builder-2"',
            'expect = "optional"',
            'runner_class = "local"',
        )
        machines = load_machines(inventory, "/no/backends.toml")
        cc2 = next(m for m in machines if m.name == "cc-builder-2")
        self.assertTrue(cc2.available)

    def test_schedulable_false_excludes_the_builder(self) -> None:
        # The purpose-built exclusion key still takes a builder out of the pool.
        inventory = self._inventory(
            '[[node]]',
            'name = "cc-builder-2"',
            'expect = "up"',
            'schedulable = false',
            'runner_class = "local"',
        )
        machines = load_machines(inventory, "/no/backends.toml")
        cc2 = next(m for m in machines if m.name == "cc-builder-2")
        self.assertFalse(cc2.available)

    def test_all_locals_parked_still_offers_an_available_local(self) -> None:
        # The invariant the pool exists to hold, at its hardest case: the fleet has
        # parked every real local builder. `kind == "local"` is still satisfied — the
        # builders are named, just not schedulable — so a guarantee written against
        # `kind` reads as held while the solver has nothing free to place work on and
        # falls through to metered frontier. Keyed on `available`, it actually holds.
        inventory = self._inventory(
            '[[node]]',
            'name = "cc-builder-2"',
            'runner_class = "local"',
            'schedulable = false',
            '',
            '[[node]]',
            'name = "am4-worker-1"',
            'runner_class = "local"',
            'schedulable = false',
        )
        machines = load_machines(inventory, "/no/backends.toml")
        parked = {m.name for m in machines if not m.available}
        self.assertEqual(parked, {"cc-builder-2", "am4-worker-1"})
        self.assertTrue(any(m.kind == "local" and m.available for m in machines))
        self.assertTrue(any(m.kind == "frontier" and m.available for m in machines))

    def test_real_inventory_offers_a_real_available_local_builder(self) -> None:
        # This is the FLEET-CONFIG guard, deliberately ambient: it reads the live
        # fleet/inventory.toml and asserts the fleet still names a REAL schedulable
        # local builder. load_machines now guarantees an available local either way,
        # so without excluding the synthetic fallback this assertion would be
        # vacuous — it would pass while every real builder was parked.
        #
        # It is the only test here that reads live fleet state, and it is named so a
        # failure reads as "the fleet config no longer offers a local builder", not
        # as scheduler nondeterminism. That confusion is what made the original
        # failure look intermittent.
        machines = load_machines(str(REPO_ROOT / "fleet" / "inventory.toml"),
                                 str(REPO_ROOT / "hearth" / "etc" / "backends.toml"))
        real_locals = [m.name for m in machines
                       if m.kind == "local" and m.available and m.name != _SYNTHETIC_LOCAL]
        self.assertTrue(real_locals,
                        "fleet/inventory.toml names no schedulable local builder: every "
                        "local node is schedulable = false. The scheduler will fall back "
                        "to the synthetic local option, so advisory proposals no longer "
                        "name a machine that exists. Clear a `schedulable = false`, or "
                        "retire this guard deliberately.")


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
    """The provider entry point, run under a HEARTH_SCOPE sandbox (no capacity.json).

    Every input is PINNED: the sandbox gets the declared _PINNED_INVENTORY /
    _PINNED_BACKENDS fixtures, never the live working-tree fleet config. These tests
    assert what the objective does with a given pool; whether the real fleet config
    still offers that pool is a separate question, asserted separately by
    LoadMachinesTests.test_real_inventory_offers_a_real_available_local_builder.
    """

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        # Machine loading still exercises the real file-reading path — the files it
        # reads are just declared here rather than sampled from the fleet's state.
        (self.scope / "fleet").mkdir(parents=True, exist_ok=True)
        (self.scope / "hearth" / "etc").mkdir(parents=True, exist_ok=True)
        self._write_inventory(_PINNED_INVENTORY)
        (self.scope / "hearth" / "etc" / "backends.toml").write_text(
            _PINNED_BACKENDS, encoding="utf-8")

    def _write_inventory(self, text: str) -> None:
        """Pin the sandbox's inventory. Tests that care about a particular pool
        shape call this rather than depending on whatever the fleet looks like."""
        (self.scope / "fleet" / "inventory.toml").write_text(text, encoding="utf-8")

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
        # The two-economies objective, over the PINNED pool: 100000s of slack means
        # nothing forces metered spend, so the free local machine must win on cost.
        # A failure here is now a statement about the objective and nothing else.
        jobs = [{"plan_id": "j1", "task_class": "build", "est_tokens": 9999,
                 "deadline_s": 100000}]
        result = propose_schedule(jobs)
        machine = result["proposal"]["assignments"][0]["machine"]
        kinds = {m["name"]: m["kind"] for m in result["machines_considered"]}
        self.assertEqual(kinds[machine], "local")
        self.assertEqual(result["proposal"]["est_metered_tokens"], 0)

    def test_slack_deadline_stays_local_is_stable_across_runs(self) -> None:
        # The flake this test class was rewritten for: the same declared inputs must
        # produce the same placement every time. Repeat it in-process so a stray
        # ambient read (or a solver that answered differently on a busy box) shows up
        # as a disagreement rather than as an intermittent failure someone re-runs away.
        jobs = [{"plan_id": "j1", "task_class": "build", "est_tokens": 9999,
                 "deadline_s": 100000}]
        placements = set()
        for _ in range(10):
            result = propose_schedule(jobs)
            placements.add((result["proposal"]["assignments"][0]["machine"],
                            result["proposal"]["est_metered_tokens"],
                            result["proposal"]["solver_status"]))
        self.assertEqual(len(placements), 1, f"placement varied across runs: {placements}")

    def test_parked_local_pool_still_keeps_slack_work_local(self) -> None:
        # The same failure one key over. `schedulable = false` is the purpose-built
        # exclusion key, and parking every real local builder with it used to empty
        # the schedulable local pool exactly the way expect="optional" once did —
        # load_machines only asked whether a local was NAMED, and a parked builder
        # still is. The pool guarantee has to survive a fleet that parks everything.
        self._write_inventory(_PINNED_INVENTORY_ALL_LOCALS_PARKED)
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
