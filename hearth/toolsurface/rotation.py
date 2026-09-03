"""HEARTH provider: model rotation on OMEN's B70s through llama-swap (ADR-0040 Phase 2, ADR-0045 P9).

Read side (capability ``query``): ``rotation_status``, ``recommend_rung``.
Actuators (capability ``rotation_admin``): ``rotation_window``, ``rotation_load``, ``rotation_unload``,
``rotation_kv_save``, ``rotation_kv_restore``.

Every actuator requires an OPEN, NAMED window (ADR-0044: a rotation is a ledgered span the health
readers exclude) and refuses production: the endpoint may never be a production port, and
``qwen3-30b-a3b`` is never unloaded by these tools. The imagegen tenancy fence is read, not claimed.
No kernel import (provider contract: providers stay kernel-free).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from hearth.rotation.admission import AdmissionGates
from hearth.rotation.kv import KvManifest, restore_slot, save_slot
from hearth.rotation.lifecycle import default_fence, load_with_assertion
from hearth.rotation.swapclient import DEFAULT_ENDPOINT, PRODUCTION_PORTS, LlamaSwapClient
from hearth.rotation.telemetry import b70_snapshot
from hearth.rotation.windows import (append_window_row, close_window, open_window, read_windows,
                                     window_row)

REPO = Path(__file__).resolve().parents[2]
VAR_DIR = REPO / "hearth" / "var"
CATALOG_PATH = REPO / "knowledge" / "omen_catalog.json"
SWAP_RUNG = "omen-swap"
PRODUCTION_MODEL = "qwen3-30b-a3b"
KV_DIR = Path("E:/work/battlemage/kv")          # every side entry's --slot-save-path in omen.yaml
_SIBLING_RE = re.compile(r"^(?P<family>.+)-vk(?P<idx>[12])$")

# Injectable seams (tests patch these; production uses the real ones).
_client_factory: Callable[[str], LlamaSwapClient] = lambda endpoint: LlamaSwapClient(endpoint=endpoint)
_snapshot_fn: Callable[[], object] = b70_snapshot
_fence_fn: Callable[[], Optional[str]] = default_fence


def _windows_path() -> Path:
    return VAR_DIR / "rotation-windows.jsonl"


def _window_dir() -> Path:
    return VAR_DIR / "rotation" / "windows"


def _last_load_path() -> Path:
    return VAR_DIR / "rotation" / "last-load.json"


def _manifest_path() -> Path:
    return VAR_DIR / "kv-manifest.json"


def _refuse_production_endpoint(endpoint: str) -> Optional[dict]:
    port = urlsplit(endpoint).port
    if port in PRODUCTION_PORTS:
        return {"ok": False, "error": f"refused: {endpoint} is a production port; the rotation tools "
                                      f"address llama-swap only", "error_code": "production_port"}
    return None


def _open_window_event(name: str) -> Optional[dict]:
    path = _window_dir() / f"{name}.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return doc if doc.get("state") == "open" else None


def _require_window(name: str) -> Optional[dict]:
    if not name or _open_window_event(name) is None:
        return {"ok": False, "error_code": "no_open_window",
                "error": f"no open rotation window named {name!r}; open one with rotation_window('open', ...)"}
    return None


def _catalog_models() -> dict:
    try:
        doc = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return {}
    return {m.get("model_id"): m for m in doc.get("models", []) if isinstance(m, dict) and m.get("model_id")}


def _catalog_entry_for(model_id: str, catalog: dict) -> Optional[dict]:
    if model_id in catalog:
        return catalog[model_id]
    match = _SIBLING_RE.match(model_id)
    family = match.group("family") if match else model_id.rsplit("-", 1)[0]
    return catalog.get(family)


def _sibling_entries(model_id: str, declared: tuple) -> list:
    match = _SIBLING_RE.match(model_id)
    if not match:
        return [model_id]
    family, idx = match.group("family"), match.group("idx")
    other = f"{family}-vk{'2' if idx == '1' else '1'}"
    return [model_id] + ([other] if other in declared else [])


def _swap_rung():
    from hearth.toolsurface.backends import load_pool

    return load_pool().by_name(SWAP_RUNG)


def _resident_by_bdf(running: list, catalog: dict) -> dict:
    """Per-card GB already held, from what llama-swap reports resident + the catalog's figures.

    Production is dual and charges its per_card_gb on BOTH cards; side models' cards are not
    knowable from /running alone (the last-load receipt records the placed card when available)."""
    resident: dict = {}
    prod = catalog.get(PRODUCTION_MODEL) or {}
    if any(m.model_id == PRODUCTION_MODEL and m.ready for m in running):
        per_card = prod.get("per_card_gb") or 0.0
        for bdf in ("0000:04:00.0", "0000:09:00.0"):
            resident[bdf] = resident.get(bdf, 0.0) + float(per_card)
    last = _read_json(_last_load_path()) or {}
    if last.get("ok") and last.get("card_bdf") and any(m.model_id == last.get("entry_used") and m.ready for m in running):
        gb = last.get("per_card_gb") or 0.0
        resident[last["card_bdf"]] = resident.get(last["card_bdf"], 0.0) + float(gb)
    return resident


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str), encoding="utf-8")


# ----------------------------------------------------------------------------- read side
def rotation_status(endpoint: str = DEFAULT_ENDPOINT) -> dict:
    """What is resident on OMEN's B70s right now, and whether the rotation lane may act.

    Reports llama-swap's /running (unreachable -> reachable:false, never an exception), the
    production rung's health (baseline epoch + observed rate + envelope, ADR-0044), the imagegen
    tenancy fence, any open rotation window, and the catalog's model ids.
    """
    out: dict = {"endpoint": endpoint, "reachable": False, "running": [], "ts": datetime.now(timezone.utc).isoformat()}
    client = _client_factory(endpoint)
    try:
        if client.health():
            out["reachable"] = True
            out["running"] = [{"model": m.model_id, "state": m.state} for m in client.running()]
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"llama-swap: {exc}"
    try:
        from hearth.health.rungstate import live_rung_state

        out["production"] = live_rung_state()
    except Exception as exc:  # noqa: BLE001
        out["production"] = {"verdict": "unknown", "error": str(exc)}
    fence = _fence_fn()
    out["tenancy"] = {"image_session": None if fence in (None, "unreadable") else fence,
                      "readable": fence != "unreadable"}
    open_windows = [name for start, end, name in read_windows(_windows_path()) if end is None]
    out["open_windows"] = open_windows
    out["catalog_models"] = sorted(_catalog_models())
    out["last_load"] = _read_json(_last_load_path())
    return out


def recommend_rung(task_family: str, prompt_bytes: int = 0) -> dict:
    """Which model/rung the authored task-family evidence recommends (advisory; never dispatches).

    Backed by hearth.scheduler.families (P5). Until that module lands, returns ok:false with the
    reason rather than guessing.
    """
    try:
        from hearth.scheduler import families as fam  # type: ignore
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "task-family preferences not available (hearth.scheduler.families absent)",
                "task_family": task_family}
    try:
        loaded = fam.load_families()
        rec = fam.recommend(task_family, max(0, int(prompt_bytes)) // 4, loaded)
        return {"ok": True, "task_family": task_family, "recommendation": rec}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "task_family": task_family}


# ----------------------------------------------------------------------------- actuators
def rotation_window(action: str, name: str, reason: str = "", models: Optional[list[str]] = None,
                    outcome: str = "passed", port: int = 8081, actor_id: str = "hearth") -> dict:
    """Open or close a named rotation window (ADR-0044 exclusion span).

    ``action`` is "open" or "close". Appends the jsonl row the health readers exclude by and returns
    the schema-valid workflow event for the CALLER to record with ``record_event`` -- this tool never
    writes the corpus. Closing takes ``outcome`` in passed|failed|aborted.
    """
    if action not in ("open", "close"):
        return {"ok": False, "error": "action must be 'open' or 'close'"}
    if not name or not re.match(r"^[A-Za-z0-9._-]{3,80}$", name):
        return {"ok": False, "error": "name must be 3-80 chars of [A-Za-z0-9._-]"}
    path = _window_dir() / f"{name}.json"
    if action == "open":
        if _open_window_event(name) is not None:
            return {"ok": False, "error": f"window {name!r} is already open"}
        event = open_window(name, port, models or [], reason, actor_id)
        row = window_row(event)
        append_window_row(row, _windows_path())
        _write_json(path, {"state": "open", "event": event})
        return {"ok": True, "window": name, "event": event, "row": row, "path": str(path)}
    opened = _open_window_event(name)
    if opened is None:
        return {"ok": False, "error_code": "no_open_window", "error": f"window {name!r} is not open"}
    try:
        event = close_window(opened["event"], outcome, {"reason": reason} if reason else None)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    row = window_row(event)
    append_window_row(row, _windows_path())
    _write_json(path, {"state": "closed", "event": opened["event"], "close_event": event})
    return {"ok": True, "window": name, "event": event, "row": row, "path": str(path)}


def rotation_load(model_id: str, window: str, expect_cards: int = 1, reason: str = "",
                  endpoint: str = DEFAULT_ENDPOINT, deadline_s: int = 300) -> dict:
    """Load a declared omen-swap model through llama-swap with admission and placement assertion.

    Refuses without an open window, under an active image session, when the model is not declared on
    the omen-swap rung, when admission fails (VRAM fit / commit floor / thermal / unknown telemetry),
    or when the endpoint is a production port. On a placement mismatch the sibling entry (``-vk1`` <->
    ``-vk2``) is tried once (ADR-0042). Returns the lifecycle receipt rows.
    """
    refusal = _refuse_production_endpoint(endpoint) or _require_window(window)
    if refusal:
        return refusal
    rung = _swap_rung()
    if rung is None or model_id not in rung.models:
        return {"ok": False, "error_code": "model_not_declared",
                "error": f"{model_id!r} is not a model of the {SWAP_RUNG} rung"}
    if model_id == PRODUCTION_MODEL:
        return {"ok": False, "error_code": "production_model", "error": "production is not rotated by this tool"}
    catalog = _catalog_models()
    entry = _catalog_entry_for(model_id, catalog) or {}
    placement = "dual" if model_id.endswith("-dual") else str(entry.get("placement") or "single")
    (VAR_DIR / "swap-logs").mkdir(parents=True, exist_ok=True)   # the side entries' --log-file dir
    KV_DIR.mkdir(parents=True, exist_ok=True)                    # --slot-save-path: llama-server refuses to start without it
    client = _client_factory(endpoint)
    try:
        running = client.running()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_code": "unreachable", "error": f"llama-swap {endpoint}: {exc}"}
    result = load_with_assertion(
        client, _sibling_entries(model_id, tuple(rung.models)), snapshot=_snapshot_fn,
        expected_cards=2 if placement == "dual" else int(expect_cards),
        per_card_gb=entry.get("per_card_gb"), vram_gb=entry.get("vram_gb"), placement=placement,
        resident_by_bdf=_resident_by_bdf(running, catalog), gates=AdmissionGates(), fence=_fence_fn,
        deadline_s=float(deadline_s))
    card = None
    if result.verdict and result.verdict.bdf_delta_gb:
        rose = [bdf for bdf, delta in result.verdict.bdf_delta_gb.items() if delta >= 1.0]
        card = rose[0] if len(rose) == 1 else None
    receipt = {"ok": result.ok, "window": window, "reason": result.reason, "requested": model_id,
               "entry_used": result.entry_used, "attempts": result.attempts, "card_bdf": card,
               "per_card_gb": entry.get("per_card_gb") or entry.get("vram_gb"),
               "load_wall_s": result.load_wall_s, "canary_timings": result.canary_timings,
               "admission": result.admission.__dict__ if result.admission else None,
               "placement": result.verdict.__dict__ if result.verdict else None,
               "events": result.events, "note": reason or None}
    _write_json(_last_load_path(), receipt)
    return receipt


def rotation_unload(model_id: str, window: str, endpoint: str = DEFAULT_ENDPOINT) -> dict:
    """Unload ONE side model (path form). Never production; never without an open window."""
    refusal = _refuse_production_endpoint(endpoint) or _require_window(window)
    if refusal:
        return refusal
    if model_id == PRODUCTION_MODEL:
        return {"ok": False, "error_code": "production_model", "error": "production is never unloaded by the rotation tools"}
    client = _client_factory(endpoint)
    try:
        ok = client.unload(model_id)
        remaining = [m.model_id for m in client.running()]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"llama-swap {endpoint}: {exc}"}
    return {"ok": bool(ok), "window": window, "unloaded": model_id, "running": remaining}


def rotation_kv_save(model_id: str, slot: int, prompt: str, window: str,
                     endpoint: str = DEFAULT_ENDPOINT) -> dict:
    """Save a slot's KV state under the identity-carrying name and record it in the manifest."""
    refusal = _refuse_production_endpoint(endpoint) or _require_window(window)
    if refusal:
        return refusal
    try:
        entry = save_slot(_client_factory(endpoint), model_id, int(slot), prompt, KvManifest(_manifest_path()))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "window": window, "entry": entry.__dict__}


def rotation_kv_restore(model_id: str, slot: int, prompt: str, window: str,
                        endpoint: str = DEFAULT_ENDPOINT) -> dict:
    """Restore a slot's KV state; a cross-model restore is refused before any request."""
    refusal = _refuse_production_endpoint(endpoint) or _require_window(window)
    if refusal:
        return refusal
    try:
        out = restore_slot(_client_factory(endpoint), model_id, int(slot), prompt, KvManifest(_manifest_path()))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_code": type(exc).__name__, "error": str(exc)}
    out["window"] = window
    return out


def get_tools() -> list[Callable]:
    return [rotation_status, recommend_rung, rotation_window, rotation_load, rotation_unload,
            rotation_kv_save, rotation_kv_restore]
