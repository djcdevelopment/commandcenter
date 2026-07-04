"""AM4 model-catalog projection (JS7a).

Pure builder: turns the raw B70 stack config (vllama's ``models.json`` catalog
plus b70tools eval ``manifest.json`` warmup samples) into the frozen
``am4-catalog.v1`` knowledge document the CP-SAT scheduler consumes — per-model
VRAM fit, placement, measured warmup (setup) time, and host-RAM safety gates.

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


def build_catalog(models_json: dict, manifests: list[dict],
                   host: str = "am4", gathered_at: str = "") -> dict:
    """Build the am4-catalog.v1 document from vllama's models.json plus a list
    of b70tools eval manifest.json dicts.

    ``models_json`` is expected to have the vllama shape: top-level "safety"
    (gates) and "models" (a dict keyed by model_id). Missing/malformed pieces
    degrade gracefully (nulls), never raise, so a partially-scouted catalog is
    still usable by the scheduler.
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
        })

    models.sort(key=lambda m: m["model_id"])

    return {
        "contract_version": CONTRACT_VERSION,
        "gathered_at": gathered_at,
        "host": host,
        "gates": gates,
        "cards": cards,
        "models": models,
    }
