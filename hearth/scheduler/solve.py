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

JS7b — setup-aware residency (Derek's insight: on AM4, model LOAD dominates runtime)
------------------------------------------------------------------------------------
A `stateful` machine (the AM4 box) carries model-residency STATE. A job with a
`required_model` M assigned to a stateful machine implies a LOAD interval unless M is
already resident:
  - ONE load per (machine, model): `load_used[m,M]` is the OR of every assignment of a
    job needing M to m. A model already in `resident_models` is pre-loaded (zero setup,
    no interval). Every job needing M on m must start at/after that load's end.
  - DDR4 staging: all load intervals sharing a physical host contend for a single
    staging slot (AddNoOverlap when staging_slots==1, else AddCumulative) — only one
    model may stream through system RAM at a time (32GB DDR4 << 64GB VRAM).
  - VRAM fit (v1, no eviction): loaded models stay resident to the horizon. Per card,
    sum(per_card_gb of initial residents + newly-loaded models placed on that card)
    <= card.vram_gb - HEADROOM. `dual` placement charges BOTH cards; `single` lets the
    solver pick one card (a boolean per card, exactly-one, charging the chosen one).
    A model that fits nowhere makes its host infeasible -> the job must go elsewhere,
    or the whole model is INFEASIBLE (surfaced in solver_status).
Loads count into makespan; the token objective is otherwise unchanged. When NO job
carries a required_model the entire JS7b layer is inert and schedules are byte-identical
to JS7a.

P2 / ADR-0045 — a rotating stateful host (OMEN, llama-swap)
-----------------------------------------------------------
  - roles: a machine with `roles` set only takes jobs whose task_class is listed
    (OMEN = ["inference"]; a build job never lands on the B70s). `roles is None`
    keeps every builder unrestricted, so proposals without OMEN are unchanged.
  - cold: setup for a (machine, model) load reads `machine.cold` — the first load
    inside a window (26.58 s for the 30B) instead of the steady 8.2 s.
  - kv_hydrate_s: a job with `kv_state_available` on a stateful machine pays the
    model's slot-restore time (1.19 s measured) inside its own interval, whether or
    not the model still has to load — the restore is per slot, the load per model.
  - exclusive groups (stretch, v1): two models sharing `exclusive_group` are never
    co-resident — at most one of them loads per machine, and a sibling of an
    initial resident cannot load at all (no eviction in v1): the job is ineligible
    there and, with nowhere else to go, the proposal is a LOUD INFEASIBLE whose
    `notes` say which model and group.
  - `loads[]` / `residency[]` carry the card's `bdf` beside its solver index, so a
    consumer never has to map an index back to a device (ADR-0042).
  - P2b: model names are CANONICALIZED — a job may name `phi4-vk1`, the alias or
    the model_id; loads, residency and VRAM are accounted once per physical model
    (`loads[].model_id` is the catalog id, `requested_as` the entry names used).
    `Machine.loadable_models` (its own catalog's keys) bounds what each stateful
    host may load, so two catalogs in one pool never plan a model onto the wrong
    host's cards.
"""

from __future__ import annotations

import math
from typing import Optional

from ortools.sat.python import cp_model

from hearth.scheduler.ontology import (
    Job,
    Machine,
    ModelSpec,
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

# VRAM headroom (GB) reserved per card below its nominal budget (drivers, fragmentation).
_VRAM_HEADROOM_GB = 0.5
# Default model load/setup time when the catalog gives no warmup figure.
_DEFAULT_SETUP_S = 30.0
# GB->integer scale for the VRAM budget constraint (0.1 GB resolution).
_GB_SCALE = 10


def _horizon_s(jobs: list[Job], durations: dict[tuple[str, str], int],
               setup_by_pair: dict[tuple[str, str], int]) -> int:
    """A safe upper bound on makespan: sum of every job's longest eligible duration,
    plus every possible (machine, model) load time (each load happens at most once)
    so the horizon still bounds start/end vars once setup intervals are stacked in."""
    total = 0
    for job in jobs:
        longest = max((d for (pid, _m), d in durations.items() if pid == job.plan_id),
                      default=0)
        total += longest
    total += sum(setup_by_pair.values())
    # Also respect the largest deadline so deadline constraints stay feasible to express.
    deadlines = [int(math.ceil(j.deadline_s)) for j in jobs if j.deadline_s is not None]
    return max(total, *deadlines, 1) if deadlines else max(total, 1)


def _host_key(machine: Machine) -> str:
    """Physical host for DDR4 staging contention. Falls back to the machine name."""
    return machine.host or machine.name


def solve_schedule(
    jobs: list[Job],
    machines: list[Machine],
    capacity: Optional[dict],
    time_limit_s: float = 10.0,
    models: Optional[dict] = None,
) -> ScheduleProposal:
    """Solve the advisory job-shop. Deterministic (single worker, fixed seed).

    `models` is the am4-catalog.v1 {model_id: ModelSpec} map (from
    ontology.load_am4_catalog); None -> stateless, JS7a-identical behavior.
    """
    models = models or {}
    available = [m for m in machines if m.available]
    by_name = {m.name: m for m in available}
    if not jobs:
        return ScheduleProposal([], 0.0, 0, "OPTIMAL", 0.0)
    if not available:
        return ScheduleProposal([], 0.0, 0, "INFEASIBLE", 0.0)

    model = cp_model.CpModel()
    notes: list[str] = []

    def _spec(model_id: str) -> Optional[ModelSpec]:
        return models.get(model_id)

    def _role_ok(job: Job, machine: Machine) -> bool:
        return machine.roles is None or job.task_class in machine.roles

    # Integer-second durations per (job, machine). A job with a saved KV slot on a
    # stateful machine pays the model's hydrate time inside its own interval.
    durations: dict[tuple[str, str], int] = {}
    kv_hydrate: dict[tuple[str, str], float] = {}
    for job in jobs:
        for machine in available:
            secs = lookup_duration_s(job, machine, capacity, models)
            if job.kv_state_available and machine.stateful and job.required_model:
                spec = _spec(job.required_model)
                if spec is not None and spec.kv_hydrate_s:
                    secs += float(spec.kv_hydrate_s)
                    kv_hydrate[(job.plan_id, machine.name)] = float(spec.kv_hydrate_s)
            durations[(job.plan_id, machine.name)] = max(1, int(math.ceil(secs)))

    # --- JS7b residency setup: which (machine, model) loads may exist -----------
    # A load is possible only for a STATEFUL machine and a model NOT already resident.
    # setup_s is the load duration (integer seconds), read for the machine's cold state.
    stateful = {m.name for m in available if m.stateful}

    def _setup_s(model_id: str, machine: Machine) -> float:
        spec = _spec(model_id)
        if spec is None:
            return _DEFAULT_SETUP_S
        return spec.setup_s(_DEFAULT_SETUP_S, cold=bool(machine.cold))

    def _group_of(model_id: str) -> Optional[str]:
        spec = _spec(model_id)
        return spec.exclusive_group if spec is not None else None

    # P2b: a job may name a model by its llama-swap ENTRY (`phi4-vk1`), its alias,
    # or its model_id; the catalog keys every spelling to one spec. Loads,
    # residency and VRAM are accounted per PHYSICAL model, so every comparison
    # below runs on the canonical model_id (an unknown name is its own canon).
    def _canon(name: str) -> str:
        spec = _spec(name)
        return spec.model_id if spec is not None else name

    resident_canon: dict[str, set[str]] = {
        m.name: {_canon(r) for r in m.resident_models} for m in available}

    def _is_resident(machine: Machine, model_id: str) -> bool:
        return _canon(model_id) in resident_canon[machine.name]

    def _loadable(machine: Machine, model_id: str) -> bool:
        """May this stateful machine load the model at all? None = any model in
        the solver's map (single-catalog behavior); else its own catalog's keys."""
        if machine.loadable_models is None:
            return True
        return _canon(model_id) in {_canon(n) for n in machine.loadable_models}

    # (machine, model) pairs that MIGHT need a load: job needs model, machine is
    # stateful, role-compatible, can load it, and does not already hold it. A model
    # whose exclusive_group is held by an initial resident cannot load (v1: no
    # eviction). Keyed by canonical model_id; `requested_as` remembers the entry
    # names the jobs used so the rotation plan can name the swap entry.
    load_pairs: set[tuple[str, str]] = set()
    requested_as: dict[tuple[str, str], set[str]] = {}
    exclusive_refusals: set[tuple[str, str]] = set()
    for job in jobs:
        raw_name = job.required_model
        if not raw_name:
            continue
        M = _canon(raw_name)
        for machine in available:
            if machine.name not in stateful or _is_resident(machine, M):
                continue
            if not _role_ok(job, machine) or not _loadable(machine, M):
                continue
            group = _group_of(M)
            if group is not None and any(
                    _group_of(r) == group and r != M for r in resident_canon[machine.name]):
                if (machine.name, M) not in exclusive_refusals:
                    exclusive_refusals.add((machine.name, M))
                    notes.append(
                        f"{M} cannot load on {machine.name}: exclusive_group "
                        f"{group!r} is held by a resident model and v1 does not evict")
                continue
            load_pairs.add((machine.name, M))
            if raw_name != M:
                requested_as.setdefault((machine.name, M), set()).add(raw_name)

    setup_by_pair: dict[tuple[str, str], int] = {}
    setup_s_by_pair: dict[tuple[str, str], float] = {}
    for (mname, M) in load_pairs:
        secs = _setup_s(M, by_name[mname])
        setup_s_by_pair[(mname, M)] = secs
        setup_by_pair[(mname, M)] = max(1, int(math.ceil(secs)))

    horizon = _horizon_s(jobs, durations, setup_by_pair)

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

        # Eligibility: a job naming a required_model can only run on machines
        # that can actually serve it — stateful machines that either already
        # hold the model or have it in the catalog to load (and are not refused
        # by an exclusive group). Without this, jobs "escape" to stateless
        # machines and dodge the load cost entirely. No stateful machine at all
        # (catalog absent) -> nothing to enforce against; degrade to stateless
        # behavior rather than INFEASIBLE. Roles apply to every job: a machine
        # that declares them only takes the task classes it lists.
        if job.required_model and stateful:
            wanted = _canon(job.required_model)
            eligible = [
                m for m in available
                if m.stateful and _role_ok(job, m) and (
                    _is_resident(m, wanted)
                    or (job.required_model in models
                        and (m.name, wanted) in load_pairs)
                )
            ]
            if not eligible:
                notes.append(
                    f"{job.plan_id}: no stateful machine can serve required_model "
                    f"{job.required_model!r}")
                return ScheduleProposal([], 0.0, 0, "INFEASIBLE", 0.0, notes=notes)
        else:
            eligible = [m for m in available if _role_ok(job, m)]
            if not eligible:
                notes.append(
                    f"{job.plan_id}: no machine declares a role for task_class "
                    f"{job.task_class!r}")
                return ScheduleProposal([], 0.0, 0, "INFEASIBLE", 0.0, notes=notes)

        lits = []
        for machine in eligible:
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

    # --- JS7b load intervals: one per (machine, model), shared across jobs -------
    # load_used[m,M] = OR of the assignments of jobs needing M to m. The optional
    # load interval exists on that literal; every such job must start at/after the
    # load's end. Loads also feed the per-machine no-overlap (a load occupies the
    # machine) and the per-host DDR4 staging contention.
    load_used: dict[tuple[str, str], cp_model.IntVar] = {}
    load_end: dict[tuple[str, str], cp_model.IntVar] = {}
    load_start: dict[tuple[str, str], cp_model.IntVar] = {}
    loads_by_host: dict[str, list] = {}

    for (mname, M) in sorted(load_pairs):
        machine = by_name[mname]
        # jobs that would need model M on machine mname
        needing = [j for j in jobs
                   if j.required_model and _canon(j.required_model) == M
                   and (j.plan_id, mname) in presence]
        if not needing:  # no eligible job could land here; no load needed
            continue
        assign_lits = [presence[(j.plan_id, mname)] for j in needing]

        used = model.NewBoolVar(f"loaduse_{mname}_{M}")
        load_used[(mname, M)] = used
        # used == OR(assign_lits): used implies at least one assignment; each
        # assignment implies used.
        model.AddBoolOr(assign_lits).OnlyEnforceIf(used)
        for lit in assign_lits:
            model.AddImplication(lit, used)
        model.AddBoolAnd([lit.Not() for lit in assign_lits]).OnlyEnforceIf(used.Not())

        setup = setup_by_pair[(mname, M)]
        ls = model.NewIntVar(0, horizon, f"ldstart_{mname}_{M}")
        le = model.NewIntVar(0, horizon, f"ldend_{mname}_{M}")
        load_start[(mname, M)] = ls
        load_end[(mname, M)] = le
        load_iv = model.NewOptionalIntervalVar(ls, setup, le, used, f"ldiv_{mname}_{M}")
        # A load occupies the machine (it cannot run a job while streaming a model).
        intervals_by_machine[mname].append(load_iv)
        loads_by_host.setdefault(_host_key(machine), []).append((load_iv, used))

        # Every job needing M on this machine starts at/after the load end.
        for j in needing:
            model.Add(job_start[j.plan_id] >= le).OnlyEnforceIf(presence[(j.plan_id, mname)])

    # no-overlap per machine (job-shop: one job — or one load — at a time)
    for machine in available:
        model.AddNoOverlap(intervals_by_machine[machine.name])

    # --- P2 exclusive groups (v1): at most one member of a group loads per machine.
    groups: dict[tuple[str, str], list] = {}
    for (mname, M), used in load_used.items():
        group = _group_of(M)
        if group is not None:
            groups.setdefault((mname, group), []).append(used)
    for (_mname, _group), lits in groups.items():
        if len(lits) > 1:
            model.Add(sum(lits) <= 1)

    # --- JS7b DDR4 staging: one model streams through a host's RAM at a time -----
    for host, entries in loads_by_host.items():
        intervals = [iv for (iv, _u) in entries]
        if len(intervals) < 2:
            continue
        # staging_slots taken from any stateful machine on this host (all share DDR4).
        slots = 1
        for m in available:
            if m.stateful and _host_key(m) == host:
                slots = max(1, int(m.staging_slots))
                break
        if slots <= 1:
            model.AddNoOverlap(intervals)
        else:
            demands = [1] * len(intervals)
            model.AddCumulative(intervals, demands, slots)

    # --- JS7b per-card VRAM fit (v1, no eviction) -------------------------------
    # For each stateful machine and each card, sum the per-card GB of initial
    # residents + newly-loaded models placed on that card <= budget - headroom.
    # `single` placement introduces a per-card boolean (exactly-one) selecting the
    # card; `dual` charges both cards unconditionally (when loaded).
    residency_plan: dict[str, dict[str, dict]] = {}  # machine -> model -> {cards, card_lits}
    for machine in available:
        if not machine.stateful or not machine.cards:
            continue
        cards = machine.cards
        card_indices = [int(c.get("index", i)) for i, c in enumerate(cards)]
        budget_scaled = {
            int(c.get("index", i)): int(round(
                (float(c.get("vram_gb", 0.0)) - _VRAM_HEADROOM_GB) * _GB_SCALE))
            for i, c in enumerate(cards)
        }
        # models this machine might hold: initial residents + loadable ones.
        machine_models: list[str] = list(dict.fromkeys(
            [_canon(r) for r in machine.resident_models]
            + [M for (mn, M) in load_pairs if mn == machine.name]))

        # per (model, card) charge literal: 1 if model resident on that card.
        # accumulate expressions per card.
        card_terms: dict[int, list] = {ci: [] for ci in card_indices}
        residency_plan[machine.name] = {}

        for M in machine_models:
            spec = _spec(M)
            charge_scaled = int(round((spec.card_charge_gb() if spec else 0.0) * _GB_SCALE))
            placement = spec.placement if spec else "single"
            is_initial = _is_resident(machine, M)
            if is_initial:
                loaded_lit = None  # always resident
            else:
                loaded_lit = load_used.get((machine.name, M))
                if loaded_lit is None:
                    continue  # not loadable here

            if placement == "dual":
                # charge every card whenever resident.
                chosen_cards = card_indices
                card_lits = {}
                for ci in card_indices:
                    if loaded_lit is None:
                        card_terms[ci].append(charge_scaled)  # constant
                    else:
                        card_terms[ci].append(charge_scaled * loaded_lit)
                residency_plan[machine.name][M] = {
                    "placement": "dual", "cards": chosen_cards,
                    "loaded_lit": loaded_lit, "card_lits": None,
                    "charge_scaled": charge_scaled,
                }
            else:
                # single: pick exactly one card (when resident) and charge it there.
                per_card = {}
                for ci in card_indices:
                    b = model.NewBoolVar(f"oncard_{machine.name}_{M}_{ci}")
                    per_card[ci] = b
                    card_terms[ci].append(charge_scaled * b)
                if loaded_lit is None:
                    # initial resident: exactly one card holds it.
                    model.Add(sum(per_card.values()) == 1)
                else:
                    # on exactly one card iff loaded, none otherwise.
                    model.Add(sum(per_card.values()) == loaded_lit)
                residency_plan[machine.name][M] = {
                    "placement": "single", "cards": None,
                    "loaded_lit": loaded_lit, "card_lits": per_card,
                    "charge_scaled": charge_scaled,
                }

        # budget per card
        for ci in card_indices:
            terms = card_terms[ci]
            if terms:
                model.Add(sum(terms) <= budget_scaled[ci])

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

    # makespan (jobs AND loads count into the span)
    span_ends = [job_end[job.plan_id] for job in jobs]
    span_ends.extend(load_end.values())
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, span_ends)

    # objective: metered tokens first, makespan second
    token_terms = []
    for job in jobs:
        tokens = job.est_tokens or job.est_out_tokens or 0
        for machine in available:
            key = (job.plan_id, machine.name)
            if key not in presence:  # machine not eligible for this job
                continue
            scaled_weight = int(round(machine.token_cost_weight * _TOKEN_WEIGHT_SCALE))
            cost = tokens * scaled_weight  # metered cost if this job lands on this machine
            if cost:
                token_terms.append(cost * presence[key])
    model.Minimize(W_TOKENS * sum(token_terms) + W_SPAN * makespan)

    solver = cp_model.CpSolver()
    # Caveat on the determinism claim above: one worker and a fixed seed make the
    # SEARCH deterministic, but max_time_in_seconds is WALL CLOCK, so a model large
    # enough to hit the limit could stop on a different incumbent on a loaded box.
    # Measured 2026-08-24: the advisory sizes we solve finish in ~170ms worst case
    # under full 24-core saturation, ~59x inside this budget, so the cutoff is not
    # reachable today. If it ever becomes so, max_deterministic_time is the knob.
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1        # deterministic
    solver.parameters.random_seed = 1
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if exclusive_refusals:
            notes.append("exclusive-group refusals contributed to the INFEASIBLE result")
        return ScheduleProposal([], 0.0, 0, status_name, 0.0, notes=notes)

    assignments = []
    metered_tokens = 0
    for job in jobs:
        for machine in available:
            key = (job.plan_id, machine.name)
            if key not in presence:
                continue
            if solver.BooleanValue(presence[key]):
                start_s = solver.Value(job_start[job.plan_id])
                end_s = solver.Value(job_end[job.plan_id])
                row = {
                    "plan_id": job.plan_id,
                    "machine": machine.name,
                    "start_s": float(start_s),
                    "end_s": float(end_s),
                }
                if key in kv_hydrate:
                    row["kv_hydrate_s"] = kv_hydrate[key]
                assignments.append(row)
                if machine.token_cost_weight > 0:
                    metered_tokens += (job.est_tokens or job.est_out_tokens or 0)
                break

    def _bdf_of(machine: Machine, index: int) -> Optional[str]:
        for i, c in enumerate(machine.cards):
            if int(c.get("index", i)) == index:
                bdf = c.get("bdf")
                return str(bdf) if bdf else None
        return None

    # --- JS7b outputs: loads + per-card residency summary -----------------------
    loads_out = []
    for (mname, M) in sorted(load_pairs):
        used = load_used.get((mname, M))
        if used is None or not solver.BooleanValue(used):
            continue
        plan = residency_plan.get(mname, {}).get(M, {})
        if plan.get("placement") == "dual":
            cards = plan.get("cards") or []
        else:
            card_lits = plan.get("card_lits") or {}
            cards = [ci for ci, b in card_lits.items() if solver.BooleanValue(b)]
        machine = by_name[mname]
        cards = sorted(cards)
        row = {
            "machine": mname,
            "model_id": M,
            "cards": cards,
            "start_s": float(solver.Value(load_start[(mname, M)])),
            "end_s": float(solver.Value(load_end[(mname, M)])),
        }
        bdfs = [_bdf_of(machine, ci) for ci in cards]
        if any(bdfs):
            row["bdfs"] = bdfs
        row["setup_s"] = round(setup_s_by_pair[(mname, M)], 3)
        row["cold"] = bool(machine.cold)
        if requested_as.get((mname, M)):
            row["requested_as"] = sorted(requested_as[(mname, M)])
        loads_out.append(row)

    residency_out = []
    for machine in available:
        if not machine.stateful or not machine.cards:
            continue
        plan = residency_plan.get(machine.name, {})
        for card in machine.cards:
            ci = int(card.get("index", 0))
            budget_gb = float(card.get("vram_gb", 0.0))
            resident_here: list[str] = []
            used_scaled = 0
            for M, info in plan.items():
                on_card = False
                if info["placement"] == "dual":
                    lit = info["loaded_lit"]
                    if lit is None or solver.BooleanValue(lit):
                        on_card = ci in (info.get("cards") or [])
                else:
                    b = (info.get("card_lits") or {}).get(ci)
                    on_card = b is not None and solver.BooleanValue(b)
                if on_card:
                    resident_here.append(M)
                    used_scaled += info["charge_scaled"]
            row = {
                "machine": machine.name,
                "card": ci,
                "resident_models": sorted(resident_here),
                "used_gb": round(used_scaled / _GB_SCALE, 2),
                "budget_gb": budget_gb,
            }
            if card.get("bdf"):
                row["bdf"] = str(card["bdf"])
            residency_out.append(row)

    return ScheduleProposal(
        assignments=assignments,
        makespan_s=float(solver.Value(makespan)),
        est_metered_tokens=metered_tokens,
        solver_status=status_name,
        objective_value=float(solver.ObjectiveValue()),
        loads=loads_out,
        residency=residency_out,
        notes=notes,
    )
