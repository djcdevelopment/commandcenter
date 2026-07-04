"""Image-gen scheduling experiment: CP-SAT scheduler vs FIFO vs smart-baseline.

Refactored for repo fixture use. Expose run_experiment(requests) -> dict.
"""
from __future__ import annotations

import json
from pathlib import Path

from hearth.scheduler.ontology import Job, Machine, ModelSpec
from hearth.scheduler.solve import solve_schedule


# --- 1. ModelSpecs for the 3 image models --------------------------------
# placement single; per_card_gb from real AM4 Arc Pro B70 estimates; warmup_ms_p50
# encodes the load time the scheduler charges via ModelSpec.setup_s().
MODELS: dict[str, ModelSpec] = {
    "sd3.5_large": ModelSpec(model_id="sd3.5_large", placement="single",
                             per_card_gb=16.5, warmup_ms_p50=25000.0),
    "sdxl-base":   ModelSpec(model_id="sdxl-base", placement="single",
                             per_card_gb=7.0, warmup_ms_p50=10000.0),
    "flux-schnell": ModelSpec(model_id="flux-schnell", placement="single",
                              per_card_gb=12.0, warmup_ms_p50=15000.0),
}
LOAD_S = {"sd3.5_large": 25.0, "sdxl-base": 10.0, "flux-schnell": 15.0}

# --- 2. Duration heuristic -----------------------------------------------
# seconds = steps * (width*height / 1024^2) * batch_size * k_model
# k calibrated so at 1024^2, 1 step ~= k seconds:
#   sd3.5_large ~0.55 (30 steps ~= 16.5s), sdxl ~0.30, flux-schnell ~0.18
K_MODEL = {"sd3.5_large": 0.55, "sdxl-base": 0.30, "flux-schnell": 0.18}


def duration_s(req: dict) -> float:
    k = K_MODEL[req["model"]]
    px_ratio = (req["width"] * req["height"]) / (1024.0 * 1024.0)
    return req["steps"] * px_ratio * req["batch_size"] * k


# --- capacity doc: one bucket per request, task_class = "img-<id>" --------
def build_capacity(reqs: list[dict]) -> dict:
    buckets = []
    for r in reqs:
        buckets.append({
            "task_class": f"tc-{r['request_id']}",
            "node": "am4-imagegen",
            "duration_ms": {"p90": duration_s(r) * 1000.0},
        })
    return {"contract_version": "capacity.v1", "buckets": buckets}


def make_machine() -> Machine:
    return Machine(
        name="am4-imagegen", kind="local", token_cost_weight=0.0,
        tags=["imagegen"], available=True, stateful=True,
        cards=[{"index": 0, "vram_gb": 32.0}, {"index": 1, "vram_gb": 32.0}],
        resident_models=[], staging_slots=1, host="am4",
    )


def make_jobs(reqs: list[dict]) -> list[Job]:
    jobs = []
    for r in reqs:
        jobs.append(Job(
            plan_id=r["request_id"],
            task_class=f"tc-{r['request_id']}",
            deadline_s=r.get("deadline_s"),
            required_model=r["model"],
            est_tokens=0,
        ))
    return jobs


# --- metrics helpers ------------------------------------------------------
def deadline_misses(reqs: list[dict], end_by_id: dict[str, float]) -> int:
    miss = 0
    for r in reqs:
        dl = r.get("deadline_s")
        if dl is not None and end_by_id.get(r["request_id"], 0.0) > dl + 1e-6:
            miss += 1
    return miss


# =========================================================================
# SCHEDULER ARM
# =========================================================================
def scheduler_arm(reqs: list[dict]) -> dict:
    capacity = build_capacity(reqs)
    jobs = make_jobs(reqs)

    # Try full 250 at once; if not OPTIMAL/FEASIBLE, roll windows of 50.
    machine = make_machine()
    prop = solve_schedule(jobs, [machine], capacity, time_limit_s=120.0, models=MODELS)
    mode = "single-shot-250"
    if prop.solver_status not in ("OPTIMAL", "FEASIBLE"):
        return _scheduler_rolling(reqs, capacity, mode_reason=prop.solver_status)

    return _summarize_scheduler(reqs, [prop], mode, [make_machine()])


def _scheduler_rolling(reqs: list[dict], capacity: dict, mode_reason: str) -> dict:
    """Rolling windows of 50; carry resident_models across windows; offset time."""
    props = []
    machines_used = []
    resident: list[str] = []
    time_offset = 0.0
    W = 50
    for i in range(0, len(reqs), W):
        chunk = reqs[i:i + W]
        m = make_machine()
        m.resident_models = list(resident)
        jobs = make_jobs(chunk)
        p = solve_schedule(jobs, [m], capacity, time_limit_s=120.0, models=MODELS)
        props.append((p, time_offset, m))
        machines_used.append(m)
        # advance offset by this window's makespan; carry residency = models loaded
        # in this window (single machine, so union of required models present)
        for r in chunk:
            if r["model"] not in resident:
                resident.append(r["model"])
        # VRAM cap: 2 cards, can't hold all; keep only last-loaded that fit — but
        # for state-carry we approximate by keeping models whose per_card fits;
        # simplest faithful carry: keep the set loaded (solver enforces per window).
        time_offset += p.makespan_s
    return _summarize_scheduler_rolling(reqs, props, mode_reason)


