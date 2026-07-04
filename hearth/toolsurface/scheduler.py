"""HEARTH tool provider: shadow scheduler (JS3).

`propose_schedule` is ADVISORY and read-only: it takes a caller-supplied snapshot of
jobs and returns a CP-SAT job-shop proposal plus a scheduler-decision.v1 record. It
never dispatches, never touches SSH, never mutates the fleet. Machines are loaded from
the OMEN-side inventory + backend pool (both tolerated missing); durations from the
projected knowledge/capacity.json (tolerated absent -> declared defaults).

Objective encodes the two-economies doctrine: metered frontier tokens are minimized
first, makespan second — local compute is treated as ~free.

Paths resolve inside HEARTH_SCOPE via resolve_in_scope, matching the rest of the
tool surface. Kernel-free by contract (providers never import the kernel package).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from hearth.toolsurface._scope import resolve_in_scope, scope_root
from hearth.scheduler.decision import build_scheduler_decision, validate_decision
from hearth.scheduler.ontology import Job, load_capacity, load_machines
from hearth.scheduler.solve import solve_schedule

DEFAULT_CAPACITY_PATH = "knowledge/capacity.json"
_INVENTORY_REL = "fleet/inventory.toml"
_BACKENDS_REL = "hearth/etc/backends.toml"


def _job_from_dict(raw: dict) -> Job:
    if not isinstance(raw, dict) or not raw.get("plan_id"):
        raise ValueError("each job must be a dict with a non-empty plan_id")
    return Job(
        plan_id=str(raw["plan_id"]),
        task_class=str(raw.get("task_class") or "default"),
        precedence=[str(p) for p in (raw.get("precedence") or [])],
        deadline_s=raw.get("deadline_s"),
        est_tokens=raw.get("est_tokens"),
    )


def propose_schedule(jobs: list[dict], capacity_path: str = DEFAULT_CAPACITY_PATH) -> dict:
    """Propose an advisory job-shop schedule for a snapshot of jobs (read-only).

    Solves jobs x eligible-machines with CP-SAT: exactly-one machine per job,
    no-overlap per machine, precedence and hard deadlines respected. Objective
    minimizes metered (frontier) token spend first and makespan second. Returns
    {ok, proposal, decision_record, machines_considered}; the decision_record
    conforms to and is validated against scheduler-decision.v1. Nothing is dispatched.
    """
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list of job dicts")
    job_objs = [_job_from_dict(raw) for raw in jobs]

    root = scope_root()
    inventory_path = str(root / _INVENTORY_REL)
    backends_path = str(root / _BACKENDS_REL)
    machines = load_machines(inventory_path, backends_path)

    capacity = load_capacity(str(resolve_in_scope(capacity_path)))

    proposal = solve_schedule(job_objs, machines, capacity)
    decision = build_scheduler_decision(job_objs, machines, proposal)
    validate_decision(decision)

    return {
        "ok": proposal.solver_status in ("OPTIMAL", "FEASIBLE"),
        "proposal": {
            "assignments": proposal.assignments,
            "makespan_s": proposal.makespan_s,
            "est_metered_tokens": proposal.est_metered_tokens,
            "solver_status": proposal.solver_status,
            "objective_value": proposal.objective_value,
        },
        "decision_record": decision,
        "machines_considered": [
            {"name": m.name, "kind": m.kind, "token_cost_weight": m.token_cost_weight,
             "available": m.available, "tags": m.tags}
            for m in machines
        ],
    }


def get_tools() -> list[Callable]:
    return [propose_schedule]
