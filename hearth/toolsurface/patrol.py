"""HEARTH tool: patrol — one round of the guard dog's coherence watch.

Slice 0 of Watchfire/Flare (WATCHFIRE-FLARE-DESIGN-2026-07-04.html): a
deterministic, rules-only gap detector — the guard dog's eyes, callable as a
HEARTH tool. One patrol = "make the rounds now — anything out of place?" It
gathers recent run records from the conductor over SSH (the same hop task_lane
uses) and casts the coherence spells in hearth.health.gaps. The scheduled
watchdog runs a patrol on every 15-minute tick.

No NPU and no learning yet — that is the earned upgrade once these rules have
produced labeled reps. patrol *finds and names* a gap; it does not fix it (the
auto-heal-vs-flag-only line is a separate, deliberate decision — see masters_pet).
"""
from __future__ import annotations

import base64
import json
from typing import Callable, Optional

from hearth.health.gaps import (gaps_as_dicts, load_capacity_document, scan_knowledge,
                                scan_runs, summarize)
from hearth.toolsurface.task_lane import CONDUCTOR_REPO, _run_ssh

# Lazy-imported refresh callees; imported at function-call time in patrol(),
# never at module load, so tests can mock them. This avoids circular dependencies
# and makes missing modules at test time non-fatal.
_project_capacity_knowledge = None
_gather_am4_catalog = None
_schedule_hindsight = None

def _ensure_refresh_imports():
    """Lazy-load refresh dependencies once, on first use."""
    global _project_capacity_knowledge, _gather_am4_catalog, _schedule_hindsight
    if _project_capacity_knowledge is None:
        from hearth.toolsurface.knowledge import project_capacity_knowledge
        _project_capacity_knowledge = project_capacity_knowledge
    if _gather_am4_catalog is None:
        from hearth.toolsurface.am4 import gather_am4_catalog
        _gather_am4_catalog = gather_am4_catalog
    if _schedule_hindsight is None:
        from hearth.toolsurface.scheduler import schedule_hindsight
        _schedule_hindsight = schedule_hindsight

DEFAULT_CAPACITY_PATH = "knowledge/capacity.json"

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


def patrol(capacity_path: str = DEFAULT_CAPACITY_PATH, refresh: bool = True) -> dict:
    """Make one round of the coherence watch: scan the fleet's runs, flag gaps.

    Returns ``{ok, scanned, considered, gaps:[{kind,severity,plan_id,detail}],
    summary, refresh: {...}}``. A gap is a place two sources disagree — a run that reads
    in-flight but is stalled, a pass grade over an empty deliverable, a builder
    reporting missing files, or a run taking far longer than capacity predicts.
    Finds and names the gap; does not fix it.

    When refresh=True, after the gap scan, attempts to refresh three knowledge sources:
    (1) project_capacity_knowledge(), (2) gather_am4_catalog(write=True), (3)
    schedule_hindsight(limit=20). Each is wrapped in try/except so a failure in
    one does not break patrol. Refresh outcomes are recorded under the "refresh" key.

    ``capacity_path`` resolves in the HEARTH sandbox; when absent, schedule_divergence
    fires nothing — cheap, default-on, and silent when there's no capacity data yet.
    """
    payload, error = _gather_runs()
    if error is not None:
        return {"ok": False, "error": error}
    records = payload.get("records", [])
    capacity = load_capacity_document(capacity_path)
    gaps = scan_runs(records, capacity=capacity) + scan_knowledge(capacity_path)
    result = {
        "ok": True,
        "scanned": payload.get("scanned", len(records)),
        "considered": len(records),
        "gaps": gaps_as_dicts(gaps),
        "summary": summarize(gaps),
    }

    # Optional refresh: best-effort update of three knowledge sources.
    if refresh:
        _ensure_refresh_imports()
        refresh_results = {}

        # (a) Refresh capacity knowledge
        try:
            cap_result = _project_capacity_knowledge()
            refresh_results["capacity"] = {"ok": True, **cap_result}
        except Exception as exc:
            refresh_results["capacity"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # (b) Refresh AM4 catalog
        try:
            am4_result = _gather_am4_catalog(write=True)
            refresh_results["am4_catalog"] = {"ok": True, "model_count": len(am4_result.get("models", {}))}
        except Exception as exc:
            refresh_results["am4_catalog"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # (c) Refresh hindsight regret (limit 20 runs)
        try:
            hindsight_result = _schedule_hindsight(limit=20)
            if hindsight_result.get("ok"):
                report = hindsight_result.get("report", {})
                refresh_results["hindsight"] = {
                    "ok": True,
                    "regret": {
                        "n_runs": report.get("n_runs"),
                        **(report.get("regret") or {}),
                    }
                }
            else:
                refresh_results["hindsight"] = {"ok": False, "error": hindsight_result.get("error", "unknown error")}
        except Exception as exc:
            refresh_results["hindsight"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        result["refresh"] = refresh_results

    return result


def get_tools() -> "list[Callable]":
    return [patrol]
