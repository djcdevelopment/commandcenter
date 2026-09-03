"""Scheduler ontology: dataclasses + pure loaders. No SSH, no network, no dispatch.

Jobs arrive as a plain list-of-dicts SNAPSHOT from the caller — this package never
reaches out to the fleet to discover them. Machines are loaded from the OMEN-side
inventory (fleet/inventory.toml) + backend pool (hearth/etc/backends.toml); both are
tolerated missing, in which case a small sane default machine list is used. Durations
are looked up from a projected capacity.json (capacity.v1) with a fallback chain down
to a declared per-task-class default table.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --- declared fallbacks -----------------------------------------------------

# Per-task-class default job durations (seconds), used only when capacity.json has
# no matching bucket. Deliberately coarse: a shadow proposal, not a promise.
DEFAULT_DURATIONS_S: dict[str, float] = {
    "inference": 120.0,
    "build": 1800.0,
    "test": 300.0,
    "default": 600.0,
}

# Builders eligible for async fleet tasks today. A hypothetical frontier builder
# carries a high token-cost weight so the token objective only reaches for it when
# deadlines force parallelism it cannot avoid. Used only when inventory/backends
# files are absent.
_DEFAULT_MACHINES: tuple[dict, ...] = (
    {"name": "am4-worker-1", "kind": "local", "token_cost_weight": 0.0,
     "tags": ["local", "code", "big-context"], "available": True},
    {"name": "cc-builder-1", "kind": "frontier", "token_cost_weight": 1.0,
     "tags": ["code", "frontier"], "available": True},
    {"name": "cc-builder-2", "kind": "local", "token_cost_weight": 0.0,
     "tags": ["local", "code"], "available": True},
    {"name": "frontier-builder", "kind": "frontier", "token_cost_weight": 1.0,
     "tags": ["frontier"], "available": True},
)

# Builder names the scheduler treats as async-eligible machines (pool membership;
# their local-vs-frontier kind comes from the runner-class registry below).
_POOL_BUILDER_NAMES = {"am4-worker-1", "cc-builder-1", "cc-builder-2"}

# Machine.available answers "may the solver PLAN work here?". That is NOT what the
# inventory's `expect` field means. `expect` is fleet_ping's ALARM flag: "up" = this
# node's absence turns the health sweep red, "optional" = it never does. An
# expect="optional" node may be live and serving right now (fx99 is one) — "not
# required to answer" is not "known to be down".
#
# Reading `expect` as availability was a category error that stayed invisible until
# the 2026-08-24 fleet hold marked every parked VM expect="optional" for sweep
# hygiene. That one config edit emptied the schedulable local pool, leaving only the
# synthetic always-on `frontier-builder`, and silently flipped the scheduler from
# local-first to metered-only — the exact outcome the two-economies objective exists
# to prevent. Deliberate exclusion now has its own key, `schedulable = false`, so a
# monitoring decision can never again rewrite the token economy as a side effect.
_SCHEDULABLE_KEY = "schedulable"

# The two synthetic options load_machines always offers the solver. Neither is a
# fleet node: they exist so BOTH economies are always on the table, and the
# two-economies objective can only ever choose between them on cost — never by
# omission, because one side's candidates quietly vanished from a config file.
_SYNTHETIC_FRONTIER = "frontier-builder"
_SYNTHETIC_LOCAL = "local-builder"

# Declared builder locality, used when fleet/inventory.toml (or its runner_class
# fields) is absent. Corrected 2026-07-11 against each node's live runner.json:
# cc-builder-1 carries NO runner.json, so its worker defaults to the metered
# claude runner (frontier); every other builder is an openai runner pointed at a
# local backend (oxen / OMEN Ollama). The old set treated cc-builder-1 as local
# and omitted omen-worker-1 entirely — wrong in both directions (see
# docs/REGRET-TREND-2026-07.md, Finding 2).
FALLBACK_RUNNER_CLASSES: dict[str, str] = {
    "am4-worker-1": "local",
    "omen-worker-1": "local",
    "cc-builder-2": "local",
    "cc-builder-3": "local",
    "cc-builder-4": "local",
    "claudefarm1": "local",
    "cc-builder-1": "frontier",
    "frontier-builder": "frontier",
}


def load_runner_classes(inventory_path: str) -> dict[str, str]:
    """Map builder name -> "local" | "frontier" from the fleet inventory.

    Reads each node's structured ``runner_class`` field (the OMEN-reachable
    projection of that node's runner.json). Starts from the declared
    FALLBACK_RUNNER_CLASSES so a winner the inventory no longer names still
    classifies; inventory declarations win over the fallback. A missing or
    unreadable inventory yields the fallback map alone.
    """
    classes = dict(FALLBACK_RUNNER_CLASSES)
    inventory = _read_toml(Path(inventory_path))
    if inventory is None:
        return classes
    for node in inventory.get("node", []):
        name = node.get("name")
        runner_class = node.get("runner_class")
        if name and runner_class in ("local", "frontier"):
            classes[name] = runner_class
    return classes


@dataclass
class ModelSpec:
    """A loadable model on a stateful machine (the AM4 box under am4-catalog.v1,
    OMEN under omen-catalog.v1). Carries the residency economics JS7b optimizes
    over: how big it is per card (`per_card_gb`), how long it takes to stage into
    VRAM (`load_s_*` from receipts, `warmup_ms_*` as the older fallback), how fast
    it generates once warm (`expected_gen_tps`), and — P2 / ADR-0045 — the rotation
    figures a llama-swap host adds: the first load inside a window is slower than a
    steady one (`load_s_first_in_window` vs `load_s_steady`), a saved KV slot can be
    hydrated instead of re-prefilled (`kv_hydrate_s`), a swap drains in-flight
    streams (`unload_drain_s_max`), and two models sharing an `exclusive_group`
    are never co-resident. Every rotation field is Optional: unmeasured stays
    None (Constitution / R8), and the solver falls back to the older figures."""

    model_id: str
    alias: Optional[str] = None
    placement: str = "single"  # "single" | "dual"
    visible_devices: Optional[str] = None
    vram_gb: Optional[float] = None       # total VRAM footprint
    per_card_gb: Optional[float] = None   # per-card footprint (dual charges both)
    expected_gen_tps: Optional[float] = None
    warmup_ms_p50: Optional[float] = None
    warmup_ms_max: Optional[float] = None
    sample_count: Optional[int] = None
    notes: Optional[str] = None
    # --- rotation (omen-catalog.v1; all from receipts, nullable) -------------
    load_s_steady: Optional[float] = None           # dio cold load, steady state
    load_s_first_in_window: Optional[float] = None  # first load inside a window
    kv_hydrate_s: Optional[float] = None            # slot restore (kv_restore_s)
    kv_save_s: Optional[float] = None
    unload_drain_s_max: Optional[float] = None      # swaps DRAIN in-flight streams
    exclusive_group: Optional[str] = None           # never co-resident with siblings
    swap_entry: Optional[str] = None                # llama-swap entry name, if declared
    receipt: Optional[dict] = None                  # {field: "path#selector"}

    def setup_s(self, default_s: float = 30.0, cold: bool = False) -> float:
        """Load (setup) time in seconds.

        Preference order: the receipt-backed rotation figure for the host's
        thermal state — `load_s_first_in_window` when `cold` (the first load
        inside a window: 26.58 s for the 30B vs 8.19 steady, r2 receipts), else
        `load_s_steady` — then the older warmup p50 / max pair, then `default_s`.
        A cold host with only a steady figure still uses the steady figure (it is
        the best evidence available), and vice versa.
        """
        preferred = ((self.load_s_first_in_window, self.load_s_steady) if cold
                     else (self.load_s_steady, self.load_s_first_in_window))
        for candidate in preferred:
            if candidate is not None and candidate > 0:
                return float(candidate)
        for candidate in (self.warmup_ms_p50, self.warmup_ms_max):
            if candidate is not None and candidate > 0:
                return float(candidate) / 1000.0
        return default_s

    def card_charge_gb(self) -> float:
        """Per-card VRAM charged when this model is resident. `dual` placement
        charges `per_card_gb` on BOTH cards; `single` on exactly one card."""
        if self.per_card_gb is not None:
            return float(self.per_card_gb)
        if self.vram_gb is not None:
            # No per-card figure: split total across the placement's card count.
            return float(self.vram_gb) / (2.0 if self.placement == "dual" else 1.0)
        return 0.0


@dataclass
class Job:
    """A unit of work to place. Snapshot-shaped: precedence names other plan_ids.

    JS7b: `required_model` names a model that must be RESIDENT on the chosen
    stateful machine before the job may start (paying a load/setup interval if it
    is not already loaded). `est_out_tokens` lets duration derive from a model's
    `expected_gen_tps` when the catalog supplies one."""

    plan_id: str
    task_class: str
    precedence: list[str] = field(default_factory=list)
    deadline_s: Optional[float] = None
    est_tokens: Optional[int] = None
    required_model: Optional[str] = None
    est_out_tokens: Optional[int] = None
    # U1: caller-supplied direct duration — wins over every lookup path.
    est_duration_s: Optional[float] = None
    # P2 / ADR-0045: the task family (routing-families.v1) the caller tagged the
    # job with; the provider resolves it to `required_model` through
    # hearth.scheduler.families when no explicit model is named. Pure data here.
    task_family: Optional[str] = None
    # Prompt depth estimate (tokens); the families' depth rules read it. When
    # absent, `est_tokens` is the same quantity by intent.
    prompt_tokens: Optional[int] = None
    # A saved KV slot exists for this job's prompt on the required model: the
    # scheduler charges the model's `kv_hydrate_s` (1.19 s measured, ADR-0040 P3)
    # instead of a re-prefill and the rotation plan emits a kv_restore step.
    kv_state_available: bool = False


@dataclass
class Machine:
    """A schedulable resource. token_cost_weight scales metered-token cost: ~0 for
    local (owned, mains power), high for frontier (metered API tokens).

    JS7b: a `stateful` machine (the AM4 box) carries model-residency STATE — its
    physical `cards` (each with a vram_gb budget), the `resident_models` already
    loaded at t=0, and how many models may stream through DDR4 at once
    (`staging_slots`, the single-DDR4 bottleneck = 1)."""

    name: str
    kind: str  # "local" | "frontier"
    token_cost_weight: float
    tags: list[str] = field(default_factory=list)
    available: bool = True
    stateful: bool = False
    cards: list[dict] = field(default_factory=list)  # [{index, vram_gb, bdf?}]
    resident_models: list[str] = field(default_factory=list)  # loaded at t=0
    staging_slots: int = 1
    host: Optional[str] = None  # physical host key for DDR4 staging contention
    # P2 / ADR-0045: which task classes this machine may take. None = any (the
    # builders); ["inference"] for the OMEN inference host, so a build job is
    # never planned onto the B70s.
    roles: Optional[list[str]] = None
    # The host is cold: the next load pays `load_s_first_in_window` (26.58 s for
    # the 30B) instead of the steady 8.2 s. Advisory input; the caller decides.
    cold: bool = False
    # P2b: the model names THIS stateful machine can load — its own catalog's
    # keys (model_id, alias, swap entries). None = any model in the solver's
    # `models` map (the single-catalog JS7b behavior). With two stateful hosts in
    # one pool (AM4 under am4-catalog.v1, OMEN under omen-catalog.v1) the solver
    # must not plan an OMEN-only model onto AM4's cards, or vice versa.
    loadable_models: Optional[list[str]] = None


@dataclass
class ScheduleProposal:
    """The advisory result. Read-only; nothing here is dispatched.

    JS7b adds `loads` (the model-load/setup intervals the placement implies — one
    per (machine, model) actually loaded, sharing the DDR4 staging slot) and
    `residency` (the per-card resident-VRAM summary at horizon)."""

    assignments: list[dict]  # [{plan_id, machine, start_s, end_s}]
    makespan_s: float
    est_metered_tokens: int
    solver_status: str
    objective_value: float
    loads: list[dict] = field(default_factory=list)  # [{machine, model_id, cards, bdfs?, start_s, end_s, setup_s?, cold?}]
    residency: list[dict] = field(default_factory=list)  # [{machine, card, bdf?, resident_models, used_gb, budget_gb}]
    # P2: why a result is what it is (exclusive-group refusals, ineligible jobs).
    # Empty for every proposal that needed no explanation — the JS7a shape.
    notes: list[str] = field(default_factory=list)


# --- loaders ----------------------------------------------------------------


def _read_toml(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_machines(inventory_path: str, backends_path: str) -> list[Machine]:
    """Build the machine list from the fleet inventory + backend pool.

    Async-eligible builders come from the inventory's logical/builder nodes
    (am4-worker-1, cc-builder-1, cc-builder-2), each kinded local-vs-frontier by
    the runner-class registry (load_runner_classes) — a frontier-runner builder
    like cc-builder-1 is a real machine the solver may use, but at metered token
    weight. A single hypothetical frontier builder is always appended so
    deadline-forced parallelism has somewhere to go. Backend tags
    (hearth/etc/backends.toml) enrich local machine tags where a backend rides
    the same node. Missing files -> declared defaults.

    Availability comes from the node's optional `schedulable` flag (default true),
    NOT from `expect` — see the note above _POOL_BUILDER_NAMES.

    GUARANTEE: the returned pool always contains at least one AVAILABLE machine of
    each kind. Whatever the inventory says, the solver is offered both economies, so
    frontier can only ever win the objective on cost — never because a config edit
    emptied the local side.
    """
    inventory = _read_toml(Path(inventory_path))
    backends = _read_toml(Path(backends_path))
    if inventory is None:
        return [Machine(**spec) for spec in _DEFAULT_MACHINES]

    runner_classes = load_runner_classes(inventory_path)

    # Collect backend tags keyed loosely by node hint (best-effort enrichment).
    backend_tags: list[str] = []
    if backends is not None:
        for backend in backends.get("backend", []):
            backend_tags.extend(backend.get("tags", []))

    machines: list[Machine] = []
    for node in inventory.get("node", []):
        name = node.get("name")
        if name not in _POOL_BUILDER_NAMES:
            continue
        kind = "frontier" if runner_classes.get(name) == "frontier" else "local"
        tags = [kind]
        tags.extend(tag for tag in ("code",) if tag in backend_tags or True)
        machines.append(Machine(
            name=name,
            kind=kind,
            token_cost_weight=1.0 if kind == "frontier" else 0.0,
            tags=sorted(set(tags)),
            available=bool(node.get(_SCHEDULABLE_KEY, True)),
        ))

    if not any(m.kind == "local" for m in machines):
        # Inventory present but named no known local builders — fall back so the
        # scheduler always has a local option.
        machines += [Machine(**spec) for spec in _DEFAULT_MACHINES if spec["kind"] == "local"]

    # Always offer one frontier builder (metered tokens; high weight).
    machines.append(Machine(
        name=_SYNTHETIC_FRONTIER, kind="frontier", token_cost_weight=1.0,
        tags=["frontier"], available=True,
    ))

    # ...and, symmetrically, always offer one AVAILABLE local builder. The frontier
    # option above is synthetic and hardcoded available=True, so it is the one
    # candidate no config edit can remove; leaving the local side to whatever the
    # inventory happens to say is the asymmetry that let the 2026-08-24 fleet hold
    # hand the token economy to frontier by omission (6d6badc).
    #
    # Testing `kind` alone does not close that hole — it only asks whether a local
    # builder is NAMED, and a parked one is still named. `schedulable = false`, the
    # very key 6d6badc introduced (and which its own note says is where
    # am4-worker-1's exclusion belongs), therefore reproduces the identical
    # frontier-by-omission failure one key over. The guarantee has to be keyed on
    # what the solver can actually place work on: an AVAILABLE local machine.
    if not any(m.kind == "local" and m.available for m in machines):
        machines.append(Machine(
            name=_SYNTHETIC_LOCAL, kind="local", token_cost_weight=0.0,
            tags=["local"], available=True,
        ))
    return machines


def load_capacity(capacity_path: str) -> Optional[dict]:
    """Load a projected capacity.json (capacity.v1). Returns None if absent/unreadable
    so callers degrade to DEFAULT_DURATIONS_S."""
    path = Path(capacity_path)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(document, dict) or document.get("contract_version") != "capacity.v1":
        return None
    return document


def _bucket_p90(document: dict, *, task_class: Optional[str], tool: Optional[str],
                node: Optional[str]) -> Optional[float]:
    """Return the p90 duration (ms) of the first bucket matching the given
    (task_class|tool) x node key, or None.

    A bucket with null p90 (meaning all events were failures, excluded from
    percentiles) does NOT match — we continue to the next bucket in the fallback
    chain rather than using it."""
    for bucket in document.get("buckets", []):
        if node is not None and bucket.get("node") != node:
            continue
        if task_class is not None and bucket.get("task_class") != task_class:
            continue
        if tool is not None and bucket.get("tool") != tool:
            continue
        p90 = (bucket.get("duration_ms") or {}).get("p90")
        # Null p90 means all events for this bucket were failures (excluded from
        # percentiles), so the bucket carries no valid duration data. Skip it.
        if p90 is not None and isinstance(p90, (int, float)) and p90 > 0:
            return float(p90)
    return None


AM4_CATALOG_CONTRACT = "am4-catalog.v1"
OMEN_CATALOG_CONTRACT = "omen-catalog.v1"
CATALOG_CONTRACTS = (AM4_CATALOG_CONTRACT, OMEN_CATALOG_CONTRACT)

# llama-swap declares every single-card OMEN model TWICE (`<m>-vk1` env=1,
# `<m>-vk2` env=2) and the dual 27B once as `<m>-dual` (ADR-0042: the Vulkan index
# is a candidate, never an identity; placement is asserted after load). A job may
# name any of those entries as its `required_model`; the catalog keys each spec
# under its swap entry names too so the lookup resolves. The suffix does NOT pin a
# solver card — the solver still picks the card by VRAM fit.
_SWAP_ENTRY_SUFFIXES = {"single": ("-vk1", "-vk2"), "dual": ("-dual",)}


def _empty_catalog(contract_version: Optional[str] = None) -> dict:
    return {
        "models": {}, "gates": None, "cards": None,
        "contract_version": contract_version, "host": None,
        "resident_models": [], "staging_slots": None, "coresidency": None,
    }


def _spec_from_raw(raw: dict, contract_version: str) -> ModelSpec:
    """One models[] row -> ModelSpec. Both contracts share the required keys
    (am4-catalog.v1's); omen-catalog.v1 adds the rotation fields, mapped here
    and left None when absent or null (receipts only)."""
    spec = ModelSpec(
        model_id=str(raw["model_id"]),
        alias=raw.get("alias"),
        placement=str(raw.get("placement") or "single"),
        visible_devices=raw.get("visible_devices"),
        vram_gb=raw.get("vram_gb"),
        per_card_gb=raw.get("per_card_gb"),
        expected_gen_tps=raw.get("expected_gen_tps"),
        warmup_ms_p50=raw.get("warmup_ms_p50"),
        warmup_ms_max=raw.get("warmup_ms_max"),
        sample_count=raw.get("sample_count"),
        notes=raw.get("notes"),
    )
    if contract_version == OMEN_CATALOG_CONTRACT:
        spec.load_s_steady = raw.get("load_s_steady")
        spec.load_s_first_in_window = raw.get("load_s_first_in_window")
        spec.kv_hydrate_s = raw.get("kv_restore_s")
        spec.kv_save_s = raw.get("kv_save_s")
        spec.unload_drain_s_max = raw.get("unload_drain_s_max")
        spec.exclusive_group = raw.get("exclusive_group")
        spec.swap_entry = raw.get("swap_entry")
        receipts = raw.get("receipts")
        spec.receipt = dict(receipts) if isinstance(receipts, dict) else None
    return spec


def load_model_catalog(path: str, contracts: tuple[str, ...] = CATALOG_CONTRACTS) -> dict:
    """Load a stateful-host model catalog, dispatching on `contract_version`.

    Returns {"models": {name: ModelSpec}, "gates": dict|None, "cards": list|None,
    "contract_version", "host", "resident_models", "staging_slots", "coresidency"}.

      am4-catalog.v1  -> the JS7b body: model_id + alias keys, warmup_ms figures.
      omen-catalog.v1 -> the same plus the rotation fields (load_s_steady,
                         load_s_first_in_window, kv_restore_s -> kv_hydrate_s,
                         unload_drain_s_max, exclusive_group, swap_entry,
                         receipts), cards keyed by BDF, and the document's
                         resident_models / staging_slots / coresidency. Each spec is
                         also keyed under its llama-swap entry names (`<m>-vk1`,
                         `<m>-vk2`, `<m>-dual`) and its declared swap_entry.

    Tolerant of the file being ABSENT, malformed, or of a contract outside
    `contracts`: everything comes back empty/None so the scheduler degrades to
    stateless. A model is keyed by BOTH its model_id and its alias (when distinct)
    so a job's `required_model` may name either.
    """
    p = Path(path)
    if not p.is_file():
        return _empty_catalog()
    try:
        document = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return _empty_catalog()
    if not isinstance(document, dict):
        return _empty_catalog()
    contract_version = document.get("contract_version")
    if contract_version not in contracts:
        return _empty_catalog()

    models: dict[str, ModelSpec] = {}
    for raw in document.get("models", []):
        if not isinstance(raw, dict) or not raw.get("model_id"):
            continue
        spec = _spec_from_raw(raw, contract_version)
        models[spec.model_id] = spec
        if spec.alias and spec.alias not in models:
            models[spec.alias] = spec
        if contract_version == OMEN_CATALOG_CONTRACT:
            entries = [spec.swap_entry] if spec.swap_entry else []
            entries += [spec.model_id + suffix
                        for suffix in _SWAP_ENTRY_SUFFIXES.get(spec.placement, ())]
            for entry in entries:
                models.setdefault(entry, spec)

    cards = document.get("cards")
    resident = document.get("resident_models")
    staging = document.get("staging_slots")
    coresidency = document.get("coresidency")
    return {
        "models": models,
        "gates": document.get("gates"),
        "cards": cards if isinstance(cards, list) else None,
        "contract_version": contract_version,
        "host": document.get("host"),
        "resident_models": [str(m) for m in resident] if isinstance(resident, list) else [],
        "staging_slots": int(staging) if isinstance(staging, int) and not isinstance(staging, bool) else None,
        "coresidency": coresidency if isinstance(coresidency, dict) else None,
    }


def load_am4_catalog(path: str) -> dict:
    """Load the frozen am4-catalog.v1 model catalog. Returns
    {"models": {model_id: ModelSpec}, "gates": dict|None, "cards": list|None}.

    Thin wrapper over load_model_catalog pinned to the AM4 contract: an
    omen-catalog.v1 document at this path degrades to empty (the AM4 machine must
    never inherit OMEN's cards), exactly as before the loader was generalized.
    """
    loaded = load_model_catalog(path, contracts=(AM4_CATALOG_CONTRACT,))
    return {"models": loaded["models"], "gates": loaded["gates"], "cards": loaded["cards"]}


def lookup_duration_s(job: Job, machine: Machine, capacity: Optional[dict],
                      models: Optional[dict] = None) -> float:
    """Estimated duration (seconds) for `job` on `machine`.

    Fallback chain:
      0. model gen-rate: est_out_tokens / expected_gen_tps  (JS7b, only when the job
         names a required_model that the catalog supplies a gen-rate for)
      1. capacity (task_class x node) p90
      2. capacity (tool x node) p90       — tool taken as the job's task_class
      3. DEFAULT_DURATIONS_S[task_class]  — else DEFAULT_DURATIONS_S['default']

    `machine.name` is used as the capacity `node` key; when no node-specific bucket
    exists the same lookups are retried node-agnostic before falling to defaults.
    This is RUN time only — model LOAD/setup time is modeled separately in solve.py.
    """
    if job.est_duration_s is not None and job.est_duration_s > 0:
        return float(job.est_duration_s)
    if models and job.required_model and job.est_out_tokens:
        spec = models.get(job.required_model)
        if spec is not None and spec.expected_gen_tps:
            return float(job.est_out_tokens) / float(spec.expected_gen_tps)
    if capacity is not None:
        for node in (machine.name, None):
            hit = _bucket_p90(capacity, task_class=job.task_class, tool=None, node=node)
            if hit is not None:
                return hit / 1000.0
            hit = _bucket_p90(capacity, task_class=None, tool=job.task_class, node=node)
            if hit is not None:
                return hit / 1000.0
    return DEFAULT_DURATIONS_S.get(job.task_class, DEFAULT_DURATIONS_S["default"])
