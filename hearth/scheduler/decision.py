"""Turn a ScheduleProposal into a scheduler-decision.v1 record and validate it.

The v1 contract is single-dispatch shaped (one `selected` builder). A job-shop
proposal places MANY jobs across machines, so we conform by treating the whole batch
as one scheduling decision whose:
  - candidates_considered = the machines the solver weighed (selected=True for any
    machine that got at least one job),
  - selected              = the machine carrying the most work (the primary resource),
  - economy_influence     = where the two-economies objective is explained (tokens
    first, makespan second) with a counterfactual for the makespan-first alternative,
  - decision_reason       = a one-line human summary of the placement.

Validation reuses the same jsonschema/Draft202012 path the ledger uses, falling back
to a minimal structural check when jsonschema is unavailable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hearth.scheduler.ontology import Job, Machine, ScheduleProposal

_CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA_PATH = _CONTRACT_ROOT / "scheduler-decision.v1.schema.json"

_validator = None


def validate_decision(record: dict) -> None:
    """Validate a decision record against scheduler-decision.v1. Raises ValueError."""
    global _validator
    try:
        import jsonschema
    except ImportError:
        _validate_stdlib(record)
        return
    if _validator is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(_validator.iter_errors(record), key=str)
    if errors:
        raise ValueError(f"scheduler-decision.v1 validation failed: {errors[0].message}")


def _validate_stdlib(record: dict) -> None:
    required = {"contract_version", "decision_id", "workflow_id", "run_id", "timestamp",
                "workload_shape", "candidates_considered", "selected", "decision_reason"}
    missing = required - set(record)
    if missing:
        raise ValueError(f"scheduler-decision.v1 missing keys: {sorted(missing)}")
    if record["contract_version"] != "scheduler-decision.v1":
        raise ValueError("contract_version must be scheduler-decision.v1")
    if not record["candidates_considered"]:
        raise ValueError("candidates_considered must be non-empty")


def build_scheduler_decision(
    jobs: list[Job],
    machines: list[Machine],
    proposal: ScheduleProposal,
    *,
    decision_id: str = "sched_shadow_001",
    workflow_id: str = "wf_shadow",
    run_id: str = "run_shadow",
    timestamp: Optional[str] = None,
) -> dict:
    """Build a schema-valid scheduler-decision.v1 record for an advisory proposal."""
    ts = timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    placed = {a["plan_id"]: a["machine"] for a in proposal.assignments}
    load: dict[str, int] = {}
    for machine_name in placed.values():
        load[machine_name] = load.get(machine_name, 0) + 1

    available = [m for m in machines if m.available]
    candidates = []
    for machine in available:
        got_work = machine.name in load
        candidates.append({
            "builder_id": machine.name,
            "model_id": None,
            "backend": machine.kind,
            "filters_passed": True,
            "score": float(load.get(machine.name, 0)),
            "selected": got_work,
            "rejected_reason": None if got_work else "no job assigned to this machine",
        })
    # A schema-valid record needs at least one candidate; if the pool was empty the
    # solver would have returned INFEASIBLE and this record still explains that.
    if not candidates:
        candidates.append({
            "builder_id": "none", "model_id": None, "backend": None,
            "filters_passed": False, "score": None, "selected": False,
            "rejected_reason": "no available machines",
        })

    # Primary machine = the one carrying the most jobs (ties -> first placed).
    if load:
        primary = max(load, key=lambda name: load[name])
        primary_kind = next((m.kind for m in available if m.name == primary), None)
    else:
        primary = candidates[0]["builder_id"]
        primary_kind = candidates[0]["backend"]

    frontier_used = any(m.kind == "frontier" and m.name in load for m in available)
    total_tokens = sum(j.est_tokens or 0 for j in jobs)

    reason = (
        f"shadow job-shop over {len(jobs)} job(s) x {len(available)} machine(s): "
        f"status={proposal.solver_status}, makespan={proposal.makespan_s:.0f}s, "
        f"metered_tokens={proposal.est_metered_tokens} "
        f"({'frontier used to meet a deadline' if frontier_used else 'all-local (token objective wins)'})"
    )

    # Economy block: the objective the proposal optimized and the makespan-first
    # counterfactual (what a span-first objective would have preferred).
    fastest_machine = None
    if available:
        # A span-first objective would prefer the fastest machine outright — here,
        # frontier (unloaded, no queue) if one exists, else any local.
        fastest_machine = next((m.name for m in available if m.kind == "frontier"),
                               available[0].name)
    economy_influence = {
        "objective_selected": "cost_per_outcome",
        "signals_read": ["machine.token_cost_weight", "job.est_tokens", "job.deadline_s"],
        "reason": (
            "two-economies doctrine: metered tokens minimized first, makespan second. "
            f"total est_tokens={total_tokens}; metered (frontier) tokens spent="
            f"{proposal.est_metered_tokens}. "
            + ("A hard deadline forced a frontier placement."
               if frontier_used else "No deadline forced metered spend; kept all-local.")
        ),
        "counterfactual": {
            "objective": "knowledge_per_hour",
            "would_have_chosen": fastest_machine,
            "note": ("a makespan-first objective would prefer the fastest unconstrained "
                     "machine even at metered-token cost, rather than packing local builders"),
        },
    }

    record = {
        "contract_version": "scheduler-decision.v1",
        "decision_id": decision_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "timestamp": ts,
        "workload_shape": {
            "task_kind": "job-shop-batch",
            "estimated_context_tokens": total_tokens or None,
            "requires_gpu": None,
            "notes": f"advisory shadow schedule; {len(proposal.assignments)} placement(s)",
        },
        "candidates_considered": candidates,
        "selected": {
            "builder_id": primary,
            "model_id": None,
            "backend": primary_kind,
        },
        "decision_reason": reason,
        "predictions": {
            "runtime_s": proposal.makespan_s,
            "prediction_source": "cp-sat-shadow-scheduler",
        },
        "evidence_refs": [],
        "policy_influence": None,
        "capability_influence": None,
        "economy_influence": economy_influence,
    }
    return record
