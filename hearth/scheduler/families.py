"""Task-family -> model preference, authored with evidence (P5, ADR-0045).

The operator's family-level routing intent lives in
``hearth/etc/routing-families.toml`` (contract ``routing-families.v1``). This
module loads it, validates that every preference carries its evidence, and
turns ``(task_family, prompt_tokens)`` into a RECOMMENDATION. It never routes,
pins, loads, or dispatches anything (ADR-0008: the scheduler advises, the
conductor dispatches; the door routes itself per ``backends.toml``).

Two consumers, two entry points:

* ``resolve_required_model(job, families)`` -- the advisory scheduler's hook. A
  job that already names ``required_model`` keeps it; a job with a
  ``task_family`` gets the family's model at the job's prompt depth; a job with
  neither gets ``None`` (nothing is forced onto a family-less job, so proposals
  without families stay byte-identical). Pure: no pool, no I/O beyond the TOML.
* ``recommend(task_family, prompt_tokens, families)`` -- the door-facing shape
  (rotation provider ``recommend_rung``, ``plan_execution(task_family=...)``).
  Adds ``backend_hint`` derived from ``Pool.by_model`` so the hint can never go
  stale relative to ``backends.toml``, and ``pin_required`` (the rung carries no
  routing tags, so opportunistic routing will never land there -- the caller
  must pin by name).

Depth semantics (``prompt_tokens`` is the caller's estimate; ``None`` = unknown):

* ``min_prompt_tokens`` on a family is an evidence floor: the primary model
  applies at/above it; below it ``below_threshold_model_id`` is recommended.
  Unknown depth keeps the primary model and says so in ``reason``.
* ``depth_override`` wins at/above its own ``min_prompt_tokens`` (the ADR-0039
  jobs/hour inversion). Unknown depth never triggers an override.

Stdlib only; no ``hearth.kernel`` import.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

CONTRACT = "routing-families.v1"
ENV_VAR = "HEARTH_ROUTING_FAMILIES"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "etc" / "routing-families.toml"
DEFAULT_FAMILY = "default"

# The nine equally weighted families of campaign/qwen38/assay/tasks.json
# (qwen38-assay.v1). The test suite re-derives this list from the assay file;
# the constant is here so consumers can enumerate without reading the campaign.
ASSAY_FAMILIES: tuple[str, ...] = (
    "summarization",
    "extraction",
    "classification",
    "drafting",
    "reasoning_planning",
    "tool_execution",
    "document_ocr",
    "chart_diagram",
    "screenshot_grounded",
)


class FamiliesConfigError(ValueError):
    """The routing-families declaration is missing or structurally invalid."""


@dataclass(frozen=True)
class DepthRule:
    """At/above ``min_prompt_tokens`` prompt tokens, ``model_id`` is preferred."""

    min_prompt_tokens: int
    model_id: str
    evidence: str


@dataclass(frozen=True)
class FamilyPreference:
    """One authored family -> model preference and the evidence it cites."""

    name: str
    model_id: str
    evidence: str
    reason: str
    receipt: Optional[str] = None
    receipt_note: Optional[str] = None
    min_prompt_tokens: Optional[int] = None
    below_threshold_model_id: Optional[str] = None
    depth_override: Optional[DepthRule] = None


@dataclass(frozen=True)
class Families:
    """The loaded declaration: every family by name, plus where it came from."""

    contract: str
    families: dict[str, FamilyPreference]
    source: Optional[Path] = None
    authored: Optional[str] = None

    def names(self) -> tuple[str, ...]:
        return tuple(self.families)

    def get(self, task_family: Optional[str]) -> FamilyPreference:
        """The preference for ``task_family``, falling to ``default`` when the
        family is unnamed or unknown. The returned ``.name`` says which."""
        if task_family and task_family in self.families:
            return self.families[task_family]
        return self.families[DEFAULT_FAMILY]


# --------------------------------------------------------------------------- #
# Loading + validation
# --------------------------------------------------------------------------- #

def _require_str(table: dict, key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FamiliesConfigError(f"{where}: {key} must be a non-empty string")
    return value


def _optional_str(table: dict, key: str, where: str) -> Optional[str]:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FamiliesConfigError(f"{where}: {key} must be a non-empty string when present")
    return value


def _positive_int(table: dict, key: str, where: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FamiliesConfigError(f"{where}: {key} must be a positive integer")
    return value


def _coerce_depth_rule(raw: Any, where: str) -> DepthRule:
    if not isinstance(raw, dict):
        raise FamiliesConfigError(f"{where}: depth_override must be a table")
    return DepthRule(
        min_prompt_tokens=_positive_int(raw, "min_prompt_tokens", where),
        model_id=_require_str(raw, "model_id", where),
        evidence=_require_str(raw, "evidence", where),
    )


def _coerce_family(name: str, raw: Any) -> FamilyPreference:
    where = f"family.{name}"
    if not isinstance(raw, dict):
        raise FamiliesConfigError(f"{where}: must be a table")
    min_prompt_tokens: Optional[int] = None
    below: Optional[str] = None
    if raw.get("min_prompt_tokens") is not None:
        min_prompt_tokens = _positive_int(raw, "min_prompt_tokens", where)
        below = _require_str(raw, "below_threshold_model_id", where)
    elif raw.get("below_threshold_model_id") is not None:
        raise FamiliesConfigError(
            f"{where}: below_threshold_model_id needs a min_prompt_tokens floor")
    override: Optional[DepthRule] = None
    if raw.get("depth_override") is not None:
        override = _coerce_depth_rule(raw["depth_override"], f"{where}.depth_override")
    return FamilyPreference(
        name=name,
        model_id=_require_str(raw, "model_id", where),
        evidence=_require_str(raw, "evidence", where),
        reason=_require_str(raw, "reason", where),
        receipt=_optional_str(raw, "receipt", where),
        receipt_note=_optional_str(raw, "receipt_note", where),
        min_prompt_tokens=min_prompt_tokens,
        below_threshold_model_id=below,
        depth_override=override,
    )


def load_families(path: Optional[Path | str] = None) -> Families:
    """Load and validate the declaration.

    Resolution: explicit ``path`` > ``HEARTH_ROUTING_FAMILIES`` > the packaged
    ``hearth/etc/routing-families.toml``. A missing file is an error, not a
    fallback -- this is authored intent, and a silent stand-in would route on
    nobody's say-so (loud fallbacks doctrine).
    """
    resolved = Path(path) if path else Path(os.environ.get(ENV_VAR, DEFAULT_PATH))
    if not resolved.is_file():
        raise FamiliesConfigError(f"routing families file not found: {resolved}")
    with open(resolved, "rb") as fh:
        data = tomllib.load(fh)
    contract = data.get("contract")
    if contract != CONTRACT:
        raise FamiliesConfigError(
            f"{resolved}: contract must be {CONTRACT!r}, got {contract!r}")
    raw_families = data.get("family")
    if not isinstance(raw_families, dict) or not raw_families:
        raise FamiliesConfigError(f"{resolved}: no [family.<name>] tables declared")
    families = {name: _coerce_family(name, raw) for name, raw in raw_families.items()}
    if DEFAULT_FAMILY not in families:
        raise FamiliesConfigError(f"{resolved}: [family.{DEFAULT_FAMILY}] is required")
    authored = data.get("authored")
    return Families(
        contract=contract,
        families=families,
        source=resolved,
        authored=str(authored) if authored is not None else None,
    )


# --------------------------------------------------------------------------- #
# Picking a model (pure)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _Pick:
    model_id: str
    evidence: str
    reason: str
    depth_rule_applied: bool


def _pick_model(pref: FamilyPreference, prompt_tokens: Optional[int]) -> _Pick:
    """Apply the family's depth semantics to one prompt-depth estimate."""
    known = prompt_tokens is not None
    if pref.min_prompt_tokens is not None:
        if known and prompt_tokens < pref.min_prompt_tokens:
            return _Pick(
                model_id=pref.below_threshold_model_id or pref.model_id,
                evidence=pref.evidence,
                reason=(
                    f"prompt_tokens {prompt_tokens} < min_prompt_tokens "
                    f"{pref.min_prompt_tokens}: the {pref.name} evidence floor is not "
                    f"reached, so {pref.below_threshold_model_id} is recommended "
                    f"instead of {pref.model_id}. {pref.reason}"
                ),
                depth_rule_applied=False,
            )
    if pref.depth_override is not None and known:
        rule = pref.depth_override
        if prompt_tokens >= rule.min_prompt_tokens:
            return _Pick(
                model_id=rule.model_id,
                evidence=rule.evidence,
                reason=(
                    f"depth_override: prompt_tokens {prompt_tokens} >= "
                    f"{rule.min_prompt_tokens}, so {rule.model_id} is preferred over "
                    f"{pref.model_id} for {pref.name}. {pref.reason}"
                ),
                depth_rule_applied=True,
            )
    reason = pref.reason
    if not known and (pref.min_prompt_tokens is not None or pref.depth_override is not None):
        floors = []
        if pref.min_prompt_tokens is not None:
            floors.append(f"min_prompt_tokens={pref.min_prompt_tokens}")
        if pref.depth_override is not None:
            floors.append(f"depth_override at {pref.depth_override.min_prompt_tokens}")
        reason = f"{reason} (prompt_tokens unknown; {', '.join(floors)} not checked)"
    return _Pick(model_id=pref.model_id, evidence=pref.evidence, reason=reason,
                 depth_rule_applied=False)


