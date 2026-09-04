"""Rung health as ADR-0044 defines it: baseline epoch + observed rate + acceptance envelope.

Pure and passive. Reads two files (paths injectable) and never touches the
rung itself:

- ``campaign/ff-probes/rate-baselines.json`` (contract ``ff-rate-baselines.v1``)
- ``hearth/var/arc-keepalive.jsonl`` (one JSON row per line; only the tail is read)

A 1-token keep-alive ping proves liveness, not health: only a DEEP row
(``predicted_n >= 8`` carrying ``decode_tok_s``) can yield ``at_rate``. The
envelope is a fraction of THIS baseline epoch, not of machine capacity, and this
module deliberately names no regime and applies no restart discriminator.

Must not import ``hearth.kernel``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINES = _ROOT / "campaign" / "ff-probes" / "rate-baselines.json"
DEFAULT_KEEPALIVE = _ROOT / "hearth" / "var" / "arc-keepalive.jsonl"

NOTE = ("envelope is of THIS baseline epoch, not of capacity (ADR-0044); "
        "restart discriminator not applied; no regime names")

DEEP_MIN_PREDICTED = 8


def load_baseline(path=DEFAULT_BASELINES, rung: str = "omen-arc"):
    """Return {tok_s, epoch, fail_below, warn_below, note} or None if absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    entry = (doc.get("rungs") or {}).get(rung)
    if not isinstance(entry, dict):
        return None
    tok_s = entry.get("baseline_decode_tok_s")
    if not isinstance(tok_s, (int, float)) or tok_s <= 0:
        return None
    env = entry.get("acceptance_envelope") or {}
    return {
        "tok_s": float(tok_s),
        "epoch": str(entry.get("baseline_epoch") or entry.get("baseline_set") or ""),
        "fail_below": float(env.get("fail_below_frac", 0.8)),
        "warn_below": float(env.get("warn_below_frac", 0.9)),
        "note": NOTE,
    }


