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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
import urllib.request
import urllib.error

from hearth.toolsurface._scope import resolve_in_scope, scope_root
from hearth.toolsurface.task_lane import CONDUCTOR_REPO, _run_ssh
from hearth.scheduler.decision import build_scheduler_decision, validate_decision
from hearth.scheduler.families import Families, load_families, resolve_required_model
from hearth.scheduler.hindsight import render_table, replay
from hearth.scheduler.ontology import (
    OMEN_CATALOG_CONTRACT,
    GPU_CATALOG_CONTRACT,
    Job,
    Machine,
    load_capacity,
    load_machines,
    load_model_catalog,
    load_gpu_inventory,
    load_runner_classes,
)
from hearth.scheduler.rotation_plan import build_rotation_plan, check_fit, cumulative_overflow
from hearth.scheduler.solve import solve_schedule

DEFAULT_CAPACITY_PATH = "knowledge/capacity.json"
DEFAULT_AM4_CATALOG_PATH = "knowledge/am4_catalog.json"
DEFAULT_AM4_GPU_CATALOG_PATH = "knowledge/am4_gpu_catalog.json"
DEFAULT_FX99_GPU_CATALOG_PATH = "knowledge/fx99_gpu_catalog.json"
# P2b / ADR-0045: the OMEN catalog from receipts (P1). When it loads, the pool
# gains the rotating stateful inference host; absent -> proposals are unchanged.
DEFAULT_OMEN_CATALOG_PATH = "knowledge/omen_catalog.json"
_INVENTORY_REL = "fleet/inventory.toml"
_BACKENDS_REL = "hearth/etc/backends.toml"
_FEASIBLE = ("OPTIMAL", "FEASIBLE")

# Idle-drain retest runs are PROOFING data, not real work (2026-07-20: five empty
# "agent produced nothing" retest laps entered the regret window as 19-20s wins).
# Their plan ids carry the drain lane's authored prefix — the one durable marker
# these historical records have, since result.json carries no task_class today —
# so the gather derives the tag from the run-dir name. An explicit task_class in
# result.json wins. Must equal fleet/bankedfire_drain.py PLAN_ID_PREFIX (test-pinned).
_PROOFING_PLAN_PREFIX = "hearth-drain-"

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
        if name.startswith("PROOFING_PREFIX_PLACEHOLDER"):
            rec["task_class"] = r.get("task_class") or "proofing"
        else:
            rec["task_class"] = r.get("task_class") or r.get("workflow_id") or "unknown"
        # Regret-gate hole #3 (REGRET-TREND-2026-07 / SCHEDULER-STRATEGY S5):
        # the gather never collected tokens_out, so every regret number was an
        # estimate. Collect it when a result carries it, under any of the three
        # shapes a runner might write; hindsight's estimator already prefers an
        # explicit record value over capacity-bucket or DEFAULT_EST_TOKENS.
        tok = r.get("tokens_out")
        if tok is None:
            tok = (r.get("cost") or {}).get("tokens_out")
        if tok is None:
            tok = (r.get("usage") or {}).get("completion_tokens")
        if isinstance(tok, (int, float)) and tok >= 0:
            rec["tokens_out"] = int(tok)
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
    src = (_GATHER_SRC_TEMPLATE
           .replace("LIMIT_PLACEHOLDER", str(int(limit)))
           .replace("PROOFING_PREFIX_PLACEHOLDER", _PROOFING_PLAN_PREFIX))
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


