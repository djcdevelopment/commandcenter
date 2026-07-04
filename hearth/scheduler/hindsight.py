"""JS4: hindsight regret assay.

Replays completed historical runs through the JS3 CP-SAT scheduler (solve.py)
as if they had all arrived together, and compares what the solver WOULD have
proposed against what actually happened. Pure functions: no SSH, no network,
no dispatch. The gather (conductor -> run records) lives in the toolsurface
provider, imitating hearth/toolsurface/patrol.py's existing mechanism.

This is the evidence gate that decides whether the shadow scheduler (JS3) ever
earns actuation power: if replaying real history shows the solver would have
saved meaningful metered tokens or span without violating anything the actual
dispatch respected, that's the case for promotion. If not, JS3 stays advisory.

Regret is computed in the same two-economies order the solver optimizes:
tokens first (what would have been spent on frontier/metered machines under
the proposal, vs what was actually spent), then span (the actual runs' total
observed span vs the solver's makespan over the same batch).
"""
from __future__ import annotations

from hearth.scheduler.ontology import (
    DEFAULT_DURATIONS_S,
    Job,
    Machine,
    lookup_duration_s,
)
from hearth.scheduler.solve import solve_schedule

# Fallback estimate (tokens) for a run whose actual cost is unknown and whose
# winning machine isn't a recognized local builder — a declared default, not a
# measurement. Kept coarse and visible, matching DEFAULT_DURATIONS_S's spirit.
DEFAULT_EST_TOKENS = 2000

_LOCAL_BUILDER_NAMES = {"am4-worker-1", "cc-builder-1", "cc-builder-2"}


def _is_local_winner(winner: str | None, machines_by_name: dict[str, Machine]) -> bool:
    if winner is None:
        return False
    machine = machines_by_name.get(winner)
    if machine is not None:
        return machine.kind == "local"
    return winner in _LOCAL_BUILDER_NAMES


def _estimate_tokens_for_record(record: dict, capacity: dict | None) -> int:
    """Estimate actual metered tokens spent on a historical run.

    Preference order: an explicit `tokens_out` on the record (if the caller
    already resolved it from the ledger) > capacity-bucket
    tokens_out_per_s_p50 x duration_s > DEFAULT_EST_TOKENS.
    """
    explicit = record.get("tokens_out")
    if isinstance(explicit, (int, float)) and explicit >= 0:
        return int(explicit)

    duration_s = record.get("duration_s")
    task_class = record.get("task_class") or "build"
    winner = record.get("winner")
    if capacity is not None and isinstance(duration_s, (int, float)):
        for bucket in capacity.get("buckets", []):
            if bucket.get("node") not in (winner, None):
                continue
            if bucket.get("task_class") not in (task_class, None):
                continue
            rate = bucket.get("tokens_out_per_s_p50")
            if rate:
                return int(round(rate * duration_s))
    return DEFAULT_EST_TOKENS


def build_jobs_from_history(
    records: list[dict],
    machines: list[Machine] | None = None,
    capacity: dict | None = None,
) -> list[dict]:
    """Turn completed, successful historical run records into job specs.

    Completed = status == "ok", or no status at all with a winner (the
    conductor only stamps status on errored/abandoned/stub runs). Errored,
    stubbed, or still-in-flight runs carry no reliable "what actually
    happened" signal and are excluded. Each returned dict carries both the
    scheduler-shaped job fields (plan_id, task_class, est_tokens) AND the
    hindsight-only actual-outcome fields (actual_machine, actual_s,
    actual_tokens) needed later for regret comparison.
    """
    machines_by_name = {m.name: m for m in (machines or [])}
    jobs: list[dict] = []
    for record in records:
        if record.get("status") not in (None, "ok"):
            continue
        winner = record.get("winner")
        if not winner:
            continue
        plan_id = record.get("plan_id")
        if not plan_id:
            continue
        task_class = record.get("task_class") or "build"
        duration_s = record.get("duration_s")
        if not isinstance(duration_s, (int, float)) or duration_s < 0:
            duration_s = DEFAULT_DURATIONS_S.get(task_class, DEFAULT_DURATIONS_S["default"])

        is_local = _is_local_winner(winner, machines_by_name)
        actual_tokens = 0 if is_local else _estimate_tokens_for_record(record, capacity)

        jobs.append({
            "plan_id": str(plan_id),
            "task_class": task_class,
            "est_tokens": actual_tokens if actual_tokens else DEFAULT_EST_TOKENS,
            "actual_machine": winner,
            "actual_s": float(duration_s),
            "actual_tokens": actual_tokens,
        })
    return jobs


