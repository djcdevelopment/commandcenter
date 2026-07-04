"""HEARTH tool: patrol — one round of the guard dog's coherence watch.

Slice 0 of Watchfire/Flare (WATCHFIRE-FLARE-DESIGN-2026-07-04.html): a
deterministic, rules-only gap detector — the guard dog's eyes, callable as a
HEARTH tool. One patrol = "make the rounds now — anything out of place?" It
gathers recent run records from the conductor over SSH (the same hop task_lane
uses) and casts the coherence spells in hearth.health.gaps. The scheduled
watchdog runs a patrol on every 15-minute tick.

No NPU and no learning yet — that is the earned upgrade once these rules have
produced labeled reps. patrol *finds and names* a gap; it does not fix it (the
auto-heal-vs-flag-only line is a separate, deliberate decision — see remediate).
"""
from __future__ import annotations

import base64
import json
from typing import Callable, Optional

from hearth.health.gaps import gaps_as_dicts, scan_runs, summarize
from hearth.toolsurface.task_lane import CONDUCTOR_REPO, _run_ssh

# Runs on the conductor's python3; emits a compact JSON summary of run dirs that
# carry a nodes.json (i.e. were dispatched), newest first, bounded to 60. The
# 60-cap is explicit, not silent: `scanned` reports the true total so a truncated
# sweep is visible.
_GATHER_SRC = r'''
import json, os, time
now = time.time()
runs = "runs"
records = []
try:
    names = os.listdir(runs)
except FileNotFoundError:
    names = []
for name in names:
    d = os.path.join(runs, name)
    nodes = os.path.join(d, "nodes.json")
    if not os.path.isfile(nodes):
        continue
    res = os.path.join(d, "result.json")
    rec = {"plan_id": name, "age_s": round(now - os.path.getmtime(nodes)),
           "has_result": os.path.isfile(res)}
    if rec["has_result"]:
        try:
            r = json.load(open(res))
            rec["status"] = r.get("status")
            rec["error"] = (r.get("error") or "")[:200]
            rec["stub"] = bool(r.get("_stub"))
            rec["winner"] = r.get("winner")
            rec["promoted"] = bool((r.get("promotion") or {}).get("promoted"))
            qs = r.get("questions") or []
            rec["n_questions"] = len(qs)
            rec["questions_text"] = " ".join(q.get("question", "") for q in qs)[:600]
            sb = (r.get("assay") or {}).get("scoreboard") or []
            win = next((s for s in sb if s.get("worker") == r.get("winner")), None)
            if win:
                rec["winner_files"] = win.get("file_count")
                rec["winner_grade"] = win.get("grade")
        except Exception as e:
            rec["parse_error"] = str(e)[:120]
    records.append(rec)
records.sort(key=lambda x: x["age_s"])
print(json.dumps({"records": records[:60], "scanned": len(records)}))
'''


def _gather_runs(runner: Optional[Callable] = None):
    """Gather run records from the conductor. Returns (payload, error)."""
    b64 = base64.b64encode(_GATHER_SRC.encode("utf-8")).decode("ascii")
    remote = f"cd {CONDUCTOR_REPO} && echo {b64} | base64 -d | python3 -"
    stdout, error = _run_ssh(remote, runner=runner)
    if error is not None:
        return None, error
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"non-JSON gather output: {exc}"
    return payload, None


def patrol() -> dict:
    """Make one round of the coherence watch: scan the fleet's runs, flag gaps.

    Returns ``{ok, scanned, considered, gaps:[{kind,severity,plan_id,detail}],
    summary}``. A gap is a place two sources disagree — a run that reads
    in-flight but is stalled, a pass grade over an empty deliverable, a builder
    reporting missing files. Finds and names the gap; does not fix it.
    """
    payload, error = _gather_runs()
    if error is not None:
        return {"ok": False, "error": error}
    records = payload.get("records", [])
    gaps = scan_runs(records)
    return {
        "ok": True,
        "scanned": payload.get("scanned", len(records)),
        "considered": len(records),
        "gaps": gaps_as_dicts(gaps),
        "summary": summarize(gaps),
    }


def get_tools() -> "list[Callable]":
    return [patrol]
