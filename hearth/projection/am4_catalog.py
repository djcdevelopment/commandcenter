"""AM4 model-catalog projection (JS7a + O5 llama-server extension).

Pure builder: turns the raw B70 stack config (vllama's ``models.json`` catalog
plus b70tools eval ``manifest.json`` warmup samples) into the frozen
``am4-catalog.v1`` knowledge document the CP-SAT scheduler consumes — per-model
VRAM fit, placement, measured warmup (setup) time, and host-RAM safety gates.

Since ADR-0018 the steady-state tenant of both B70s is a llama-server-served
resident MoE (``b70-moe.service``, :8082) that vllama's catalog knows nothing
about, so the projection also accepts an optional ``llama_server`` payload
(unit/serve-script text + a ``/v1/models`` serve-truth probe) plus the O5
capacity-facts document, and emits those models as additional ``models[]``
entries. The contract stays am4-catalog.v1: every required field is present and
the llama-server extras (``served_by``, ``port``, ``serving``, ``slots``,
``n_ctx``, ``prefill_tps``, ``goodput_tps``) are additive optional fields,
which the frozen shape explicitly permits.

No I/O here — ``hearth.toolsurface.am4`` owns the SSH gather and the write.
This module only shapes already-fetched dicts, so it is trivially unit-testable
with fixtures (no live SSH in tests).
"""

from __future__ import annotations

import re
from typing import Optional

CONTRACT_VERSION = "am4-catalog.v1"
CARD_VRAM_GB = 32.0
CARD_COUNT = 2

# Matches the first "<number> GB" occurring in a free-text note, e.g.
# "8.4 GB single-card territory" or "MoE 17.3 GB, 256k native.".
_VRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*GB", re.IGNORECASE)


def _parse_vram_gb(note: Optional[str]) -> Optional[float]:
    """Best-effort VRAM (GB) extraction from a free-text notes field.

    Tolerates messiness: returns None (never raises) when no "<number> GB"
    pattern is present.
    """
    if not isinstance(note, str):
        return None
    match = _VRAM_RE.search(note)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Stdlib nearest-rank percentile (mirrors hearth/projection/capacity.py's
    convention: simple, dependency-free, deterministic on ties)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    frac = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * frac


def _basename(path: Optional[str]) -> Optional[str]:
    if not isinstance(path, str) or not path:
        return None
    normalized = path.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1]


def _collect_warmups_by_file(manifests: list[dict]) -> dict[str, list[float]]:
    """Group warmup.wall_ms samples by the model file basename referenced in
    each manifest's model_path. Manifests missing warmup/model_path, or with a
    non-numeric wall_ms, are skipped (tolerated, not fatal)."""
    by_file: dict[str, list[float]] = {}
    for manifest in manifests or []:
        if not isinstance(manifest, dict):
            continue
        file_name = _basename(manifest.get("model_path"))
        if not file_name:
            continue
        warmup = manifest.get("warmup")
        if not isinstance(warmup, dict):
            continue
        wall_ms = warmup.get("wall_ms")
        if not isinstance(wall_ms, (int, float)):
            continue
        by_file.setdefault(file_name, []).append(float(wall_ms))
    return by_file


# llama-server CLI flags as they appear in the b70-moe unit / serve script.
# Short flags use a lookbehind so e.g. the "-m" inside "--metrics" or the "-c"
# inside "-ctk q8_0" can never match.
_ARG_RES = {
    "alias": re.compile(r"--alias\s+(\S+)"),
    "model_path": re.compile(r"(?<![\w-])-m\s+(\S+)"),
    "devices": re.compile(r"(?<![\w-])-dev\s+(\S+)"),
    "port": re.compile(r"--port\s+(\d+)"),
    "slots": re.compile(r"(?<![\w-])-np\s+(\d+)"),
    "n_ctx": re.compile(r"(?<![\w-])-c\s+(\d+)"),
}


def _parse_serve_args(text: Optional[str]) -> dict:
    """Best-effort extraction of the llama-server launch flags from unit /
    serve-script text. Missing flags come back absent, never raise."""
    if not isinstance(text, str) or not text:
        return {}
    parsed: dict = {}
    for key, pattern in _ARG_RES.items():
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(1)
        parsed[key] = int(value) if value.isdigit() else value
    return parsed


