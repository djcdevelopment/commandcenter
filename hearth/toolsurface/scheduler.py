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

import base64
import json
from pathlib import Path
from typing import Callable, Optional

from hearth.toolsurface._scope import resolve_in_scope, scope_root
from hearth.toolsurface.task_lane import CONDUCTOR_REPO, _run_ssh
from hearth.scheduler.decision import build_scheduler_decision, validate_decision
from hearth.scheduler.hindsight import render_table, replay
from hearth.scheduler.ontology import (
    Job,
    load_am4_catalog,
    load_capacity,
    load_machines,
    load_runner_classes,
)
from hearth.scheduler.solve import solve_schedule

DEFAULT_CAPACITY_PATH = "knowledge/capacity.json"
DEFAULT_AM4_CATALOG_PATH = "knowledge/am4_catalog.json"
_INVENTORY_REL = "fleet/inventory.toml"
_BACKENDS_REL = "hearth/etc/backends.toml"

# Runs on the conductor's python3; same shape as patrol.py's _GATHER_SRC (imitated,
# not re-invented) but filtered to completed ("ok") runs, newest first, bounded to
# `limit`. hindsight only wants runs whose actual outcome is knowable.
_GATHER_SRC_TEMPLATE = r'''
import json, os, time
now = time.time()
runs = "runs"
records = []
try:
    names = os.listdir(runs)
except FileNotFoundError:
    names = []
for name in names:
    d = os.path.join(runs, name)
    nodes = os.path.join(d, "nodes.json")
    if not os.path.isfile(nodes):
        continue
    res = os.path.join(d, "result.json")
    if not os.path.isfile(res):
        continue
    rec = {"plan_id": name, "age_s": round(now - os.path.getmtime(nodes))}
    try:
        rec["duration_s"] = round(os.path.getmtime(res) - os.path.getmtime(nodes))
        r = json.load(open(res))
        rec["status"] = r.get("status")
        rec["winner"] = r.get("winner")
        rec["task_class"] = r.get("task_class") or r.get("workflow_id") or "unknown"
    except Exception as e:
        rec["parse_error"] = str(e)[:120]
        rec["status"] = None
    records.append(rec)
records.sort(key=lambda x: x["age_s"])
# Completed = explicit ok, or the common success shape: no status key at all
# (conductor only stamps status on errored/abandoned/stub runs) but a winner.
records = [r for r in records if r.get("status") == "ok"
           or (r.get("status") is None and not r.get("parse_error") and r.get("winner"))][:LIMIT_PLACEHOLDER]
print(json.dumps({"records": records, "scanned": len(records)}))
'''


def _gather_completed_runs(limit: int, runner: Optional[Callable] = None):
    """Gather recent completed ('ok') run records from the conductor. Returns
    (payload, error), imitating hearth/toolsurface/patrol.py's gather mechanism."""
    src = _GATHER_SRC_TEMPLATE.replace("LIMIT_PLACEHOLDER", str(int(limit)))
    b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
    remote = f"cd {CONDUCTOR_REPO} && echo {b64} | base64 -d | python3 -"
    stdout, error = _run_ssh(remote, runner=runner)
    if error is not None:
        return None, error
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"non-JSON gather output: {exc}"
    return payload, None


def _job_from_dict(raw: dict) -> Job:
    if not isinstance(raw, dict) or not raw.get("plan_id"):
        raise ValueError("each job must be a dict with a non-empty plan_id")
    return Job(
        plan_id=str(raw["plan_id"]),
        task_class=str(raw.get("task_class") or "default"),
        precedence=[str(p) for p in (raw.get("precedence") or [])],
        deadline_s=raw.get("deadline_s"),
        est_tokens=raw.get("est_tokens"),
        required_model=(str(raw["required_model"]) if raw.get("required_model") else None),
        est_out_tokens=raw.get("est_out_tokens"),
        est_duration_s=raw.get("est_duration_s"),
    )


# The AM4 box — the one physically stateful machine (2x Arc B70, 32GB DDR4). Its
# logical builder name in the inventory. When the am4-catalog is present, THIS machine
# gains the catalog's cards as its VRAM budget and becomes `stateful`.
_AM4_MACHINE_NAME = "am4-worker-1"