def render_table(report: dict) -> str:
    """Render a regret report as an aligned monospace table (human-readable)."""
    lines: list[str] = []
    header = f"{'plan_id':<24} {'actual_machine':<18} {'proposed_machine':<18} {'actual_s':>10} {'est_s':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in report.get("per_run", []):
        lines.append(
            f"{row['plan_id']:<24} {row['actual_machine']:<18} "
            f"{str(row['proposed_machine']):<18} {row['actual_s']:>10.1f} {row['est_s']:>10.1f}"
        )
    lines.append("-" * len(header))
    lines.append(f"n_runs={report['n_runs']}  solver_status={report['proposed']['solver_status']}")
    lines.append(
        f"actual:   span_s={report['actual']['span_s']:.1f}  "
        f"metered_tokens={report['actual']['metered_tokens']}"
    )
    lines.append(
        f"proposed: span_s={report['proposed']['span_s']:.1f}  "
        f"metered_tokens={report['proposed']['metered_tokens']}"
    )
    lines.append(
        f"regret:   tokens_saved={report['regret']['tokens_saved']}  "
        f"span_delta_s={report['regret']['span_delta_s']:.1f}"
    )
    return "\n".join(lines)


def replay(records: list[dict], machines: list[Machine], capacity: dict | None) -> dict:
    """Replay historical run `records` through the CP-SAT scheduler as a batch.

    Returns a regret report:
      {n_runs, actual:{span_s, metered_tokens}, proposed:{span_s, metered_tokens,
       solver_status}, regret:{tokens_saved, span_delta_s},
       per_run:[{plan_id, actual_machine, proposed_machine, actual_s, est_s}]}

    `tokens_saved` = actual metered tokens - proposed metered tokens (positive
    means the solver would have spent fewer metered tokens). `span_delta_s` =
    actual total span (sum of each run's own actual duration, i.e. what really
    elapsed run-by-run in the historical record) - proposed makespan (the
    solver's batch makespan, since the whole point of batching is parallelism
    the actual sequential/ad-hoc dispatch didn't necessarily exploit).
    """
    job_history = build_jobs_from_history(records, machines=machines, capacity=capacity)

    actual_span_s = sum(j["actual_s"] for j in job_history)
    actual_tokens = sum(j["actual_tokens"] for j in job_history)

    jobs = [
        Job(plan_id=j["plan_id"], task_class=j["task_class"], est_tokens=j["est_tokens"])
        for j in job_history
    ]
    proposal = solve_schedule(jobs, machines, capacity)

    proposed_machine_by_plan = {a["plan_id"]: a["machine"] for a in proposal.assignments}
    proposed_end_by_plan = {a["plan_id"]: a["end_s"] for a in proposal.assignments}
    proposed_start_by_plan = {a["plan_id"]: a["start_s"] for a in proposal.assignments}

    per_run = []
    for j in job_history:
        plan_id = j["plan_id"]
        proposed_machine = proposed_machine_by_plan.get(plan_id)
        if proposed_machine is not None:
            est_s = proposed_end_by_plan[plan_id] - proposed_start_by_plan[plan_id]
        else:
            est_s = 0.0
        per_run.append({
            "plan_id": plan_id,
            "actual_machine": j["actual_machine"],
            "proposed_machine": proposed_machine,
            "actual_s": j["actual_s"],
            "est_s": est_s,
        })

    tokens_saved = actual_tokens - proposal.est_metered_tokens
    span_delta_s = actual_span_s - proposal.makespan_s

    return {
        "n_runs": len(job_history),
        "actual": {
            "span_s": actual_span_s,
            "metered_tokens": actual_tokens,
        },
        "proposed": {
            "span_s": proposal.makespan_s,
            "metered_tokens": proposal.est_metered_tokens,
            "solver_status": proposal.solver_status,
        },
        "regret": {
            "tokens_saved": tokens_saved,
            "span_delta_s": span_delta_s,
        },
        "per_run": per_run,
    }
