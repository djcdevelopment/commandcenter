"""Rotation plan: what a stateful llama-swap host would have to do for a proposal.

Pure and advisory (ADR-0008: the scheduler advises, the conductor dispatches; the
rotation provider actuates only inside a ledgered window). This module turns a
ScheduleProposal over a rotating stateful Machine (OMEN, ADR-0045) into the
operator-readable step list the P11 proof captures verbatim:

    {machine, steps: [{t_s, action: load|kv_restore, model_id, swap_entry,
                       cards: [bdf], placement, est_s, est_s_first_in_window,
                       evidence, serves: [plan_id]}],
     blocked: [...], assumptions: [...]}

It is returned by the provider as a SIBLING key `rotation_plan` — never inside
`decision_record`, whose scheduler-decision.v1 schema is closed.

Two pre-solve helpers live here too, so the provider can move a job the host can
never hold into `blocked` (with the numbers) instead of letting the whole solve
collapse to INFEASIBLE:

  * `check_fit` — a model whose per-card charge exceeds what any card could ever
    free (budget - headroom - dual residents) is blocked on its own; definitive.
  * `cumulative_overflow` — only consulted AFTER the solver said INFEASIBLE: a
    best-fit-decreasing pack over the requested models names the ones that do not
    fit beside the others. An approximation, so it is never applied pre-emptively
    (it could block a job the solver would have placed) and its reason says so.

Every card is named by BDF where the catalog supplies one (ADR-0042: an index is a
solver slot, never a device identity). Stdlib only.
"""

from __future__ import annotations

from typing import Any, Optional

from hearth.scheduler.ontology import Job, Machine, ModelSpec, ScheduleProposal

# Mirrors solve._VRAM_HEADROOM_GB; the catalog's gates.vram_headroom_gb overrides.
DEFAULT_HEADROOM_GB = 0.5

_ENTRY_SUFFIXES = ("-vk1", "-vk2", "-dual")


def _headroom_gb(gates: Optional[dict]) -> float:
    if isinstance(gates, dict):
        value = gates.get("vram_headroom_gb")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return DEFAULT_HEADROOM_GB


def _card_label(machine: Machine, index: int) -> str:
    """BDF when the card declares one, else the solver index as a string."""
    for i, card in enumerate(machine.cards):
        if int(card.get("index", i)) == index:
            return str(card.get("bdf") or index)
    return str(index)


def _card_budgets(machine: Machine, headroom_gb: float) -> dict[int, float]:
    return {
        int(card.get("index", i)): float(card.get("vram_gb", 0.0)) - headroom_gb
        for i, card in enumerate(machine.cards)
    }


def _resident_charges(machine: Machine, models: dict[str, ModelSpec],
                      budgets: dict[int, float]) -> dict[int, float]:
    """Per-card GB already held at t=0. Dual residents charge every card; a single
    resident is placed on the card with the most room (deterministic, best case
    for the newcomer — the solver holds the exact answer)."""
    used = {index: 0.0 for index in budgets}
    if not used:
        return used
    for model_id in machine.resident_models:
        spec = models.get(model_id)
        if spec is None:
            continue
        charge = spec.card_charge_gb()
        if spec.placement == "dual":
            for index in used:
                used[index] += charge
        else:
            target = min(used, key=lambda index: (used[index] - budgets[index], index))
            used[target] += charge
    return used


def _fits(spec: ModelSpec, free: dict[int, float]) -> bool:
    charge = spec.card_charge_gb()
    if spec.placement == "dual":
        return bool(free) and all(room >= charge for room in free.values())
    return any(room >= charge for room in free.values())


def _blocked_row(job: Job, spec: ModelSpec, machine: Machine, free: dict[int, float],
                 budgets: dict[int, float], reason: str) -> dict:
    return {
        "plan_id": job.plan_id,
        "model_id": spec.model_id,
        "required_model": job.required_model,
        "machine": machine.name,
        "reason": reason,
        "placement": spec.placement,
        "per_card_gb": round(spec.card_charge_gb(), 2),
        "free_gb_by_card": {
            _card_label(machine, index): round(room, 2) for index, room in free.items()},
        "budget_gb_by_card": {
            _card_label(machine, index): round(room, 2) for index, room in budgets.items()},
        "resident_models": list(machine.resident_models),
    }


def check_fit(jobs: list[Job], models: dict[str, ModelSpec], machine: Machine,
              gates: Optional[dict] = None, *, exempt: Optional[set[str]] = None,
              ) -> tuple[list[Job], list[dict]]:
    """Split `jobs` into (fittable, blocked) for one stateful machine.

    A job is blocked when its required model, ALONE beside the t=0 residents,
    exceeds every card the placement needs (single: no card has room; dual: some
    card has none). Jobs without a required model, with a model the catalog does
    not know, with a model already resident, or naming a model in `exempt`
    (served by another stateful machine) are never blocked here.
    """
    if not machine.stateful or not machine.cards:
        return list(jobs), []
    headroom = _headroom_gb(gates)
    budgets = _card_budgets(machine, headroom)
    used = _resident_charges(machine, models, budgets)
    free = {index: budgets[index] - used[index] for index in budgets}
    exempt = exempt or set()
    kept: list[Job] = []
    blocked: list[dict] = []
    for job in jobs:
        model_id = job.required_model
        spec = models.get(model_id) if model_id else None
        if (spec is None or model_id in exempt or model_id in machine.resident_models
                or spec.model_id in machine.resident_models or _fits(spec, free)):
            kept.append(job)
            continue
        blocked.append(_blocked_row(
            job, spec, machine, free, budgets,
            reason=(f"over_full: {spec.placement} placement needs "
                    f"{spec.card_charge_gb():.2f} GB per card; no card can free it "
                    f"beside the residents (headroom {headroom:g} GB)")))
    return kept, blocked


