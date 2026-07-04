"""CP-SAT job-shop model. Pure: takes jobs + machines + a capacity doc, returns a
ScheduleProposal. No I/O, no dispatch.

Model
-----
For each (job x eligible machine) we create an OPTIONAL interval whose presence
literal `x[j,m]` means "job j runs on machine m". Constraints:
  - exactly-one: sum_m x[j,m] == 1  (every job placed on one machine)
  - no-overlap:  AddNoOverlap over each machine's optional intervals (one job at a
                 time per machine — the job-shop resource constraint)
  - precedence:  end(pred) <= start(succ)  (across whichever machines they land on)
  - deadline:    end(job) <= deadline_s    (hard, when given)

Objective (two-economies doctrine — metered tokens first, makespan second):

    minimize  W_TOKENS * sum_over_placed( est_tokens * token_cost_weight_scaled )
            + W_SPAN   * makespan

`token_cost_weight` is a float in [0,1] (0 = local/free, up to 1 = frontier/metered);
we scale it to an integer (x1000) for CP-SAT's integer objective. W_TOKENS is chosen
large enough that ANY avoidable frontier-token spend dominates makespan: no makespan
improvement can pay for even one metered token when a local placement is feasible.
Only a hard deadline that a job cannot meet on the (contended) local machines forces
a frontier placement — exactly the intended behavior.
"""

from __future__ import annotations

import math
from typing import Optional

from ortools.sat.python import cp_model

from hearth.scheduler.ontology import (
    Job,
    Machine,
    ScheduleProposal,
    lookup_duration_s,
)

# Objective weights. The token term is scaled by 1000 (weight resolution) and must
# dominate the span term across the whole horizon: W_TOKENS is set so one metered
# "token-weight unit" outweighs the largest makespan the horizon can produce.
_TOKEN_WEIGHT_SCALE = 1000  # token_cost_weight float -> int
W_SPAN = 1                  # makespan tiebreaker
# W_TOKENS multiplies (est_tokens * scaled_weight). Kept modest per-unit but, because
# est_tokens is typically thousands and the scaled weight is up to 1000, the token term
# reaches millions while makespan stays in the horizon range (seconds) — token wins.
W_TOKENS = 1


def _horizon_s(jobs: list[Job], durations: dict[tuple[str, str], int]) -> int:
    """A safe upper bound on makespan: sum of every job's longest eligible duration."""
    total = 0
    for job in jobs:
        longest = max((d for (pid, _m), d in durations.items() if pid == job.plan_id),
                      default=0)
        total += longest
    # Also respect the largest deadline so deadline constraints stay feasible to express.
    deadlines = [int(math.ceil(j.deadline_s)) for j in jobs if j.deadline_s is not None]
    return max(total, *deadlines, 1) if deadlines else max(total, 1)


def solve_schedule(
    jobs: list[Job],
    machines: list[Machine],
    capacity: Optional[dict],
    time_limit_s: float = 10.0,
) -> ScheduleProposal:
    """Solve the advisory job-shop. Deterministic (single worker, fixed seed)."""
    available = [m for m in machines if m.available]
    by_name = {m.name: m for m in available}
    if not jobs:
        return ScheduleProposal([], 0.0, 0, "OPTIMAL", 0.0)
    if not available:
        return ScheduleProposal([], 0.0, 0, "INFEASIBLE", 0.0)

    model = cp_model.CpModel()

    # Integer-second durations per (job, machine).
    durations: dict[tuple[str, str], int] = {}
    for job in jobs:
        for machine in available:
            secs = lookup_duration_s(job, machine, capacity)
            durations[(job.plan_id, machine.name)] = max(1, int(math.ceil(secs)))

    horizon = _horizon_s(jobs, durations)

    # Per (job, machine): presence literal + optional interval; per job: start/end.
    presence: dict[tuple[str, str], cp_model.IntVar] = {}
    intervals_by_machine: dict[str, list] = {m.name: [] for m in available}
    job_start: dict[str, cp_model.IntVar] = {}
    job_end: dict[str, cp_model.IntVar] = {}

    for job in jobs:
        start = model.NewIntVar(0, horizon, f"start_{job.plan_id}")
        end = model.NewIntVar(0, horizon, f"end_{job.plan_id}")
        job_start[job.plan_id] = start
        job_end[job.plan_id] = end

        lits = []
        for machine in available:
            key = (job.plan_id, machine.name)
            lit = model.NewBoolVar(f"x_{job.plan_id}_{machine.name}")
            presence[key] = lit
            lits.append(lit)
            dur = durations[key]
            m_start = model.NewIntVar(0, horizon, f"s_{job.plan_id}_{machine.name}")
            m_end = model.NewIntVar(0, horizon, f"e_{job.plan_id}_{machine.name}")
            interval = model.NewOptionalIntervalVar(
                m_start, dur, m_end, lit, f"iv_{job.plan_id}_{machine.name}")
            intervals_by_machine[machine.name].append(interval)
            # When this machine is chosen, the job's start/end equal this interval's.
            model.Add(start == m_start).OnlyEnforceIf(lit)
            model.Add(end == m_end).OnlyEnforceIf(lit)
        # exactly-one machine per job
        model.Add(sum(lits) == 1)

    # no-overlap per machine (job-shop: one job at a time)
    for machine in available:
        model.AddNoOverlap(intervals_by_machine[machine.name])

    # precedence: predecessor end <= successor start
    plan_ids = {job.plan_id for job in jobs}
    for job in jobs:
        for pred in job.precedence:
            if pred in plan_ids:
                model.Add(job_end[pred] <= job_start[job.plan_id])

    # hard deadlines
    for job in jobs:
        if job.deadline_s is not None:
            model.Add(job_end[job.plan_id] <= int(math.ceil(job.deadline_s)))

    # makespan
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, [job_end[job.plan_id] for job in jobs])

    # objective: metered tokens first, makespan second
    token_terms = []
    for job in jobs:
        tokens = job.est_tokens or 0
        for machine in available:
            scaled_weight = int(round(machine.token_cost_weight * _TOKEN_WEIGHT_SCALE))
            cost = tokens * scaled_weight  # metered cost if this job lands on this machine
            if cost:
                token_terms.append(cost * presence[(job.plan_id, machine.name)])
    model.Minimize(W_TOKENS * sum(token_terms) + W_SPAN * makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1        # deterministic
    solver.parameters.random_seed = 1
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return ScheduleProposal([], 0.0, 0, status_name, 0.0)

    assignments = []
    metered_tokens = 0
    for job in jobs:
        for machine in available:
            if solver.BooleanValue(presence[(job.plan_id, machine.name)]):
                start_s = solver.Value(job_start[job.plan_id])
                end_s = solver.Value(job_end[job.plan_id])
                assignments.append({
                    "plan_id": job.plan_id,
                    "machine": machine.name,
                    "start_s": float(start_s),
                    "end_s": float(end_s),
                })
                if machine.token_cost_weight > 0:
                    metered_tokens += (job.est_tokens or 0)
                break

    return ScheduleProposal(
        assignments=assignments,
        makespan_s=float(solver.Value(makespan)),
        est_metered_tokens=metered_tokens,
        solver_status=status_name,
        objective_value=float(solver.ObjectiveValue()),
    )
