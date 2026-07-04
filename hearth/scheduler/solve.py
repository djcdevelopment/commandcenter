"""CP-SAT scheduler: job placement and model load optimization."""
from __future__ import annotations

from typing import Any

from hearth.scheduler.ontology import Job, Machine, ModelSpec, ScheduleProposal


def solve_schedule(
    jobs: list[Job],
    machines: list[Machine],
    capacity: dict[str, Any],
    time_limit_s: float = 120.0,
    models: dict[str, ModelSpec] | None = None,
) -> ScheduleProposal:
    """
    Solve a scheduling problem using CP-SAT (or fallback).

    Args:
        jobs: List of jobs to schedule.
        machines: List of machines available.
        capacity: Capacity/duration contract document.
        time_limit_s: Solver time limit in seconds.
        models: ModelSpec catalog for load time lookups.

    Returns:
        ScheduleProposal with solver status, assignments, and load events.
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        # Fallback if ortools not available
        return _solve_fallback(jobs, machines, capacity, models or {})

    if not models:
        models = {}

    model = cp_model.CpModel()

    # Build a map: task_class -> duration_ms (p90)
    task_class_to_duration_ms = {}
    for bucket in capacity.get("buckets", []):
        tc = bucket["task_class"]
        dur_ms = bucket["duration_ms"]["p90"]
        task_class_to_duration_ms[tc] = dur_ms

    # Decision variables: for each job, which machine it runs on and when.
    job_interval_vars = {}
    for job in jobs:
        dur_s = task_class_to_duration_ms.get(job.task_class, 30000.0) / 1000.0
        dur_ms = int(dur_s * 1000)

        # For simplicity, assume job runs on the only machine.
        # Interval: [start, end) in milliseconds.
        start = model.NewIntVar(0, 500000, f"{job.plan_id}_start")
        job_interval_vars[job.plan_id] = (start, dur_ms, job)

    # No-overlap constraint: jobs on same machine don't overlap.
    if machines:
        m = machines[0]
        intervals = [
            model.NewIntervalVar(
                start, dur_ms, start + dur_ms, f"{plan_id}_interval"
            )
            for plan_id, (start, dur_ms, _) in job_interval_vars.items()
        ]
        model.AddNoOverlap(intervals)

    # Deadline constraints.
    for plan_id, (start, dur_ms, job) in job_interval_vars.items():
        if job.deadline_s is not None:
            deadline_ms = int(job.deadline_s * 1000)
            end_ms = start + dur_ms
            model.Add(end_ms <= deadline_ms)

    # Objective: minimize makespan (max end time).
    if job_interval_vars:
        makespan_ms = model.NewIntVar(0, 500000, "makespan")
        for start, dur_ms, _ in job_interval_vars.values():
            model.Add(makespan_ms >= start + dur_ms)
        model.Minimize(makespan_ms)

    # Solve.
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 4
    status = solver.Solve(model)

    # Translate status.
    status_str = "UNKNOWN"
    if status == cp_model.OPTIMAL:
        status_str = "OPTIMAL"
    elif status == cp_model.FEASIBLE:
        status_str = "FEASIBLE"
    elif status == cp_model.INFEASIBLE:
        status_str = "INFEASIBLE"
    elif status == cp_model.MODEL_INVALID:
        status_str = "MODEL_INVALID"

    # Extract solution.
    assignments = []
    makespan_s = 0.0
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for plan_id, (start, dur_ms, job) in job_interval_vars.items():
            start_s = solver.Value(start) / 1000.0
            end_s = start_s + dur_ms / 1000.0
            assignments.append({
                "plan_id": plan_id,
                "machine": machines[0].name if machines else "unknown",
                "start_s": start_s,
                "end_s": end_s,
            })
            makespan_s = max(makespan_s, end_s)

    # Model loads: when models are loaded (setup events).
    loads = _compute_loads(assignments, jobs, models)

    return ScheduleProposal(
        solver_status=status_str,
        makespan_s=makespan_s,
        assignments=assignments,
        loads=loads,
    )


def _compute_loads(
    assignments: list[dict],
    jobs: list[Job],
    models: dict[str, ModelSpec],
) -> list[dict]:
    """Compute model load events from assignments."""
    loads = []
    loaded_models = set()
    current_time = 0.0

    # Sort assignments by start time.
    sorted_assign = sorted(assignments, key=lambda a: a["start_s"])

    for assign in sorted_assign:
        job = next((j for j in jobs if j.plan_id == assign["plan_id"]), None)
        if not job:
            continue

        model_id = job.required_model
        if model_id not in loaded_models:
            # Load this model.
            model_spec = models.get(model_id)
            setup_s = model_spec.setup_s() if model_spec else 10.0
            load_start = current_time
            load_end = load_start + setup_s
            loads.append({
                "model_id": model_id,
                "start_s": load_start,
                "end_s": load_end,
            })
            loaded_models.add(model_id)
            current_time = load_end

    return loads


def _solve_fallback(
    jobs: list[Job],
    machines: list[Machine],
    capacity: dict,
    models: dict[str, ModelSpec],
) -> ScheduleProposal:
    """Fallback solver when ortools is unavailable (greedy FIFO)."""
    # Task class -> duration
    task_class_to_duration_ms = {}
    for bucket in capacity.get("buckets", []):
        tc = bucket["task_class"]
        dur_ms = bucket["duration_ms"]["p90"]
        task_class_to_duration_ms[tc] = dur_ms

    # Sort jobs by plan_id (stable).
    assignments = []
    time_s = 0.0
    loaded_models = set()

    for job in sorted(jobs, key=lambda j: j.plan_id):
        dur_s = task_class_to_duration_ms.get(job.task_class, 30000.0) / 1000.0

        # Load model if not resident.
        if job.required_model not in loaded_models:
            model_spec = models.get(job.required_model)
            if model_spec:
                time_s += model_spec.setup_s()
            loaded_models.add(job.required_model)

        end_s = time_s + dur_s
        assignments.append({
            "plan_id": job.plan_id,
            "machine": machines[0].name if machines else "unknown",
            "start_s": time_s,
            "end_s": end_s,
        })
        time_s = end_s

    loads = []
    return ScheduleProposal(
        solver_status="FEASIBLE",
        makespan_s=time_s,
        assignments=assignments,
        loads=loads,
    )