def _field(job: Any, name: str) -> Any:
    """Read ``name`` off a Job dataclass or a snapshot dict, else ``None``."""
    if isinstance(job, dict):
        return job.get(name)
    return getattr(job, name, None)


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def job_prompt_tokens(job: Any) -> Optional[int]:
    """The depth estimate for a job: ``prompt_tokens`` when the snapshot carries
    it, else ``est_tokens`` (the pre-P2 field, the same quantity by intent)."""
    tokens = _as_int(_field(job, "prompt_tokens"))
    if tokens is None:
        tokens = _as_int(_field(job, "est_tokens"))
    return tokens


def resolve_required_model(job: Any, families: Optional[Families] = None) -> Optional[str]:
    """The model a job should require, or ``None`` to leave it unconstrained.

    Precedence: an explicit ``required_model`` on the job wins untouched; then
    the job's ``task_family`` (unknown families fall to ``default``) at the
    job's prompt depth; a job with no family returns ``None`` so family-less
    proposals stay exactly as they were. Accepts a ``Job`` dataclass or the
    snapshot dict shape.
    """
    explicit = _field(job, "required_model")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    task_family = _field(job, "task_family")
    if not isinstance(task_family, str) or not task_family.strip():
        return None
    if families is None:
        families = load_families()
    pref = families.get(task_family)
    return _pick_model(pref, job_prompt_tokens(job)).model_id


