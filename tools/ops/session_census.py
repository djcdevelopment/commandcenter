#!/usr/bin/env python3
"""session-census: a denominator of frontier sessions that is independent of HEARTH.

Counts top-level frontier-agent sessions per day straight from the agents' own transcripts
(Claude Code ``~/.claude/projects/<projdir>/<uuid>.jsonl``, Codex
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``), applies the eligibility rule, and joins
the eligible Claude sessions to two independent records of adoption:

  * HEARTH  - the session's ``cc-<first 8 of sessionId>`` task id is a key of
              knowledge/offload.json ``by_task`` or a ``task_id`` string in the execution
              event log (read-only).
  * amux lane - ``cc_conversation_id[:8] == sessionId[:8]`` in an amux session meta file.

Output: knowledge/session-coverage.json (schema ``session-coverage.v1``) and a markdown
table on stdout. Shares are always reported with their denominators; two shares are never
combined (they are not independent and the second is not conditional on the first).

Gemini transcripts are not observable on this host and are reported as ``unobservable``.

Usage:
  python3 tools/ops/session_census.py [--days 14] [--today YYYY-MM-DD] [--out PATH] [--no-write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

SCHEMA = "session-coverage.v1"
MIN_PROMPTS = 3
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
CODEX_DAY_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})/[^/]*$")
# Codex injects its own context as role=user messages; these are not operator prompts.
CODEX_INJECTED_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context",
    "<user_instructions",
    "<INSTRUCTIONS",
    "<turn_aborted",
    "<user_shell_command",
    "<permissions instructions",
)
HEADER_SCAN_LINES = 40

REPO = Path(__file__).resolve().parents[2]
HOME = Path.home()
DEFAULTS = {
    "claude_dir": HOME / ".claude" / "projects",
    "codex_dir": HOME / ".codex" / "sessions",
    "offload": REPO / "knowledge" / "offload.json",
    "events": HOME / "hearth-production" / "var" / "execution" / "events.ndjson",
    "amux_sessions": HOME / "work" / "amux" / "scratch" / "amux_home" / "sessions",
    "rd_ids": REPO / "knowledge" / "rd-task-ids.json",
    "out": REPO / "knowledge" / "session-coverage.json",
}


# --------------------------------------------------------------------------- helpers
def _utc_day(ts: str | float | None) -> str | None:
    """YYYY-MM-DD (UTC) from an ISO-8601 string or an epoch; None when unparseable."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _iter_json_lines(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def task_id_for(session_id: str | None) -> str | None:
    """The HEARTH task id a Claude Code session stamps: ``cc-`` + first 8 of the session id."""
    if not session_id:
        return None
    return f"cc-{session_id[:8]}"


# --------------------------------------------------------------------------- Claude
def _is_real_claude_prompt(rec: dict) -> bool:
    """A user line that carries operator text: not a tool_result echo, not a meta line."""
    msg = rec.get("message")
    role = msg.get("role") if isinstance(msg, dict) else None
    if rec.get("type") != "user" and role != "user":
        return False
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        if kinds and kinds <= {"tool_result"}:
            return False
    return True


def scan_claude_session(path: Path) -> dict:
    """One transcript -> {session_id, day, prompts}. Day: first timestamp, else mtime."""
    session_id = None
    first_ts = None
    prompts = 0
    for i, rec in enumerate(_iter_json_lines(path)):
        if not isinstance(rec, dict):
            continue
        if first_ts is None and i < HEADER_SCAN_LINES and rec.get("timestamp"):
            first_ts = rec["timestamp"]
        if session_id is None and rec.get("sessionId"):
            session_id = str(rec["sessionId"])
        if _is_real_claude_prompt(rec):
            prompts += 1
    day = _utc_day(first_ts)
    day_source = "first_line_timestamp"
    if day is None:
        try:
            day = _utc_day(path.stat().st_mtime)
        except OSError:
            day = None
        day_source = "mtime"
    return {
        "session_id": session_id or path.stem,
        "day": day,
        "day_source": day_source,
        "prompts": prompts,
        "path": str(path),
    }


def find_claude_sessions(root: Path):
    """Top-level session files only: UUID-named, never under a ``subagents`` directory."""
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*.jsonl")):
        rel_parts = p.relative_to(root).parts
        if "subagents" in rel_parts[:-1]:
            continue
        if not UUID_RE.match(p.stem):
            continue
        yield p


# --------------------------------------------------------------------------- Codex
def _codex_user_text(payload: dict) -> str:
    parts = payload.get("content")
    if isinstance(parts, list):
        return "".join(b.get("text", "") for b in parts if isinstance(b, dict))
    return parts if isinstance(parts, str) else ""


