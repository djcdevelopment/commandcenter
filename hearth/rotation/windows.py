"""Named rotation windows -- the ledgered spans a health reader must exclude (ADR-0044, ADR-0045 P3).

A window is opened before the first GPU touch of a rotation proof or cutover and closed with an
outcome, exactly as the 04:29-04:32 artificial episode was excluded from the INC-2026-08-30-A
recurrence record. Two carriers, no contract change:

1. ``hearth/var/rotation-windows.jsonl`` -- one row per open/close, read by
   ``hearth.health.rungstate`` (``windows=``) and ``campaign/lz-probes/etw11_recurrence.py``.
2. A schema-valid workflow event (``assay.started`` / ``assay.passed`` / ``assay.failed`` -- all
   in ``contracts/workflow-event.schema.json``'s enum) for the caller to record with the door's
   ``record_event`` tool. This module never writes the corpus itself.

Pure except for the jsonl append (path injectable; BOM-less UTF-8, LF).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_WINDOWS_PATH = Path("C:/work/commandcenter/hearth/var/rotation-windows.jsonl")
WORKFLOW_ID = "wf-rotation-side-port"
OUTCOMES = ("passed", "failed", "aborted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_to_epoch(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def open_window(name: str, port: int, models: Iterable[str], reason: str, actor_id: str,
                *, ts: Optional[str] = None) -> dict:
    """The ``assay.started`` event for a window (a dict; validate/record it yourself)."""
    stamp = ts or _now()
    return {
        "event_id": f"evt_rotwin_{uuid.uuid4().hex[:12]}",
        "event_type": "assay.started",
        "timestamp": stamp,
        "workflow_id": WORKFLOW_ID,
        "run_id": name,
        "actor": {"type": "operator", "id": actor_id},
        "status": "started",
        "outcome": None,
        "assay_id": f"rotation-window:{name}",
        "payload": {
            "source": "hearth.rotation.windows",
            "window": name,
            "phase": "open",
            "port": int(port),
            "models": [str(m) for m in models],
            "reason": reason,
            "ts_start": stamp,
        },
    }


def close_window(opened: dict, outcome: str, receipts: Optional[dict] = None,
                 *, ts: Optional[str] = None) -> dict:
    """The matching ``assay.passed``/``assay.failed`` event; ``aborted`` records as failed."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    stamp = ts or _now()
    passed = outcome == "passed"
    payload = dict(opened.get("payload") or {})
    payload.update({"phase": "close", "ts_end": stamp, "result": outcome,
                    "receipts": receipts or {}})
    return {
        "event_id": f"evt_rotwin_{uuid.uuid4().hex[:12]}",
        "event_type": "assay.passed" if passed else "assay.failed",
        "timestamp": stamp,
        "workflow_id": opened.get("workflow_id", WORKFLOW_ID),
        "run_id": opened.get("run_id"),
        "actor": dict(opened.get("actor") or {"type": "operator", "id": "unknown"}),
        "status": "completed" if passed else "failed",
        "outcome": "success" if passed else "failure",
        "assay_id": opened.get("assay_id"),
        # tools/workflow/ontology.py: assay.passed / assay.failed REQUIRE candidate_id (the thing under
        # assay). The 2026-09-03 proof's close was refused by record_event without it.
        "candidate_id": opened.get("candidate_id") or f"rotation-window:{opened.get('run_id')}",
        "payload": payload,
    }


def window_row(event: dict) -> dict:
    """The compact jsonl row for an open/close event."""
    payload = event.get("payload") or {}
    return {
        "ts": event.get("timestamp"),
        "event": "window.open" if payload.get("phase") == "open" else "window.close",
        "name": event.get("run_id"),
        "reason": payload.get("reason"),
        "ports": [payload.get("port")] if payload.get("port") else [],
        "models": payload.get("models", []),
        "status": "open" if payload.get("phase") == "open" else payload.get("result"),
        "event_id": event.get("event_id"),
    }


def append_window_row(row: dict, path: Path | str = DEFAULT_WINDOWS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_windows(path: Path | str = DEFAULT_WINDOWS_PATH) -> list[tuple]:
    """``[(start_epoch, end_epoch|None, name), ...]`` -- an open window without a close is
    open-ended (``end_epoch`` None), which readers treat as 'until now'."""
    path = Path(path)
    if not path.is_file():
        return []
    starts: dict = {}
    ends: dict = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("name")
        epoch = _ts_to_epoch(str(row.get("ts", "")))
        if not name or epoch is None:
            continue
        if row.get("event") == "window.open" or row.get("status") == "open":
            starts.setdefault(name, epoch)
        else:
            ends[name] = epoch
    out = []
    for name, start in starts.items():
        out.append((start, ends.get(name), name))
    out.sort()
    return out


def excluded_by(epoch: float, windows: Iterable[tuple], now: Optional[float] = None) -> Optional[str]:
    """The name of the window covering ``epoch``, or None."""
    for start, end, name in windows:
        stop = end if end is not None else (now if now is not None else float("inf"))
        if start <= epoch <= stop:
            return name
    return None
