"""The sizer the door consults: heuristic in-process, or the loopback encoder
service (``omen-sizer.service``, tools/sizer/serve.py) with the heuristic as the
fallback on any fault. One env gate, read per call so a host can flip it live:

    HEARTH_SIZER=off        (default) never consulted; every route byte-identical
    HEARTH_SIZER=heuristic  hearth.sizer.heuristic, in-process, < 1 ms
    HEARTH_SIZER=npu        POST {HEARTH_SIZER_URL}/size with a hard timeout
                            (HEARTH_SIZER_TIMEOUT_MS, default 30); on timeout,
                            connection refusal or a malformed answer, the
                            heuristic answers and `source` says "heuristic"
                            with `fallback_from: "npu"` so the ledger shows the
                            service was asked and did not answer.

The result feeds ADMISSION only (the output reserve the router checks a rung
against) and the tool-family refinement; it never sets the generation budget,
so a wrong bin can misroute but cannot truncate.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

from hearth.sizer.heuristic import size_heuristic

SIZER_ENV = "HEARTH_SIZER"
SIZER_URL_ENV = "HEARTH_SIZER_URL"
SIZER_TIMEOUT_ENV = "HEARTH_SIZER_TIMEOUT_MS"
DEFAULT_SIZER_URL = "http://127.0.0.1:8797"
DEFAULT_TIMEOUT_MS = 30
MODES = ("off", "heuristic", "npu")
# The encoder reads the instruction head only; the service truncates to its
# static window, but the door already keeps the request body small.
_HEAD_CHARS = 4000


def sizer_mode() -> str:
    raw = (os.environ.get(SIZER_ENV) or "off").strip().lower()
    return raw if raw in MODES else "off"


def _service_size(prompt: str, system: Optional[str], files: Optional[list[dict]],
                  payload_bytes: Optional[int], task_family: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    url = (os.environ.get(SIZER_URL_ENV) or DEFAULT_SIZER_URL).rstrip("/") + "/size"
    try:
        timeout_s = max(5, int(os.environ.get(SIZER_TIMEOUT_ENV) or DEFAULT_TIMEOUT_MS)) / 1000.0
    except ValueError:
        timeout_s = DEFAULT_TIMEOUT_MS / 1000.0
    body = json.dumps({
        "prompt": (prompt or "")[:_HEAD_CHARS] if "</file>" not in (prompt or "") else (prompt or "").rsplit("</file>", 1)[1][:_HEAD_CHARS],
        "system": (system or "")[:800],
        "files": files or [],
        "payload_bytes": payload_bytes,
        "task_family": task_family,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"
    if not isinstance(answer, dict) or answer.get("output_class") not in ("xs", "s", "m", "l", "xl"):
        return None, "malformed answer"
    return answer, None


def size_request(prompt: str, *, system: Optional[str] = None, files: Optional[list[dict]] = None,
                 payload_bytes: Optional[int] = None, task_family: Optional[str] = None,
                 mode: Optional[str] = None) -> Optional[dict]:
    """The sizer's answer for this request, or None when the gate is off.

    Never raises: the door must not fail a call because its advisor did.
    """
    mode = mode or sizer_mode()
    if mode == "off":
        return None
    t0 = time.perf_counter()
    if mode == "npu":
        answer, fault = _service_size(prompt, system, files, payload_bytes, task_family)
        if answer is not None:
            answer.setdefault("source", "npu")
            answer["ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return answer
        fallback = size_heuristic(prompt, system=system, files=files, payload_bytes=payload_bytes, task_family=task_family)
        fallback["fallback_from"] = "npu"
        fallback["fallback_reason"] = fault
        fallback["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return fallback
    try:
        return size_heuristic(prompt, system=system, files=files, payload_bytes=payload_bytes, task_family=task_family)
    except Exception as exc:  # noqa: BLE001 -- an advisor fault is a signal, not a failure
        return {"output_class": "s", "expected_output_tokens": 384, "task_family": task_family,
                "declared_family": task_family, "confidence": 0.0, "source": "heuristic",
                "signals": [f"sizer-fault:{type(exc).__name__}"], "ms": round((time.perf_counter() - t0) * 1000, 2)}


def sizer_label(answer: Optional[dict]) -> str:
    """The routed_by segment for a consulted sizer: ``sizer:<source>:<bin>:``; empty when not consulted."""
    if not answer:
        return ""
    return f"sizer:{answer.get('source', 'heuristic')}:{answer.get('output_class', '?')}:"


__all__ = ["SIZER_ENV", "SIZER_URL_ENV", "SIZER_TIMEOUT_ENV", "DEFAULT_SIZER_URL", "DEFAULT_TIMEOUT_MS",
           "MODES", "sizer_mode", "size_request", "sizer_label"]