def scan_codex_rollout(path: Path, root: Path) -> dict:
    """One rollout -> {id, day, prompts}. prompts is None when the format shows no user turns."""
    prompts = 0
    saw_message_format = False
    for rec in _iter_json_lines(path):
        if not isinstance(rec, dict):
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        if rec.get("type") == "response_item" and payload.get("type") == "message":
            saw_message_format = True
            if payload.get("role") == "user":
                text = _codex_user_text(payload).lstrip()
                if text and not text.startswith(CODEX_INJECTED_PREFIXES):
                    prompts += 1
    day = None
    try:
        m = CODEX_DAY_RE.search(path.relative_to(root).as_posix())
    except ValueError:
        m = CODEX_DAY_RE.search(path.as_posix())
    if m:
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return {
        "id": path.name,
        "day": day,
        "prompts": prompts if saw_message_format else None,
        "path": str(path),
    }


def find_codex_rollouts(root: Path):
    if not root.is_dir():
        return
    yield from sorted(root.rglob("rollout-*.jsonl"))


# --------------------------------------------------------------------------- joins
def load_rd_ids(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if isinstance(data, dict):
        data = data.get("task_ids", [])
    return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()


def load_offload_task_ids(path: Path) -> tuple[set[str], dict]:
    """by_task keys from knowledge/offload.json (a list of {key, ...} rows, or a mapping)."""
    meta = {"by_task_truncated": None, "by_task_omitted": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), meta
    by_task = data.get("by_task", []) if isinstance(data, dict) else []
    meta["by_task_truncated"] = data.get("by_task_truncated") if isinstance(data, dict) else None
    meta["by_task_omitted"] = data.get("by_task_omitted") if isinstance(data, dict) else None
    if isinstance(by_task, dict):
        keys = by_task.keys()
    else:
        keys = (r.get("key") for r in by_task if isinstance(r, dict))
    return {k for k in keys if isinstance(k, str)}, meta


def _walk_task_ids(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "task_id" and isinstance(v, str):
                out.add(v)
            elif isinstance(v, (dict, list)):
                _walk_task_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_task_ids(v, out)


def load_event_task_ids(path: Path) -> set[str]:
    """Every string ``task_id`` anywhere in an execution event (read-only, line by line)."""
    out: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"task_id"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                _walk_task_ids(rec, out)
    except OSError:
        pass
    return out


def load_lane_prefixes(sessions_dir: Path) -> set[str]:
    """First 8 chars of ``cc_conversation_id`` from amux ``*.meta.json``."""
    out: set[str] = set()
    if not sessions_dir.is_dir():
        return out
    for p in sorted(sessions_dir.glob("*.meta.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cid = meta.get("cc_conversation_id") if isinstance(meta, dict) else None
        if isinstance(cid, str) and len(cid) >= 8:
            out.add(cid[:8])
    return out


# --------------------------------------------------------------------------- census
def _share(count: int, denom: int) -> dict:
    return {"count": count, "of_eligible": denom, "share": (round(count / denom, 4) if denom else None)}


def build_census(
    *,
    claude_dir: Path,
    codex_dir: Path,
    offload: Path,
    events: Path,
    amux_sessions: Path,
    rd_ids: Path,
    today: dt.date,
    days: int = 14,
) -> dict:
    window = [(today - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    wset = set(window)
    excluded_ids = load_rd_ids(rd_ids)
    offload_ids, offload_meta = load_offload_task_ids(offload)
    event_ids = load_event_task_ids(events)
    hearth_ids = offload_ids | event_ids
    lane_prefixes = load_lane_prefixes(amux_sessions)

    rows = {
        d: {
            "day": d,
            "claude_top_level": 0,
            "claude_excluded_rd": 0,
            "claude_eligible": 0,
            "codex_rollouts": 0,
            "codex_eligible": 0,
            "codex_prompts_unknown": 0,
            "used_hearth": 0,
            "in_lane": 0,
        }
        for d in window
    }

    for p in find_claude_sessions(claude_dir):
        s = scan_claude_session(p)
        if s["day"] not in wset:
            continue
        r = rows[s["day"]]
        r["claude_top_level"] += 1
        if s["prompts"] < MIN_PROMPTS:
            continue
        tid = task_id_for(s["session_id"])
        if tid in excluded_ids:
            r["claude_excluded_rd"] += 1
            continue
        r["claude_eligible"] += 1
        if tid in hearth_ids:
            r["used_hearth"] += 1
        if s["session_id"][:8] in lane_prefixes:
            r["in_lane"] += 1

    for p in find_codex_rollouts(codex_dir):
        s = scan_codex_rollout(p, codex_dir)
        if s["day"] not in wset:
            continue
        r = rows[s["day"]]
        r["codex_rollouts"] += 1
        if s["prompts"] is None:
            r["codex_prompts_unknown"] += 1
        elif s["prompts"] >= MIN_PROMPTS:
            r["codex_eligible"] += 1

    per_day = []
    for d in window:
        r = rows[d]
        codex_unknown = r["codex_prompts_unknown"] > 0
        per_day.append(
            {
                "day": d,
                "claude_top_level": r["claude_top_level"],
                "claude_excluded_rd": r["claude_excluded_rd"],
                "claude_eligible": r["claude_eligible"],
                "codex_rollouts": r["codex_rollouts"],
                "codex_eligible": None if codex_unknown else r["codex_eligible"],
                "codex_prompts_unknown": r["codex_prompts_unknown"],
                "used_hearth": _share(r["used_hearth"], r["claude_eligible"]),
                "in_lane": _share(r["in_lane"], r["claude_eligible"]),
            }
        )

    tot = {k: sum(rows[d][k] for d in window) for k in rows[window[0]] if k != "day"}
    totals = {
        "claude_top_level": tot["claude_top_level"],
        "claude_excluded_rd": tot["claude_excluded_rd"],
        "claude_eligible": tot["claude_eligible"],
        "codex_rollouts": tot["codex_rollouts"],
        # known rollouts only; a lower bound while codex_prompts_unknown > 0
        "codex_eligible": tot["codex_eligible"],
        "codex_prompts_unknown": tot["codex_prompts_unknown"],
        "used_hearth": _share(tot["used_hearth"], tot["claude_eligible"]),
        "in_lane": _share(tot["in_lane"], tot["claude_eligible"]),
    }

    method = (
        "Denominator = top-level frontier sessions from the agents' own transcripts, independent of HEARTH. "
        "Claude: ~/.claude/projects/<projdir>/<uuid>.jsonl, UUID-named files only, nothing under a subagents/ "
        "directory; day = UTC date of the first line carrying a timestamp (mtime if none); session id = the "
        "sessionId field (file stem if absent); prompts = user lines that are not tool_result echoes and not "
        "isMeta/isSidechain. Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, day = directory path, "
        "prompts = role=user response_item messages excluding injected context (AGENTS.md, environment, "
        "instructions); null when the rollout shows no message items. "
        f"Eligible = >= {MIN_PROMPTS} prompts and cc-<first 8 of sessionId> not in knowledge/rd-task-ids.json. "
        "used_hearth (Claude only): the session's cc- id is a key of knowledge/offload.json by_task or a string "
        "task_id in the execution events.ndjson (read-only). in_lane (Claude only): an amux "
        "sessions/*.meta.json cc_conversation_id[:8] equals sessionId[:8]. Both are shares of claude_eligible "
        "and are not independent: do not multiply them. Codex sessions are not joined (no cc- id; the "
        "cx-<lane> stamp starts with I1.2). Windows are UTC days, last "
        f"{days} including today."
    )
    caveats = []
    if offload_meta["by_task_truncated"]:
        caveats.append(
            f"offload.json by_task is truncated ({offload_meta['by_task_omitted']} task ids omitted); "
            "the events.ndjson join covers the gap, but used_hearth can still undercount."
        )
    if not claude_dir.is_dir():
        caveats.append(f"claude transcripts directory missing: {claude_dir}")
    if not events.is_file():
        caveats.append(f"execution events missing: {events}; used_hearth rests on offload.json alone")

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": window[0], "end": window[-1], "days": days, "tz": "UTC"},
        "unobservable": ["gemini"],
        "per_day": per_day,
        "totals": totals,
        "method": method,
        "caveats": caveats,
        "sources": {
            "hearth_task_ids_offload": len(offload_ids),
            "hearth_task_ids_events": len(event_ids),
            "lane_prefixes": len(lane_prefixes),
            "excluded_rd_ids": sorted(excluded_ids),
        },
    }


def _fmt_share(s: dict) -> str:
    if s["share"] is None:
        return f"{s['count']}/{s['of_eligible']} (n/a)"
    return f"{s['count']}/{s['of_eligible']} ({s['share'] * 100:.0f}%)"


def render_markdown(c: dict) -> str:
    lines = [
        f"Session census {c['window']['start']}..{c['window']['end']} (UTC)",
        "",
        "| day | claude top-level | claude eligible | codex rollouts | codex eligible | used HEARTH (of eligible) | in lane (of eligible) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in c["per_day"] + [dict(c["totals"], day="total")]:
        ce = "unknown" if r["codex_eligible"] is None else r["codex_eligible"]
        if r["day"] == "total" and r["codex_prompts_unknown"]:
            ce = f"{ce} (+{r['codex_prompts_unknown']} unknown)"
        lines.append(
            f"| {r['day']} | {r['claude_top_level']} | {r['claude_eligible']} | {r['codex_rollouts']} | {ce} "
            f"| {_fmt_share(r['used_hearth'])} | {_fmt_share(r['in_lane'])} |"
        )
    lines += ["", "unobservable: " + ", ".join(c["unobservable"])]
    lines += [f"caveat: {x}" for x in c["caveats"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--today", help="YYYY-MM-DD (UTC); default: today")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), type=Path, default=v)
    ap.add_argument("--no-write", action="store_true", help="print the table only")
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(dt.timezone.utc).date()
    census = build_census(
        claude_dir=a.claude_dir,
        codex_dir=a.codex_dir,
        offload=a.offload,
        events=a.events,
        amux_sessions=a.amux_sessions,
        rd_ids=a.rd_ids,
        today=today,
        days=a.days,
    )
    if not a.no_write:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")
    print(render_markdown(census))
    return 0


if __name__ == "__main__":
    sys.exit(main())
