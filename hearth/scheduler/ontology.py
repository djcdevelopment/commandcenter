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

# Local builders eligible for async fleet tasks today (token cost ~= free). A
# hypothetical frontier builder carries a high token-cost weight so the token
# objective only reaches for it when deadlines force parallelism it cannot avoid.
# Used only when inventory/backends files are absent.
_DEFAULT_MACHINES: tuple[dict, ...] = (
    {"name": "am4-worker-1", "kind": "local", "token_cost_weight": 0.0,
     "tags": ["local", "code", "big-context"], "available": True},
    {"name": "cc-builder-1", "kind": "local", "token_cost_weight": 0.0,
     "tags": ["local", "code"], "available": True},
    {"name": "cc-builder-2", "kind": "local", "token_cost_weight": 0.0,
     "tags": ["local", "code"], "available": True},
    {"name": "frontier-builder", "kind": "frontier", "token_cost_weight": 1.0,
     "tags": ["frontier"], "available": True},
)

# Logical/local builder names the scheduler treats as async-eligible local machines.
_LOCAL_BUILDER_NAMES = {"am4-worker-1", "cc-builder-1", "cc-builder-2"}


@dataclass
class ModelSpec:
    """A loadable model on the stateful AM4 machine, sourced from am4-catalog.v1
    models[]. Carries the residency economics JS7b optimizes over: how big it is
    per card (`per_card_gb`), how long it takes to stage into VRAM (`warmup_ms_*`),
    and how fast it generates once warm (`expected_gen_tps`)."""

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

    def setup_s(self, default_s: float = 30.0) -> float:
        """Load (setup) time in seconds: warmup p50, fallback max, fallback default."""
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
    cards: list[dict] = field(default_factory=list)  # [{index, vram_gb}]
    resident_models: list[str] = field(default_factory=list)  # loaded at t=0
    staging_slots: int = 1
    host: Optional[str] = None  # physical host key for DDR4 staging contention


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
    loads: list[dict] = field(default_factory=list)  # [{machine, model_id, cards, start_s, end_s}]
    residency: list[dict] = field(default_factory=list)  # [{machine, card, resident_models, used_gb, budget_gb}]


# --- loaders ----------------------------------------------------------------


def _read_toml(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_machines(inventory_path: str, backends_path: str) -> list[Machine]:
    """Build the machine list from the fleet inventory + backend pool.

    Async-eligible LOCAL builders come from the inventory's logical/builder nodes
    (am4-worker-1, cc-builder-1, cc-builder-2); a single hypothetical frontier
    builder is always appended so deadline-forced parallelism has somewhere to go.
    Backend tags (hearth/etc/backends.toml) enrich local machine tags where a
    backend rides the same node. Missing files -> declared defaults.
    """
    inventory = _read_toml(Path(inventory_path))
    backends = _read_toml(Path(backends_path))
    if inventory is None:
        return [Machine(**spec) for spec in _DEFAULT_MACHINES]

    # Collect backend tags keyed loosely by node hint (best-effort enrichment).
    backend_tags: list[str] = []
    if backends is not None:
        for backend in backends.get("backend", []):
            backend_tags.extend(backend.get("tags", []))

    machines: list[Machine] = []
    for node in inventory.get("node", []):
        name = node.get("name")
        if name not in _LOCAL_BUILDER_NAMES:
            continue
        expect = node.get("expect", "up")
        tags = ["local"]
        tags.extend(tag for tag in ("code",) if tag in backend_tags or True)
        machines.append(Machine(
            name=name,
            kind="local",
            token_cost_weight=0.0,
            tags=sorted(set(tags)),
            available=(expect == "up"),
        ))

    if not machines:
        # Inventory present but named no known local builders — fall back so the
        # scheduler always has a local option.
        machines = [Machine(**spec) for spec in _DEFAULT_MACHINES if spec["kind"] == "local"]

    # Always offer one frontier builder (metered tokens; high weight).
    machines.append(Machine(
        name="frontier-builder", kind="frontier", token_cost_weight=1.0,
        tags=["frontier"], available=True,
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
    (task_class|tool) x node key, or None."""
    for bucket in document.get("buckets", []):
        if node is not None and bucket.get("node") != node:
            continue
        if task_class is not None and bucket.get("task_class") != task_class:
            continue
        if tool is not None and bucket.get("tool") != tool:
            continue
        p90 = (bucket.get("duration_ms") or {}).get("p90")
        if p90 is not None:
            return float(p90)
    return None


AM4_CATALOG_CONTRACT = "am4-catalog.v1"


def load_am4_catalog(path: str) -> dict:
    """Load the frozen am4-catalog.v1 model catalog. Returns
    {"models": {model_id: ModelSpec}, "gates": dict|None, "cards": list|None}.

    Tolerant of the file being ABSENT or malformed: every field comes back None/{}
    so the scheduler degrades to stateless (fully backward compatible with JS7a).
    A model is keyed by BOTH its model_id and its alias (when distinct) so a job's
    `required_model` may name either.
    """
    empty = {"models": {}, "gates": None, "cards": None}
    p = Path(path)
    if not p.is_file():
        return empty
    try:
        document = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(document, dict) or document.get("contract_version") != AM4_CATALOG_CONTRACT:
        return empty

    models: dict[str, ModelSpec] = {}
    for raw in document.get("models", []):
        if not isinstance(raw, dict) or not raw.get("model_id"):
            continue
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
        models[spec.model_id] = spec
        if spec.alias and spec.alias not in models:
            models[spec.alias] = spec

    cards = document.get("cards")
    return {
        "models": models,
        "gates": document.get("gates"),
        "cards": cards if isinstance(cards, list) else None,
    }


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
