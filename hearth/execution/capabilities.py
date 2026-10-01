"""HEARTH backend capability records (Task 10, ADR-0052, ADR-0050).

Loads per-backend capability records containing sizing constraints (context, slots,
output reserve), performance baselines (decode tok/s, prefill tok/s, cold-load time),
and qualification outcomes per task family (derived from Tasks 8 and 9).

Used by ``ExecutionService.plan`` and ``plan_execution`` to attach an informative
capability slice explaining why a lane was chosen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_CAPABILITIES_DIR = Path(__file__).resolve().parents[1] / "etc" / "capabilities"
ENV_CAPABILITIES_DIR = "HEARTH_CAPABILITIES_DIR"

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _resolve_capabilities_dir() -> Path:
    env = os.environ.get(ENV_CAPABILITIES_DIR)
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    return DEFAULT_CAPABILITIES_DIR


def load_backend_capability(backend_name: str) -> Optional[dict[str, Any]]:
    """Load the capability record for a given backend name, cached by mtime."""
    if not backend_name or not isinstance(backend_name, str):
        return None
    cap_dir = _resolve_capabilities_dir()
    target_file = cap_dir / f"{backend_name}.json"
    if not target_file.is_file():
        return None
    try:
        mtime = target_file.stat().st_mtime
        hit = _CACHE.get(str(target_file))
        if hit and hit[0] == mtime:
            return hit[1]
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CACHE[str(target_file)] = (mtime, data)
        return data
    except Exception:
        return None


def get_capability_slice(
    backend_name: Optional[str],
    task_family: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the capability slice for plan_execution and job dispatch."""
    if not backend_name:
        return None
    cap = load_backend_capability(backend_name)
    if not cap:
        return None
    slice_data: dict[str, Any] = {
        "backend": backend_name,
        "model": cap.get("model"),
        "node": cap.get("node"),
        "context_tokens": cap.get("context_tokens"),
        "parallel_slots": cap.get("parallel_slots"),
        "max_tokens": cap.get("max_tokens"),
        "decode_tok_per_s": cap.get("decode_tok_per_s"),
        "prefill_tok_per_s": cap.get("prefill_tok_per_s"),
        "cold_load_s": cap.get("cold_load_s"),
        "citations": cap.get("citations"),
    }
    families_map = cap.get("task_families", {})
    if task_family and task_family in families_map:
        fam_info = families_map[task_family]
        slice_data["task_family"] = task_family
        slice_data["outcome"] = fam_info.get("outcome")
        slice_data["evidence_status"] = fam_info.get("evidence_status")
        slice_data["observation"] = fam_info.get("observation")
        slice_data["citation"] = fam_info.get("citation")
    elif task_family:
        slice_data["task_family"] = task_family
        slice_data["outcome"] = "unqualified"
        slice_data["evidence_status"] = "unqualified"
    return slice_data