def _fact_value(capacity_facts: Optional[dict], backend: str, metric: str) -> Optional[float]:
    """Look up one numeric fact from the O5 capacity-facts document
    (am4-fleet-node/results/capacity-facts-*.json shape)."""
    if not isinstance(capacity_facts, dict):
        return None
    for fact in capacity_facts.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        if fact.get("backend") == backend and fact.get("metric") == metric:
            value = fact.get("value")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _build_llama_server_models(llama_server: Optional[dict],
                                capacity_facts: Optional[dict]) -> list[dict]:
    """Shape the llama-server-served models (the resident MoE lane, ADR-0018)
    into am4-catalog.v1 entries.

    Signals, all optional: ``unit_text`` / ``serve_script`` (launch flags),
    ``unit_active`` (systemctl is-active), ``models_api`` (the :8082
    ``/v1/models`` serve-truth response). No signals at all -> no entries, so
    vllama-only gathers are byte-identical to the pre-extension catalog.
    Measured perf comes from the O5 capacity facts keyed ``backend=am4-moe``
    (the :8082 lane IS the am4-moe rung) and ``am4-host``/``vram_per_card``.
    """
    llama_server = llama_server if isinstance(llama_server, dict) else {}
    models_api = llama_server.get("models_api")
    models_api = models_api if isinstance(models_api, dict) else None
    unit_active = llama_server.get("unit_active")

    args = _parse_serve_args(
        "\n".join(text for text in (llama_server.get("serve_script"),
                                    llama_server.get("unit_text"))
                  if isinstance(text, str)))

    api_ids: list[str] = []
    if models_api is not None:
        for item in models_api.get("data") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                api_ids.append(item["id"])

    if not args and models_api is None and not isinstance(unit_active, str):
        return []

    alias = args.get("alias") if isinstance(args.get("alias"), str) else None
    model_id = alias or (api_ids[0] if api_ids else None)
    if model_id is None:
        model_path = args.get("model_path")
        model_id = _basename(model_path).rsplit(".", 1)[0] if _basename(model_path) else None
    if model_id is None:
        return []

    # Placement from -dev (SYCL0,SYCL1 -> dual, "0,1"); the resident-moe lane
    # is dual-card by design, so that is also the default when flags are absent.
    devices = args.get("devices") if isinstance(args.get("devices"), str) else None
    device_indices = re.findall(r"(\d+)", devices) if devices else []
    if devices:
        placement = "dual" if len(device_indices) >= 2 else "single"
        visible_devices = ",".join(device_indices) or None
    else:
        placement, visible_devices = "dual", "0,1"

    # Fit-required resident model: it soaks the card(s) entirely, so the honest
    # scheduler charge is the full measured per-card VRAM, not a file size.
    vram_per_card_mib = _fact_value(capacity_facts, "am4-host", "vram_per_card")
    per_card_gb = round(vram_per_card_mib / 1024.0, 1) if vram_per_card_mib else None
    vram_gb = None
    if per_card_gb is not None:
        vram_gb = round(per_card_gb * (2.0 if placement == "dual" else 1.0), 1)

    solo_tps = _fact_value(capacity_facts, "am4-moe", "solo_decode_rate")
    goodput_tps = _fact_value(capacity_facts, "am4-moe", "aggregate_decode_ceiling")
    prefill_tps = _fact_value(capacity_facts, "am4-moe", "prefill_rate")
    cold_load_min = _fact_value(capacity_facts, "am4-moe", "cold_load_time")
    cold_load_ms = cold_load_min * 60_000.0 if cold_load_min else None

    if models_api is not None:
        serving = model_id in api_ids or (alias is not None and alias in api_ids)
    elif isinstance(unit_active, str):
        serving = unit_active.strip() == "active"
    else:
        serving = None

    slots = args.get("slots") if isinstance(args.get("slots"), int) else None
    n_ctx = args.get("n_ctx") if isinstance(args.get("n_ctx"), int) else None

    note_bits = [f"Resident MoE via llama-server (b70-moe.service, ADR-0018); "
                 f"{placement}-card, fit-required — charges full per-card VRAM."]
    if slots and n_ctx:
        note_bits.append(f"{slots} slots x {n_ctx // slots} ctx/slot — over-limit "
                         "prompts SILENTLY truncate (GAMBIT).")
    if capacity_facts is not None and (solo_tps or goodput_tps or prefill_tps):
        note_bits.append("Perf from O5 capacity facts (GAMBIT 2026-07-18): "
                         "solo decode / aggregate goodput / warm prefill = "
                         f"{solo_tps} / {goodput_tps} / {prefill_tps} tok/s.")

    return [{
        "model_id": model_id,
        "alias": alias or model_id,
        "placement": placement,
        "visible_devices": visible_devices,
        "vram_gb": vram_gb,
        "per_card_gb": per_card_gb,
        "expected_gen_tps": solo_tps,
        "warmup_ms_p50": cold_load_ms,
        "warmup_ms_max": cold_load_ms,
        "sample_count": 1 if cold_load_ms is not None else 0,
        "notes": " ".join(note_bits),
        # additive optional fields (allowed by the frozen v1 shape)
        "served_by": "llama-server",
        "port": args.get("port") if isinstance(args.get("port"), int) else None,
        "serving": serving,
        "slots": slots,
        "n_ctx": n_ctx,
        "prefill_tps": prefill_tps,
        "goodput_tps": goodput_tps,
    }]


