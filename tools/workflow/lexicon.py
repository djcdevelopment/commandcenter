"""HEARTH / mechnet ontology lexicon -- loader + resolver.

Presentation layer only. This module reads tools/workflow/lexicon.toml and
resolves technical span/event views into the project's lore phrasing (hearth,
guard dog, charm, banked fire, ember, watchfire...). It NEVER writes lore into
the ledger or the durable event log -- callers keep the technical event_type /
actor id as their own fields and use these helpers for *display* fields only.
This preserves the repo rule "no authorship where derivation exists".

Format: TOML + stdlib tomllib (Python 3.11+), matching the repo's stdlib-only
rule (fleet/inventory.toml: "no PyYAML dependency by design"). Neither the
system interpreter nor fleet-worker-node/.venv-omen ships PyYAML; both ship
tomllib. Zero new dependencies.

Hard fallback rule: any unknown event type or actor id resolves to the
technical string verbatim as its display. These helpers never invent lore and
never raise in render paths.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tools.workflow.ontology import EVENT_TO_PHASE, EVENT_TYPES

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.toml"

# Module-level caches. Keyed by resolved path so an explicit override does not
# collide with the default. Callers in hot render paths pay the file read once.
_LEXICON_CACHE: dict[Path, dict] = {}
_INVENTORY_CACHE: dict[Path, dict[str, dict]] = {}


def load_lexicon(path: Path | None = None, *, reload: bool = False) -> dict:
    """Load and cache the lexicon TOML. `reload=True` bypasses the cache."""
    resolved = Path(path).resolve() if path is not None else DEFAULT_LEXICON_PATH
    if reload or resolved not in _LEXICON_CACHE:
        with resolved.open("rb") as handle:
            _LEXICON_CACHE[resolved] = tomllib.load(handle)
    return _LEXICON_CACHE[resolved]


def _load_inventory(path: Path, *, reload: bool = False) -> dict[str, dict]:
    """Load fleet/inventory.toml into {node_name: node_dict}. Best-effort:
    a missing or malformed inventory yields an empty map -- enrichment is
    additive and never fails a render path."""
    resolved = Path(path).resolve()
    if reload or resolved not in _INVENTORY_CACHE:
        index: dict[str, dict] = {}
        try:
            with resolved.open("rb") as handle:
                data = tomllib.load(handle)
            for node in data.get("node", []):
                name = node.get("name")
                if name:
                    index[name] = node
        except (OSError, tomllib.TOMLDecodeError):
            index = {}
        _INVENTORY_CACHE[resolved] = index
    return _INVENTORY_CACHE[resolved]


def label_event(event: dict, *, lexicon_path: Path | None = None) -> dict:
    """Resolve a workflow event to its lore display.

    Returns {display, technical, phase_display, gloss}. `technical` is always
    the raw event_type. Unknown types fall back to the event_type verbatim as
    `display`, with `phase_display`/`gloss` None. Never raises.

    Tool-aware override: hearth-ledger events all map to the neutral
    "work.accepted" event type by design (durable data is unchanged). If the
    event carries a payload.tool with a [tools] entry in the lexicon, that
    entry's display takes precedence over the event-type display for
    `display` only -- `gloss` and `phase_display` still come from the event
    type. Events with no matching [tools] entry are unaffected.
    """
    lexicon = load_lexicon(lexicon_path)
    event_type = event.get("event_type")
    entry = lexicon.get("events", {}).get(event_type or "", {})

    phase = EVENT_TO_PHASE.get(event_type)
    phase_display = lexicon.get("phases", {}).get(phase) if phase else None

    display = entry.get("display", event_type)
    tool = (event.get("payload") or {}).get("tool")
    if tool:
        tool_entry = lexicon.get("tools", {}).get(tool)
        if tool_entry:
            display = tool_entry.get("display", display)

    return {
        "display": display,
        "technical": event_type,
        "phase_display": phase_display,
        "gloss": entry.get("gloss"),
    }


def label_actor(
    actor_id: str | None,
    runner_class: str | None = None,
    *,
    lexicon_path: Path | None = None,
    inventory_path: Path | None = None,
) -> dict:
    """Resolve an actor id to its lore display.

    Resolution order:
      1. Exact-id rule in [actors.exact] (e.g. mechnet-watchdog -> the guard dog).
      2. Inventory enrichment: if the id names a node in fleet/inventory.toml
         whose kind is in [actors.enrich].match_kinds, display = its name and
         tooltip = its purpose.
      3. Fallback: the actor_id verbatim as display, tooltip None.

    `runner_class` is accepted for signature parity with the ledger adapter and
    reserved for future rules; it does not currently change resolution. Never
    raises; a falsy actor_id returns display "" verbatim.
    """
    lexicon = load_lexicon(lexicon_path)
    actors = lexicon.get("actors", {})

    if not actor_id:
        return {"display": actor_id or "", "technical": actor_id, "tooltip": None}

    exact = actors.get("exact", {}).get(actor_id)
    if exact:
        return {
            "display": exact.get("display", actor_id),
            "technical": actor_id,
            "tooltip": exact.get("tooltip"),
        }

    enrich = actors.get("enrich")
    if enrich:
        inv_path = inventory_path if inventory_path is not None else (_ROOT / enrich.get("source", "fleet/inventory.toml"))
        node = _load_inventory(inv_path).get(actor_id)
        if node and node.get("kind") in set(enrich.get("match_kinds", [])):
            display_field = enrich.get("display_field", "name")
            tooltip_field = enrich.get("tooltip_field", "purpose")
            return {
                "display": node.get(display_field, actor_id),
                "technical": actor_id,
                "tooltip": node.get(tooltip_field),
            }

    return {"display": actor_id, "technical": actor_id, "tooltip": None}


def check_completeness(*, lexicon_path: Path | None = None) -> list[str]:
    """Return the sorted list of EVENT_TYPES missing an [events.<type>] entry.

    Empty list == the lexicon covers every ontology event type. This is the
    drift guard: a new event type in ontology.py without a lore entry shows up
    here (and fails the completeness test).
    """
    lexicon = load_lexicon(lexicon_path)
    events = lexicon.get("events", {})
    return sorted(event_type for event_type in EVENT_TYPES if event_type not in events)