# --------------------------------------------------------------------------- #
# The door-facing recommendation
# --------------------------------------------------------------------------- #

def _providers_for(model_id: str, pool: Any) -> list:
    """Non-retired backends that declare ``model_id`` (``Pool.by_model``)."""
    try:
        found = pool.by_model(model_id)
    except Exception:  # a malformed pool must not break a recommendation
        return []
    return [b for b in found if not getattr(b, "retired", False)]


def recommend(task_family: Optional[str], prompt_tokens: Optional[int] = None,
              families: Optional[Families] = None, *, pool: Any = None) -> dict:
    """Recommend a model (and the rung that serves it) for one task.

    Returns::

        {family, requested_family, model_id, backend_hint, providers, evidence,
         reason, depth_rule_applied, pin_required, prompt_tokens, advisory: True}

    ``backend_hint`` is the first non-retired rung whose ``models`` list names
    ``model_id`` (``hearth.toolsurface.backends.Pool.by_model``); ``None`` when
    nothing declared serves it. ``pin_required`` is True when that rung has no
    routing tags -- the router never picks it opportunistically, so a caller
    who wants this model must pass ``backend=<hint>``. Recommendation only: this
    function performs no dispatch, no pin, no load.
    """
    if families is None:
        families = load_families()
    if pool is None:
        from hearth.toolsurface.backends import load_pool  # local: keeps import cheap
        pool = load_pool()
    depth = _as_int(prompt_tokens)
    pref = families.get(task_family)
    pick = _pick_model(pref, depth)
    providers = _providers_for(pick.model_id, pool)
    hint = providers[0] if providers else None
    reason = pick.reason
    if hint is None:
        reason = f"{reason} (no declared, non-retired backend serves {pick.model_id})"
    return {
        "family": pref.name,
        "requested_family": task_family,
        "model_id": pick.model_id,
        "backend_hint": hint.name if hint is not None else None,
        "providers": [b.name for b in providers],
        "evidence": pick.evidence,
        "reason": reason,
        "depth_rule_applied": pick.depth_rule_applied,
        "pin_required": bool(hint is not None and not hint.tags),
        "prompt_tokens": depth,
        "advisory": True,
    }


__all__ = [
    "ASSAY_FAMILIES",
    "CONTRACT",
    "DEFAULT_FAMILY",
    "DEFAULT_PATH",
    "DepthRule",
    "Families",
    "FamiliesConfigError",
    "FamilyPreference",
    "job_prompt_tokens",
    "load_families",
    "recommend",
    "resolve_required_model",
]