def build_catalog(models_json: dict, manifests: list[dict],
                   host: str = "am4", gathered_at: str = "",
                   llama_server: Optional[dict] = None,
                   capacity_facts: Optional[dict] = None) -> dict:
    """Build the am4-catalog.v1 document from vllama's models.json plus a list
    of b70tools eval manifest.json dicts.

    ``models_json`` is expected to have the vllama shape: top-level "safety"
    (gates) and "models" (a dict keyed by model_id). Missing/malformed pieces
    degrade gracefully (nulls), never raise, so a partially-scouted catalog is
    still usable by the scheduler.

    ``llama_server`` (optional) carries the b70-moe signals gathered alongside
    (unit_text/serve_script/unit_active + the /v1/models probe) and
    ``capacity_facts`` the O5 facts document; together they yield the
    llama-server-served entries vllama is blind to. Both default to None, which
    reproduces the pre-extension catalog exactly.
    """
    models_json = models_json if isinstance(models_json, dict) else {}
    safety = models_json.get("safety") if isinstance(models_json.get("safety"), dict) else {}
    raw_models = models_json.get("models") if isinstance(models_json.get("models"), dict) else {}

    warmups_by_file = _collect_warmups_by_file(manifests)

    gates = {
        "max_host_used_gb_preflight": safety.get("max_host_used_gb_preflight"),
        "telemetry_settle_sec": safety.get("telemetry_settle_sec"),
        "health_timeout_sec": safety.get("health_timeout_sec"),
        "verdict_gate": safety.get("verdict_gate"),
    }

    cards = [{"index": i, "vram_gb": CARD_VRAM_GB} for i in range(CARD_COUNT)]

    models: list[dict] = []
    for model_id, entry in raw_models.items():
        entry = entry if isinstance(entry, dict) else {}
        note = entry.get("note")
        vram_gb = _parse_vram_gb(note)
        placement = entry.get("placement")
        per_card_gb = None
        if vram_gb is not None:
            per_card_gb = vram_gb / 2.0 if placement == "dual" else vram_gb

        file_name = entry.get("file")
        samples = warmups_by_file.get(file_name, []) if file_name else []

        models.append({
            "model_id": model_id,
            "alias": entry.get("alias"),
            "placement": placement if placement in ("single", "dual") else "single",
            "visible_devices": entry.get("visible_devices"),
            "vram_gb": vram_gb,
            "per_card_gb": per_card_gb,
            "expected_gen_tps": entry.get("expected_gen_tps"),
            "warmup_ms_p50": _percentile(samples, 50),
            "warmup_ms_max": max(samples) if samples else None,
            "sample_count": len(samples),
            "notes": note,
            "served_by": "vllama",
        })

    models.extend(_build_llama_server_models(llama_server, capacity_facts))
    models.sort(key=lambda m: m["model_id"])

    return {
        "contract_version": CONTRACT_VERSION,
        "gathered_at": gathered_at,
        "host": host,
        "gates": gates,
        "cards": cards,
        "models": models,
    }