def _summarize_scheduler(reqs, props, mode, machines) -> dict:
    prop = props[0]
    end_by_id = {a["plan_id"]: a["end_s"] for a in prop.assignments}
    load_count = {}
    total_setup = 0.0
    for ld in prop.loads:
        load_count[ld["model_id"]] = load_count.get(ld["model_id"], 0) + 1
        total_setup += ld["end_s"] - ld["start_s"]
    return {
        "arm": "scheduler",
        "mode": mode,
        "solver_status": prop.solver_status,
        "makespan_s": round(prop.makespan_s, 1),
        "load_counts": load_count,
        "total_loads": sum(load_count.values()),
        "total_setup_s": round(total_setup, 1),
        "deadline_misses": deadline_misses(reqs, end_by_id),
    }


def _summarize_scheduler_rolling(reqs, props, mode_reason) -> dict:
    end_by_id = {}
    load_count = {}
    total_setup = 0.0
    makespan = 0.0
    statuses = []
    for (p, offset, m) in props:
        statuses.append(p.solver_status)
        for a in p.assignments:
            end_by_id[a["plan_id"]] = a["end_s"] + offset
        for ld in p.loads:
            load_count[ld["model_id"]] = load_count.get(ld["model_id"], 0) + 1
            total_setup += ld["end_s"] - ld["start_s"]
        makespan += p.makespan_s
    return {
        "arm": "scheduler",
        "mode": f"rolling-50 (single-shot fell back: {mode_reason})",
        "solver_status": "/".join(sorted(set(statuses))),
        "makespan_s": round(makespan, 1),
        "load_counts": load_count,
        "total_loads": sum(load_count.values()),
        "total_setup_s": round(total_setup, 1),
        "deadline_misses": deadline_misses(reqs, end_by_id),
    }


# =========================================================================
# BASELINE ARM (FIFO): single ComfyUI queue, one job at a time, tracks ONE
# resident model, swaps (pays load) whenever next request's model differs.
# =========================================================================
def fifo_arm(reqs: list[dict], name: str, order: list[dict]) -> dict:
    t = 0.0
    resident = None
    load_count = {}
    total_setup = 0.0
    end_by_id = {}
    for r in order:
        if r["model"] != resident:
            load = LOAD_S[r["model"]]
            t += load
            total_setup += load
            load_count[r["model"]] = load_count.get(r["model"], 0) + 1
            resident = r["model"]
        t += duration_s(r)
        end_by_id[r["request_id"]] = t
    return {
        "arm": name,
        "mode": "serial single-queue, 1 resident model",
        "solver_status": "n/a (simulation)",
        "makespan_s": round(t, 1),
        "load_counts": load_count,
        "total_loads": sum(load_count.values()),
        "total_setup_s": round(total_setup, 1),
        "deadline_misses": deadline_misses(reqs, end_by_id),
    }


# =========================================================================
def run_experiment(requests: list[dict]) -> dict:
    """Run the 3-arm comparison: scheduler, FIFO, smart-baseline.

    Args:
        requests: List of image-gen request dicts.

    Returns:
        Dict with results.arms: [fifo, smart, scheduler] and metadata.
    """
    sched = scheduler_arm(requests)
    fifo = fifo_arm(requests, "fifo-baseline", requests)  # file order
    smart_order = sorted(requests, key=lambda r: r["model"])
    smart = fifo_arm(requests, "smart-baseline", smart_order)  # sort-by-model batching

    arms = [fifo, smart, sched]

    # % improvement vs FIFO baseline
    base_span = fifo["makespan_s"]
    base_setup = fifo["total_setup_s"]
    for a in arms:
        a["makespan_improvement_pct_vs_fifo"] = round(
            100.0 * (base_span - a["makespan_s"]) / base_span, 1)
        a["setup_reduction_pct_vs_fifo"] = round(
            100.0 * (base_setup - a["total_setup_s"]) / base_setup, 1) if base_setup else 0.0

    return {
        "input_requests": len(requests),
        "duration_heuristic": {
            "formula": "seconds = steps * (width*height / 1024^2) * batch_size * k_model",
            "k_model": K_MODEL,
            "load_s": LOAD_S,
        },
        "machine": "am4-imagegen (host am4, 2x32GB cards, staging_slots=1, cold start)",
        "arms": arms,
    }


if __name__ == "__main__":
    # Load fixture and run experiment.
    fixture_path = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "imagegen_requests_250.json"
    requests = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = run_experiment(requests)
    print(json.dumps(result, indent=2))