def _job_from_dict(raw: dict, families: Optional[Families] = None) -> Job:
    """One snapshot dict -> Job.

    P2b: reads `task_family`, `prompt_tokens` and `kv_state_available`. A job that
    names no `required_model` but carries a `task_family` has its model resolved
    through hearth.scheduler.families (routing-families.v1: the family's model at
    the job's prompt depth, ADR-0039 depth override included). An explicit
    `required_model` always wins; a job with neither stays unconstrained, so
    family-less snapshots produce exactly the proposals they did before.
    """
    if not isinstance(raw, dict) or not raw.get("plan_id"):
        raise ValueError("each job must be a dict with a non-empty plan_id")
    prompt_tokens = raw.get("prompt_tokens")
    task_family = raw.get("task_family")
    eligible_models = raw.get("eligible_models") or []
    if "requires_tools" in raw and not isinstance(raw["requires_tools"], bool):
        raise ValueError("requires_tools must be a boolean")
    if not isinstance(eligible_models, list) or any(
            not isinstance(option, dict) or not isinstance(option.get("backend"), str)
            or not isinstance(option.get("model_id"), str)
            or not option["backend"] or not option["model_id"]
            for option in eligible_models):
        raise ValueError("eligible_models must be a list of {backend, model_id} choices")
    job = Job(
        plan_id=str(raw["plan_id"]),
        task_class=str(raw.get("task_class") or "default"),
        precedence=[str(p) for p in (raw.get("precedence") or [])],
        deadline_s=raw.get("deadline_s"),
        est_tokens=raw.get("est_tokens"),
        required_model=(str(raw["required_model"]) if raw.get("required_model") else None),
        eligible_models=[dict(option) for option in eligible_models],
        requires_tools=bool(raw.get("requires_tools", False)),
        est_out_tokens=raw.get("est_out_tokens"),
        est_duration_s=raw.get("est_duration_s"),
        task_family=(str(task_family) if task_family else None),
        prompt_tokens=(int(prompt_tokens) if isinstance(prompt_tokens, (int, float))
                       and not isinstance(prompt_tokens, bool) else None),
        kv_state_available=bool(raw.get("kv_state_available", False)),
    )
    if job.required_model is None and not job.eligible_models and job.task_family:
        # resolve_required_model loads the packaged families file itself when
        # `families` is None; the provider preloads once per call.
        job.required_model = resolve_required_model(job, families)
    return job


# The AM4 box — the one physically stateful machine (2x Arc B70, 32GB DDR4). Its
# logical builder name in the inventory. When the am4-catalog is present, THIS machine
# gains the catalog's cards as its VRAM budget and becomes `stateful`.
_AM4_MACHINE_NAME = "am4-worker-1"

# P2b / ADR-0045: OMEN as the scheduler sees it — a rotating stateful INFERENCE
# host (two B70s under llama-swap), never a builder. It is appended by
# propose_schedule from the omen catalog, NOT by load_machines: schedule_hindsight
# replays historical BUILDER runs through load_machines, and those runs must never
# be re-planned onto the B70s.
_OMEN_MACHINE_NAME = "omen-inference"
_OMEN_HOST = "omen"
_GPU_ROLES = ("inference", "research", "review", "planning", "analysis",
              "drafting", "summarization", "extraction", "classification")
_OMEN_ROLES = _GPU_ROLES


def _apply_stateful_catalog(machines: list, catalog: dict, machine_name: str,
                            append_if_missing: bool = False, *,
                            resident: Optional[list[str]] = None,
                            cold: bool = False,
                            roles: Optional[list[str]] = None,
                            host: Optional[str] = None) -> Optional[Machine]:
    """Bind a stateful-host catalog to the machine named `machine_name`.

    In place: the machine becomes `stateful` with the catalog's cards (index +
    vram_gb + bdf when the catalog keys cards by BDF), `resident_models` (the
    catalog's, unless `resident` overrides them — e.g. llama-swap's live
    `/running`), `staging_slots`, `roles`, `cold`, and `loadable_models` (the
    catalog's keys, so a pool with two stateful hosts never plans one host's
    model onto the other's cards). With `append_if_missing` a machine of that
    name is CREATED (local, token-free) when the pool lacks it. A catalog
    without cards binds nothing and returns None.
    """
    cards = catalog.get("cards")
    if not cards:
        return None
    machine = next((m for m in machines if m.name == machine_name), None)
    if machine is None:
        if not append_if_missing:
            return None
        machine = Machine(name=machine_name, kind="local", token_cost_weight=0.0,
                          tags=["local", "inference"], available=True)
        machines.append(machine)
    machine.stateful = True
    machine.cards = []
    for i, c in enumerate(cards):
        card = {"index": int(c.get("index", i)), "vram_gb": float(c.get("vram_gb", 0.0))}
        if c.get("bdf"):
            card["bdf"] = str(c["bdf"])
        if c.get("uuid"):
            card["uuid"] = str(c["uuid"])
        machine.cards.append(card)
    machine.host = host or machine.host or machine_name
    models = catalog.get("models") or {}
    if models:
        machine.loadable_models = list(models)
    if roles is not None:
        machine.roles = list(roles)
    machine.cold = bool(cold)
    staging = catalog.get("staging_slots")
    if isinstance(staging, int) and staging > 0:
        machine.staging_slots = staging
    residents = resident if resident is not None else (catalog.get("resident_models") or [])
    # One name per PHYSICAL model: llama-swap's /running lists entries
    # (`phi4-vk1`); the catalog keys them to a model_id. Unknown names stay as
    # given (they charge nothing, but the plan shows them).
    seen: list[str] = []
    for name in residents:
        spec = models.get(str(name))
        canon = spec.model_id if spec is not None else str(name)
        if canon not in seen:
            seen.append(canon)
    machine.resident_models = seen
    return machine