def _apply_am4_catalog(machines: list, catalog: dict) -> None:
    """In place: make the AM4 machine stateful with the catalog's cards, if present."""
    cards = catalog.get("cards")
    if not cards:
        return
    for machine in machines:
        if machine.name == _AM4_MACHINE_NAME:
            machine.stateful = True
            machine.cards = [
                {"index": int(c.get("index", i)), "vram_gb": float(c.get("vram_gb", 0.0))}
                for i, c in enumerate(cards)
            ]
            machine.host = machine.host or _AM4_MACHINE_NAME
            break


def propose_schedule(jobs: list[dict], capacity_path: str = DEFAULT_CAPACITY_PATH,
                     am4_catalog_path: str = DEFAULT_AM4_CATALOG_PATH) -> dict:
    """Propose an advisory job-shop schedule for a snapshot of jobs (read-only).

    Solves jobs x eligible-machines with CP-SAT: exactly-one machine per job,
    no-overlap per machine, precedence and hard deadlines respected. Objective
    minimizes metered (frontier) token spend first and makespan second. Returns
    {ok, proposal, decision_record, machines_considered}; the decision_record
    conforms to and is validated against scheduler-decision.v1. Nothing is dispatched.

    JS7b: when `am4_catalog_path` (am4-catalog.v1) exists, the AM4 machine gains
    model-residency state — jobs naming a `required_model` pay a load/setup interval
    unless the model is resident, loads contend for a single DDR4 staging slot, and
    per-card VRAM is budgeted. Absent catalog -> stateless, JS7a-identical behavior.
    """
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list of job dicts")
    job_objs = [_job_from_dict(raw) for raw in jobs]

    root = scope_root()
    inventory_path = str(root / _INVENTORY_REL)
    backends_path = str(root / _BACKENDS_REL)
    machines = load_machines(inventory_path, backends_path)

    capacity = load_capacity(str(resolve_in_scope(capacity_path)))
    catalog = load_am4_catalog(str(resolve_in_scope(am4_catalog_path)))
    models = catalog.get("models") or {}
    _apply_am4_catalog(machines, catalog)

    proposal = solve_schedule(job_objs, machines, capacity, models=models)
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
            "loads": proposal.loads,
            "residency": proposal.residency,
        },
        "decision_record": decision,
        "machines_considered": [
            {"name": m.name, "kind": m.kind, "token_cost_weight": m.token_cost_weight,
             "available": m.available, "tags": m.tags}
            for m in machines
        ],
    }


def schedule_hindsight(records: Optional[list[dict]] = None, limit: int = 50,
                        capacity_path: str = DEFAULT_CAPACITY_PATH) -> dict:
    """Replay completed historical runs through the JS3 CP-SAT scheduler (JS4).

    This is the regret assay: it takes completed runs as if they'd all arrived
    together, asks the shadow scheduler what it would have proposed, and
    compares that against what actually happened (actual dispatch metered
    tokens + span vs the solver's proposed metered tokens + makespan). It is
    the evidence that decides whether the scheduler ever gets actuation power.

    When `records` is None, gathers the most recent `limit` completed ("ok")
    runs from the conductor over SSH (the same hop patrol.py uses) — read-only,
    no dispatch, no fleet mutation. When `records` is given, runs fully offline
    against the caller-supplied snapshot (used by tests and by callers who
    already have the data).

    Returns {ok, report, table} where `report` is the structured regret report
    (see hearth.scheduler.hindsight.replay) and `table` is an aligned
    monospace rendering of the same data for humans.
    """
    error = None
    if records is None:
        payload, error = _gather_completed_runs(limit)
        records = (payload or {}).get("records", []) if payload else []
    else:
        records = records[:limit]

    if error is not None:
        return {"ok": False, "error": error}

    root = scope_root()
    inventory_path = str(root / _INVENTORY_REL)
    backends_path = str(root / _BACKENDS_REL)
    machines = load_machines(inventory_path, backends_path)
    runner_classes = load_runner_classes(inventory_path)
    capacity = load_capacity(str(resolve_in_scope(capacity_path)))

    report = replay(records, machines, capacity, runner_classes=runner_classes)
    return {
        "ok": True,
        "report": report,
        "table": render_table(report),
    }


def get_tools() -> list[Callable]:
    return [propose_schedule, schedule_hindsight]