def read_keepalive(path=DEFAULT_KEEPALIVE, tail_bytes: int = 262144) -> list:
    """Parse the last ``tail_bytes`` of the keep-alive log; skip malformed lines and a BOM."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
            raw = fh.read()
    except OSError:
        return []
    return parse_keepalive_bytes(raw, truncated_head=size > tail_bytes)


def parse_keepalive_bytes(raw: bytes, truncated_head: bool = False) -> list:
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    lines = text.splitlines()
    if truncated_head and lines:
        lines = lines[1:]  # first line is almost certainly a fragment
    rows = []
    for line in lines:
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "ts" in obj:
            rows.append(obj)
    return rows


def _ts_epoch(ts) -> float | None:
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim sub-microsecond digits (PowerShell emits 7): keep at most 6.
    if "." in s:
        head, _, rest = s.partition(".")
        i = 0
        while i < len(rest) and rest[i].isdigit():
            i += 1
        s = head + "." + rest[:min(i, 6)] + rest[i:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_deep(row: dict) -> bool:
    n = row.get("predicted_n")
    return (isinstance(n, (int, float)) and n >= DEEP_MIN_PREDICTED
            and isinstance(row.get("decode_tok_s"), (int, float)))


def rung_state(rows, baseline, now: float, *, port: int = 8082,
               stale_after_s: float = 720.0, windows=(), rung: str = "omen-arc") -> dict:
    """Passive verdict over parsed keep-alive rows against a baseline dict."""
    state = {
        "rung": rung, "port": port, "verdict": "unknown",
        "baseline_tok_s": None, "baseline_epoch": None,
        "envelope": {"fail_below": None, "warn_below": None},
        "observed_tok_s": None, "observed_at": None, "observed_age_s": None,
        "frac_of_baseline": None, "prefill_stall_recent": False,
        "last_ping_ok": None, "deep_samples": 0, "excluded_windows": [],
        "note": NOTE,
    }
    if baseline:
        state["baseline_tok_s"] = baseline["tok_s"]
        state["baseline_epoch"] = baseline["epoch"]
        state["envelope"] = {"fail_below": baseline["fail_below"],
                             "warn_below": baseline["warn_below"]}

    # Keep rows for this port, timestamped, sorted by time; drop windowed rows.
    excluded = []
    kept = []
    for row in rows or ():
        if row.get("port") != port:
            continue
        t = _ts_epoch(row.get("ts"))
        if t is None:
            continue
        hit = None
        for start, end, name in windows or ():
            if start <= t <= end:
                hit = name
                break
        if hit is not None:
            if hit not in excluded:
                excluded.append(hit)
            continue
        kept.append((t, row))
    kept.sort(key=lambda p: p[0])
    state["excluded_windows"] = excluded

    deep = [(t, r) for t, r in kept if _is_deep(r)]
    state["deep_samples"] = len(deep)
    if kept:
        newest = kept[-1][1]
        state["last_ping_ok"] = bool(newest.get("ok"))
        state["prefill_stall_recent"] = any(bool(r.get("prefill_stall")) for _, r in kept[-3:])
    if deep:
        t, r = deep[-1]
        state["observed_tok_s"] = float(r["decode_tok_s"])
        state["observed_at"] = r.get("ts")
        state["observed_age_s"] = round(max(0.0, now - t), 1)
        if baseline:
            state["frac_of_baseline"] = round(state["observed_tok_s"] / baseline["tok_s"], 4)

    if not baseline:
        state["verdict"] = "no_baseline"
    elif not kept:
        state["verdict"] = "unreachable"
    elif not state["last_ping_ok"]:
        state["verdict"] = "unreachable"
    elif bool(kept[-1][1].get("prefill_stall")):
        state["verdict"] = "stalled"
    elif not deep or (now - deep[-1][0]) > stale_after_s:
        state["verdict"] = "stale"
    else:
        frac = state["frac_of_baseline"]
        if frac < baseline["fail_below"]:
            state["verdict"] = "degraded"
        elif frac < baseline["warn_below"]:
            state["verdict"] = "warn"
        else:
            state["verdict"] = "at_rate"
    return state


def live_rung_state(rung: str = "omen-arc", root=None, now=None) -> dict:
    """Convenience over the two default files. Never raises."""
    try:
        base_dir = Path(root) if root else _ROOT
        bpath = base_dir / "campaign" / "ff-probes" / "rate-baselines.json"
        kpath = base_dir / "hearth" / "var" / "arc-keepalive.jsonl"
        baseline = load_baseline(bpath, rung)
        rows = read_keepalive(kpath)
        # The ledgered rotation windows (ADR-0044 exclusion spans). 2026-09-03: this reader was
        # documented to exclude them and never passed them, so a proof's own probes read as
        # production's regime (71-74 tok/s during the pour). Unreadable -> no exclusion, never raise.
        try:
            from hearth.rotation.windows import read_windows
            windows = read_windows(base_dir / "hearth" / "var" / "rotation-windows.jsonl")
        except Exception:  # noqa: BLE001
            windows = ()
        port = 8082
        try:
            with open(bpath, "r", encoding="utf-8-sig") as fh:
                port = int(((json.load(fh).get("rungs") or {}).get(rung) or {}).get("port", 8082))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if now is None:
            now = datetime.now(timezone.utc).timestamp()
        return rung_state(rows, baseline, float(now), port=port, rung=rung, windows=windows)
    except Exception as exc:  # noqa: BLE001 - passive reader must never raise
        return {"rung": rung, "port": None, "verdict": "unknown", "error": f"{type(exc).__name__}: {exc}",
                "note": NOTE}


def summarize_for_notes(state: dict) -> str:
    """One line, <= 96 chars, no ';' (safe for the notes column)."""
    v = str(state.get("verdict", "unknown"))
    obs = state.get("observed_tok_s")
    base = state.get("baseline_tok_s")
    frac = state.get("frac_of_baseline")
    parts = [f"{state.get('rung', '?')} {v}"]
    if obs is not None and base is not None:
        parts.append(f"{obs:.1f}/{base:.1f} tok/s")
    if frac is not None:
        parts.append(f"{frac:.0%} of epoch")
    age = state.get("observed_age_s")
    if age is not None:
        parts.append(f"age {int(age)}s")
    if state.get("excluded_windows"):
        parts.append("excl " + ",".join(str(w) for w in state["excluded_windows"]))
    s = " ".join(parts).replace(";", ",")
    return s[:96]