def cumulative_overflow(jobs: list[Job], models: dict[str, ModelSpec], machine: Machine,
                        gates: Optional[dict] = None, *, exempt: Optional[set[str]] = None,
                        ) -> list[dict]:
    """Best-fit-decreasing pack of the requested models beside the residents; the
    models that do not fit are returned as blocked rows. Consult ONLY after the
    solver returned INFEASIBLE — it is an approximation of the solver's packing.
    """
    if not machine.stateful or not machine.cards:
        return []
    headroom = _headroom_gb(gates)
    budgets = _card_budgets(machine, headroom)
    used = _resident_charges(machine, models, budgets)
    exempt = exempt or set()
    wanted: dict[str, tuple[ModelSpec, list[Job]]] = {}
    for job in jobs:
        model_id = job.required_model
        spec = models.get(model_id) if model_id else None
        if (spec is None or model_id in exempt or model_id in machine.resident_models
                or spec.model_id in machine.resident_models):
            continue
        wanted.setdefault(spec.model_id, (spec, []))[1].append(job)
    order = sorted(wanted.values(), key=lambda pair: (-pair[0].card_charge_gb(), pair[0].model_id))
    blocked: list[dict] = []
    for spec, needing in order:
        charge = spec.card_charge_gb()
        free = {index: budgets[index] - used[index] for index in budgets}
        if spec.placement == "dual":
            if free and all(room >= charge for room in free.values()):
                for index in used:
                    used[index] += charge
                continue
        else:
            candidates = [index for index, room in free.items() if room >= charge]
            if candidates:
                # best fit: the card left with the least room afterwards
                target = min(candidates, key=lambda index: (free[index] - charge, index))
                used[target] += charge
                continue
        for job in needing:
            blocked.append(_blocked_row(
                job, spec, machine, free, budgets,
                reason=("cumulative_over_full: the solver found no packing for the full "
                        "request; a best-fit-decreasing pack beside the residents "
                        f"leaves no card with {charge:.2f} GB for this model "
                        "(approximation — re-run without the other loads to confirm)")))
    return blocked


def _swap_entry_for(required_name: Optional[str], spec: ModelSpec) -> Optional[str]:
    """The llama-swap entry the plan names: the job's own entry name when it used
    one, else the catalog's declared swap_entry, else None (asserted at load)."""
    if required_name and required_name != spec.model_id and required_name != spec.alias:
        if required_name.endswith(_ENTRY_SUFFIXES) or required_name == spec.swap_entry:
            return required_name
    return spec.swap_entry


def _entry_candidates(spec: ModelSpec) -> list[str]:
    if spec.placement == "dual":
        return [spec.swap_entry] if spec.swap_entry else [f"{spec.model_id}-dual"]
    return [f"{spec.model_id}-vk1", f"{spec.model_id}-vk2"]