def _apply_am4_catalog(machines: list, catalog: dict) -> None:
    """In place: make the AM4 machine stateful with the catalog's cards, if present.
    (Kept as the JS7b entry point; a thin wrapper over _apply_stateful_catalog.)"""
    _apply_stateful_catalog(machines, catalog, _AM4_MACHINE_NAME, append_if_missing=False)


def _gpu_catalog_matches(host: str, catalog: dict, inventory: dict[str, list[dict]]) -> bool:
    """Require the measured catalog to describe the card physically on this host."""
    declared = inventory.get(host) or []
    cards = catalog.get("cards") or []
    if catalog.get("host") != host or not declared or len(declared) != len(cards):
        return False
    by_bdf = {str(card.get("bdf")): card for card in declared}
    for card in cards:
        physical = by_bdf.get(str(card.get("bdf")))
        if physical is None or physical.get("uuid") != card.get("uuid"):
            return False
        if abs(float(physical.get("vram_gb", 0)) - float(card.get("vram_gb", 0))) > 0.1:
            return False
    return True


def _fresh_gpu_state(state: dict, max_age_s: float = 30.0) -> bool:
    try:
        observed = datetime.fromisoformat(str(state["observed_at"]).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        return -5.0 <= age <= max_age_s
    except (KeyError, ValueError, TypeError):
        return False


def capture_resource_snapshot(timeout_s: float = 3.0) -> dict:
    """Read current serve truth for the three GPU hosts; never load or dispatch."""
    from hearth.toolsurface.backends import load_pool

    pool = load_pool()
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result: dict[str, dict] = {}
    omen = pool.by_name("omen-arc")
    if omen is not None:
        try:
            with urllib.request.urlopen(omen.endpoint + "/health", timeout=timeout_s) as response:
                ready = response.status == 200
            result["omen-arc"] = {"observed_at": observed_at, "ready": ready,
                                   "parallel_slots": max(1, int(omen.settings.get("parallel_slots", 1))),
                                   "reason": "ready" if ready else "health did not answer 200"}
        except (OSError, urllib.error.URLError, ValueError, TypeError) as exc:
            result["omen-arc"] = {"observed_at": observed_at, "ready": False,
                                   "reason": f"health probe failed: {type(exc).__name__}"}
    swap = pool.by_name("omen-swap")
    if swap is not None:
        try:
            with urllib.request.urlopen(swap.endpoint + "/health", timeout=timeout_s) as response:
                swap_ready = response.status == 200
            with urllib.request.urlopen(swap.endpoint + "/running", timeout=timeout_s) as response:
                running = json.load(response).get("running", [])
            resident = [str(row["model"]) for row in running
                        if isinstance(row, dict) and row.get("model")
                        and str(row.get("state", "")).lower() in {"ready", "running", "loaded"}]
            result["omen-swap"] = {"observed_at": observed_at,
                                     "ready": swap_ready and bool(resident),
                                     "loaded_models": resident,
                                     "reason": "resident upstream ready" if resident else "no ready upstream"}
        except (OSError, urllib.error.URLError, ValueError, TypeError) as exc:
            result["omen-swap"] = {"observed_at": observed_at, "ready": False,
                                     "reason": f"swap probe failed: {type(exc).__name__}"}
    for name, catalog_path in (("fx99-ollama", DEFAULT_FX99_GPU_CATALOG_PATH),
                               ("am4-ollama", DEFAULT_AM4_GPU_CATALOG_PATH)):
        backend = pool.by_name(name)
        if backend is None:
            result[name] = {"observed_at": observed_at, "ready": False,
                            "reason": "backend not declared"}
            continue
        try:
            catalog = load_model_catalog(str(resolve_in_scope(catalog_path)),
                                         contracts=(GPU_CATALOG_CONTRACT,))
            scheduler_models = {spec.model_id for spec in catalog["models"].values()}
            with urllib.request.urlopen(backend.endpoint + "/api/tags", timeout=timeout_s) as response:
                tags = json.load(response)
            with urllib.request.urlopen(backend.endpoint + "/api/ps", timeout=timeout_s) as response:
                ps = json.load(response)
            installed = {row.get("name") for row in tags.get("models", [])}
            loaded = {row.get("name"): row for row in ps.get("models", [])}
            placement = {}
            qualified = []
            for model in sorted(scheduler_models):
                row = loaded.get(model) or {}
                size = float(row.get("size") or 0)
                size_vram = float(row.get("size_vram") or 0)
                placed = size > 0 and size_vram >= 0.95 * size
                placement[model] = {"size": int(size), "size_vram": int(size_vram),
                                    "fully_gpu": placed}
                if model in backend.models and model in installed and placed:
                    qualified.append(model)
            unqualified_loaded = sorted(set(loaded) - set(qualified))
            # An unknown or CPU-spilled resident consumes an unmodeled share of
            # VRAM. Exclude that host until its capacity is measured or freed.
            ready = bool(qualified) and not unqualified_loaded
            result[name] = {
                "observed_at": observed_at, "ready": ready,
                "gpu_placed": bool(qualified), "loaded_models": list(loaded),
                "gpu_qualified_models": qualified,
                "unqualified_loaded_models": unqualified_loaded,
                "model_placement": placement,
                "parallel_slots": max(1, int(backend.settings.get("parallel_slots", 1))),
                "reason": "ready" if ready else "model absent, spilled, or unmodeled resident",
            }
        except (OSError, urllib.error.URLError, ValueError, TypeError) as exc:
            result[name] = {"observed_at": observed_at, "ready": False,
                            "reason": f"serve-truth probe failed: {type(exc).__name__}"}
    return result


def _resolve_families_once(jobs: list[dict]) -> Optional[Families]:
    """Load routing-families.v1 once per call, only when some job needs it (a job
    with a task_family and no explicit required_model). Snapshots without
    families never touch the file, so they cannot fail on it either."""
    for raw in jobs:
        if isinstance(raw, dict) and raw.get("task_family") and not raw.get("required_model"):
            return load_families()
    return None


def propose_schedule(jobs: list[dict], capacity_path: str = DEFAULT_CAPACITY_PATH,
                     am4_catalog_path: str = DEFAULT_AM4_CATALOG_PATH,
                     omen_catalog_path: str = DEFAULT_OMEN_CATALOG_PATH,
                     omen_resident: Optional[list[str]] = None,
                     omen_cold: bool = False, *,
                     resource_snapshot: Optional[dict] = None,
                     am4_gpu_catalog_path: str = DEFAULT_AM4_GPU_CATALOG_PATH,
                     fx99_gpu_catalog_path: str = DEFAULT_FX99_GPU_CATALOG_PATH) -> dict:
    """Propose an advisory job-shop schedule for a snapshot of jobs (read-only).

    Solves jobs x eligible-machines with CP-SAT: exactly-one machine per job,
    no-overlap per machine, precedence and hard deadlines respected. Objective
    minimizes metered (frontier) token spend first and makespan second. Returns
    {ok, proposal, decision_record, machines_considered, rotation_plan}; the
    decision_record conforms to and is validated against scheduler-decision.v1.
    Nothing is dispatched.

    The historical `am4_catalog_path` is retained for callers but is not read:
    it describes B70s now physically on OMEN. AM4 and FX99 inference machines
    come from GPU catalogs that match inventory UUID/BDFs and a fresh serving
    snapshot. Loaded models are qualified individually for GPU placement; a
    bare inference machine can take bounded model work, while tool-using work
    requires a builder shell. Backend slots and physical decode hosts constrain
    throughput shared by VMs and direct calls.

    P2b / ADR-0045: when `omen_catalog_path` (omen-catalog.v1, from receipts)
    loads, the pool gains `omen-inference` — OMEN's two B70s under llama-swap as a
    rotating stateful host (model work roles exclude tool-using builds).
    `omen_resident` overrides the catalog's t=0 residents with the live
    list (llama-swap `/running` entries); `omen_cold` charges each load the
    first-in-window figure instead of the steady one. Jobs may carry a
    `task_family` (+ `prompt_tokens`) instead of a `required_model` — the family
    resolves to a model through routing-families.v1 — and `kv_state_available`
    when a saved KV slot exists for the prompt. A job whose model can NEVER fit
    beside the residents is moved to `rotation_plan.blocked` (with the numbers)
    before the solve instead of collapsing the whole proposal to INFEASIBLE;
    when the solver still finds no packing, a best-fit pass names the overflow
    and the rest is re-solved once. `rotation_plan` (steps by BDF, blocked,
    assumptions) is a SIBLING of `proposal` — never inside `decision_record`.
    Absent omen catalog -> the pool and every proposal are exactly as before.
    """
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list of job dicts")
    families = _resolve_families_once(jobs)
    job_objs = [_job_from_dict(raw, families) for raw in jobs]
    family_resolved = {
        job.plan_id for job, raw in zip(job_objs, jobs)
        if job.task_family and not raw.get("required_model") and job.required_model}

    root = scope_root()
    inventory_path = str(root / _INVENTORY_REL)
    backends_path = str(root / _BACKENDS_REL)
    machines = load_machines(inventory_path, backends_path)
    gpu_inventory = load_gpu_inventory(inventory_path)

    capacity = load_capacity(str(resolve_in_scope(capacity_path)))
    # The 2026-07 AM4 catalog describes B70s that physically moved to OMEN.
    # Preserve the path for historical callers, but never attach its cards to
    # the present-day AM4 builder or inference pool.
    # Historical path is accepted for API compatibility but its B70 models
    # cannot exempt any present-day OMEN fit check.
    am4_gpu_catalog = load_model_catalog(
        str(resolve_in_scope(am4_gpu_catalog_path)), contracts=(GPU_CATALOG_CONTRACT,))
    fx99_gpu_catalog = load_model_catalog(
        str(resolve_in_scope(fx99_gpu_catalog_path)), contracts=(GPU_CATALOG_CONTRACT,))
    snapshot = resource_snapshot if isinstance(resource_snapshot, dict) else {}
    gpu_machines = []
    resource_rejections: list[dict] = []
    accepted_gpu_catalogs: dict[str, dict] = {}
    for host, backend, gpu_catalog in (
            ("am4", "am4-ollama", am4_gpu_catalog),
            ("fx99", "fx99-ollama", fx99_gpu_catalog)):
        if not _gpu_catalog_matches(host, gpu_catalog, gpu_inventory):
            resource_rejections.append({"host": host, "backend": backend,
                                        "reason": "catalog does not match the physical GPU inventory"})
            continue
        accepted_gpu_catalogs[host] = gpu_catalog
        machine = _apply_stateful_catalog(
            machines, gpu_catalog, f"{host}-inference", append_if_missing=True,
            roles=list(_GPU_ROLES), host=host)
        if machine is not None:
            machine.backend = backend
            machine.decode_host = host
            state = snapshot.get(backend) or {}
            machine.available = bool(state.get("ready") is True
                                     and state.get("gpu_placed") is True
                                     and _fresh_gpu_state(state))
            if isinstance(state.get("loaded_models"), list):
                qualified = state.get("gpu_qualified_models")
                if not isinstance(qualified, list):
                    # Historical caller snapshots only carried a host-wide
                    # placement bit. Live captures always name each model.
                    qualified = (state["loaded_models"] if state.get("gpu_placed") else [])
                qualified_names = [str(model) for model in qualified]
                machine.resident_models = qualified_names
                machine.loadable_models = [name for name in (machine.loadable_models or [])
                                           if gpu_catalog["models"][name].model_id in qualified_names]
            slots = state.get("parallel_slots")
            machine.parallel_slots = max(1, int(slots)) if isinstance(slots, int) and not isinstance(slots, bool) else 1
            gpu_machines.append(machine)

    # A live VM shell is only usable local compute if its declared runner model
    # is qualified on the physical endpoint it calls. This specifically keeps
    # FX99's 14B builder out of the 8 GiB GPU pool until it is repointed to AM4.
    if resource_snapshot is not None:
        catalog_by_backend = {"am4-ollama": am4_gpu_catalog, "fx99-ollama": fx99_gpu_catalog}
        for machine in machines:
            if machine.name.endswith("-inference") or machine.backend not in catalog_by_backend:
                continue
            state = snapshot.get(machine.backend) or {}
            qualified = (machine.runner_model in
                         (catalog_by_backend[machine.backend].get("models") or {}))
            placed_models = state.get("gpu_qualified_models")
            if not isinstance(placed_models, list):
                placed_models = state.get("loaded_models") or []
            machine.available = bool(machine.available and qualified
                                     and state.get("ready") is True
                                     and state.get("gpu_placed") is True
                                     and _fresh_gpu_state(state)
                                     and machine.runner_model in placed_models)

    omen_catalog = load_model_catalog(str(resolve_in_scope(omen_catalog_path)),
                                      contracts=(OMEN_CATALOG_CONTRACT,))
    omen_models = omen_catalog.get("models") or {}
    omen_machine = _apply_stateful_catalog(
        machines, omen_catalog, _OMEN_MACHINE_NAME, append_if_missing=True,
        resident=(list(omen_resident) if omen_resident is not None else None),
        cold=omen_cold, roles=list(_OMEN_ROLES), host=_OMEN_HOST)
    if omen_machine is not None:
        omen_machine.backend = "omen-arc"
        omen_machine.decode_host = "omen"
        omen_state = snapshot.get("omen-arc")
        if isinstance(omen_state, dict):
            omen_machine.available = bool(omen_state.get("ready") is True
                                          and _fresh_gpu_state(omen_state))
            requests = {job.required_model for job in job_objs if job.required_model in omen_models}
            requests.update(option["model_id"] for job in job_objs
                            for option in job.eligible_models if option["backend"] == "omen-arc")
            if requests <= {"qwen3-30b-a3b"}:
                slots = omen_state.get("parallel_slots")
                if isinstance(slots, int) and not isinstance(slots, bool):
                    omen_machine.parallel_slots = max(1, slots)
            if not omen_machine.available:
                swap_state = snapshot.get("omen-swap") or {}
                resident = swap_state.get("loaded_models")
                if (swap_state.get("ready") is True and _fresh_gpu_state(swap_state)
                        and isinstance(resident, list)):
                    # The night epoch is a different serving shape. Only plan
                    # already-ready upstreams: advisory work must not trigger
                    # a swap or silently treat the daytime Qwen3 as resident.
                    ready_models = [str(name) for name in resident if name in omen_models]
                    omen_machine.backend = "omen-swap"
                    omen_machine.resident_models = ready_models
                    omen_machine.loadable_models = ready_models
                    omen_machine.available = bool(ready_models)
                    omen_machine.parallel_slots = 1
    gpu_models = {**(accepted_gpu_catalogs.get("am4", {}).get("models") or {}),
                  **(accepted_gpu_catalogs.get("fx99", {}).get("models") or {})}
    models = {**omen_models, **gpu_models}
    gates = omen_catalog.get("gates")

    assumptions: list[str] = []
    stateful_present = any(m.stateful for m in machines)
    if stateful_present:
        # A family may name a model no stateful host in this pool serves (the
        # vision families -> gemini-3.5-flash, a cloud rung the job shop does not
        # model). That is not a capacity problem: schedule the job without a
        # residency constraint and say so, instead of an INFEASIBLE collapse.
        for job in job_objs:
            if job.plan_id in family_resolved and job.required_model not in models:
                assumptions.append(
                    f"{job.plan_id}: task_family {job.task_family!r} resolves to "
                    f"{job.required_model!r}, which no stateful host in this pool "
                    "serves (a door rung, not a job-shop machine); scheduled "
                    "without a residency constraint")
                job.required_model = None

    blocked: list[dict] = []
    kept = list(job_objs)
    if omen_machine is not None:
        kept, blocked = check_fit(kept, models, omen_machine, gates,
                                  exempt=set(gpu_models))

    proposal = solve_schedule(kept, machines, capacity, models=models)
    if proposal.solver_status not in _FEASIBLE and omen_machine is not None:
        overflow = cumulative_overflow(kept, models, omen_machine, gates,
                                       exempt=set(gpu_models))
        if overflow:
            blocked.extend(overflow)
            dropped = {row["plan_id"] for row in overflow}
            kept = [job for job in kept if job.plan_id not in dropped]
            assumptions.append(
                f"solver found no packing for the full request; {sorted(dropped)} "
                "moved to blocked by best-fit overflow and the rest re-solved once")
            proposal = solve_schedule(kept, machines, capacity, models=models)

    decision = build_scheduler_decision(kept, machines, proposal)
    validate_decision(decision)
    rotation_plan = build_rotation_plan(
        proposal, models, omen_machine, kept, gates,
        blocked=blocked, assumptions=assumptions)

    return {
        "ok": proposal.solver_status in _FEASIBLE,
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
             "available": m.available, "tags": m.tags, "host": m.host,
             "backend": m.backend, "runner_model": m.runner_model,
             "decode_host": m.decode_host,
             "cards": m.cards, "parallel_slots": m.parallel_slots}
            for m in machines
        ],
        "resource_rejections": resource_rejections,
        "rotation_plan": rotation_plan,
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
    return [propose_schedule, schedule_hindsight, capture_resource_snapshot]