def _evidence(spec: ModelSpec, fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    receipts = spec.receipt or {}
    for name in fields:
        value = getattr(spec, name, None)
        if name == "kv_hydrate_s":
            citation = receipts.get("kv_restore_s")
        else:
            citation = receipts.get(name)
        out[name] = {"value": value, "receipt": citation}
    return out


def build_rotation_plan(proposal: ScheduleProposal, models: dict[str, ModelSpec],
                        machine: Optional[Machine], jobs: list[Job],
                        gates: Optional[dict] = None, *,
                        blocked: Optional[list[dict]] = None,
                        assumptions: Optional[list[str]] = None) -> dict:
    """The rotation steps a proposal implies on `machine` (None -> empty plan).

    Steps come from `proposal.loads` on the machine (one `load` per model, cards by
    BDF, `est_s` = the setup the solver charged for the machine's cold state,
    `est_s_first_in_window` = the receipt figure for a cold host) and from the
    assignments that carried a KV hydrate charge (one `kv_restore` per job at its
    start). `serves` names the plan_ids each step unblocks. `blocked` is passed
    through from the pre-solve fit check; `assumptions` collects every caveat the
    reader needs (headroom, cold state, sibling entries, no eviction, gates).
    """
    plan: dict[str, Any] = {
        "machine": machine.name if machine is not None else None,
        "steps": [],
        "blocked": list(blocked or []),
        "assumptions": list(assumptions or []),
    }
    if machine is None:
        plan["assumptions"].append(
            "no rotating stateful host in this proposal (omen catalog absent or "
            "without cards); nothing to plan")
        return plan

    headroom = _headroom_gb(gates)
    plan["machine_detail"] = {
        "name": machine.name, "host": machine.host, "roles": machine.roles,
        "cold": bool(machine.cold), "staging_slots": machine.staging_slots,
        "resident_models": list(machine.resident_models),
        "cards": [{"index": int(c.get("index", i)), "bdf": c.get("bdf"),
                   "vram_gb": c.get("vram_gb")} for i, c in enumerate(machine.cards)],
    }

    by_plan = {job.plan_id: job for job in jobs}
    assigned_here = [a for a in proposal.assignments if a["machine"] == machine.name]

    steps: list[dict] = []
    for load in proposal.loads:
        if load["machine"] != machine.name:
            continue
        model_id = load["model_id"]
        spec = models.get(model_id)
        serves = [a["plan_id"] for a in assigned_here
                  if by_plan.get(a["plan_id"]) is not None
                  and by_plan[a["plan_id"]].required_model == model_id]
        cards = load.get("bdfs") or [_card_label(machine, ci) for ci in load["cards"]]
        if spec is None:
            steps.append({
                "t_s": load["start_s"], "action": "load", "model_id": model_id,
                "swap_entry": None, "cards": cards, "placement": None,
                "est_s": load.get("setup_s", load["end_s"] - load["start_s"]),
                "est_s_first_in_window": None, "evidence": {},
                "serves": serves,
                "note": "model not in any catalog; default setup charged",
            })
            continue
        steps.append({
            "t_s": load["start_s"],
            "action": "load",
            "model_id": spec.model_id,
            "swap_entry": _swap_entry_for(model_id, spec),
            "swap_entry_candidates": _entry_candidates(spec),
            "cards": cards,
            "placement": spec.placement,
            "est_s": load.get("setup_s", spec.setup_s(cold=bool(machine.cold))),
            "est_s_first_in_window": spec.load_s_first_in_window,
            "per_card_gb": round(spec.card_charge_gb(), 2),
            "evidence": _evidence(spec, ("load_s_steady", "load_s_first_in_window",
                                         "per_card_gb", "vram_gb")),
            "serves": serves,
        })

    residency_cards: dict[str, list[str]] = {}
    for row in proposal.residency:
        if row["machine"] != machine.name:
            continue
        label = row.get("bdf") or _card_label(machine, row["card"])
        for model_id in row["resident_models"]:
            residency_cards.setdefault(model_id, []).append(label)

    for assignment in assigned_here:
        hydrate = assignment.get("kv_hydrate_s")
        job = by_plan.get(assignment["plan_id"])
        if not hydrate or job is None or not job.required_model:
            continue
        spec = models.get(job.required_model)
        if spec is None:
            continue
        steps.append({
            "t_s": assignment["start_s"],
            "action": "kv_restore",
            "model_id": spec.model_id,
            "swap_entry": _swap_entry_for(job.required_model, spec),
            "cards": residency_cards.get(job.required_model)
            or residency_cards.get(spec.model_id) or [],
            "placement": spec.placement,
            "est_s": float(hydrate),
            "est_s_first_in_window": None,
            "evidence": _evidence(spec, ("kv_hydrate_s", "kv_save_s")),
            "serves": [job.plan_id],
        })

    action_rank = {"load": 0, "kv_restore": 1}
    steps.sort(key=lambda s: (s["t_s"], action_rank.get(s["action"], 9), s["model_id"]))
    plan["steps"] = steps

    cold_field = "load_s_first_in_window" if machine.cold else "load_s_steady"
    drains = sorted({
        f"{spec.model_id}={spec.unload_drain_s_max:g}s"
        for spec in {id(s): s for s in models.values()}.values()
        if spec.unload_drain_s_max})
    plan["assumptions"].extend([
        "advisory only: the scheduler proposes, the conductor dispatches (ADR-0008); "
        "no step here loads, unloads, or restores anything",
        f"per-card headroom {headroom:g} GB below nominal; residents at t=0: "
        f"{list(machine.resident_models)}",
        f"host cold={bool(machine.cold)}: est_s uses {cold_field} where the catalog "
        "measured it, warmup_ms fallback otherwise; est_s_first_in_window is the "
        "receipt figure for a cold host",
        "single-card models are declared twice in llama-swap (-vk1/-vk2); the "
        "sibling that actually lands is chosen by placement assertion at load time, "
        "never by index (ADR-0042); `cards` is the solver's VRAM-fit choice by BDF",
        "v1 has no eviction: a loaded model stays resident to the horizon; a swap "
        "drains in-flight streams" + (f" (measured: {', '.join(drains)})" if drains else ""),
    ])
    if isinstance(gates, dict) and gates:
        numbers = ", ".join(f"{k}={v}" for k, v in sorted(gates.items()) if k != "receipt")
        plan["assumptions"].append(
            f"admission gates are checked live by the rotation lifecycle, not here: "
            f"{numbers}")
    plan["assumptions"].extend(f"solver: {note}" for note in proposal.notes)
    return plan


__all__ = [
    "DEFAULT_HEADROOM_GB",
    "build_rotation_plan",
    "check_fit",
    "cumulative_overflow",
]
