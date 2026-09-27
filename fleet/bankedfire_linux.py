#!/usr/bin/env python3
"""bankedfire_linux — one Banked Fire tick on the Linux production host, through the door.

`fleet.bankedfire_drain.run_tick` already does the whole ADR-0006 dance (arm state, one
slot, idle questions, budget, brief selection, persist-first dispatch, write-back, one
ledger row per tick). What it dispatches INTO is injectable: on Windows that was the
cc-conductor inbox over SSH. This module supplies the Linux lane instead:

  * queue_status_fn  -> local-work manifests still queued/running (read-only files)
  * submit_task_fn   -> HEARTH `submit_local_work` as the `bankedfire-drain` caller
  * task_status_fn   -> HEARTH `get_local_work`, mapped to the drain's status shape

so an authored brief becomes an immutable candidate at `awaiting_review` (ADR-0048); the
morning verdict is a human's or a frontier caller's, never this loop's. Occupancy is the
`omen-vllm` probe (seat gauges + HEARTH jobs + operator presence) via BANKEDFIRE_BACKEND.

Brief body for the `local-work` task class = a small front block, then `---`, then the
intent prose:

    repo: /home/derek/work/continuity
    commit: HEAD                      # or a 40-hex sha
    lane: auto                        # auto | fast | deep
    artifact_kind: unified_diff       # unified_diff | whole_file | markdown | json
    target_path: ct/new_module.py     # whole_file only
    paths: [ct/ct.py, tests/test_ct.py]
    task_family: code_fix
    criteria:
      - first acceptance criterion
      - second
    ---
    <intent>

Run:
    python -m fleet.bankedfire_linux            # one tick (ledgered)
    python -m fleet.bankedfire_linux --json
    python -m fleet.bankedfire_linux --dry-run  # gates + next brief, no dispatch, no ledger
Arm/disarm with the drain's own CLI (same arm file under $HEARTH_ROOT):
    python -m fleet.bankedfire_drain --arm "<reason>" --scope authored --authored-by derek
"""
from __future__ import annotations

import argparse
from datetime import datetime
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet import bankedfire_drain as drain  # noqa: E402
from hearth.backlog import select as backlog_select  # noqa: E402
from hearth.backlog import sources as backlog_sources  # noqa: E402
from hearth.toolsurface import occupancy as occ_mod  # noqa: E402

DOOR_URL = os.environ.get("HEARTH_DOOR_URL", "http://127.0.0.1:8710/mcp")
KEY_ENV = "HEARTH_DRAIN_KEY"
LOCAL_WORK_TASK_CLASS = "local-work"
EXPERIMENT_TASK_CLASS = "experiment"
DEEPAGENTS_TASK_CLASS = "deepagents"
DRAIN_CALLER_ID = "bankedfire-drain"


def lane_slots() -> dict[str, int]:
    """Per-lane in-flight caps for unattended dispatch (BANKEDFIRE_SLOTS="fast=3,deep=1,experiment=1").
    Deliberately below backends' parallel_slots: the execution plane admits per provider, but
    a job waiting for a slot spins on a worker, and unattended work must leave room for a human."""
    raw = os.environ.get("BANKEDFIRE_SLOTS", "fast=3,deep=1,experiment=1,deepagents=1")
    out: dict[str, int] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = max(0, int(v))
            except ValueError:
                pass
    return out


def slots_path() -> Path:
    return Path(os.environ.get("HEARTH_ROOT", str(Path.home() / "hearth-production"))) / "var" / "bankedfire_linux_slots.json"


def load_slots() -> list[dict[str, Any]]:
    try:
        data = json.loads(slots_path().read_text())
        return list(data.get("slots", [])) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001
        return []


def save_slots(slots: list[dict[str, Any]]) -> None:
    p = slots_path(); tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": "bankedfire-linux-slots.v1", "slots": slots}, indent=2, default=str))
    os.replace(tmp, p)


def brief_lane(brief) -> str:
    """Which cap a brief counts against before it is dispatched."""
    if brief.task_class == EXPERIMENT_TASK_CLASS:
        return "experiment"
    if brief.task_class == DEEPAGENTS_TASK_CLASS:
        return "deepagents"
    try:
        fields, _ = parse_local_work_block(brief.body)
    except ValueError:
        return "deep"
    lane = str(fields.get("lane", "auto"))
    return lane if lane in ("fast", "deep") else "deep"   # auto counts against the scarcer lane
FINAL = {"failed", "rejected", "accepted", "superseded"}
DONE = FINAL | {"awaiting_review"}
_LIST_RE = re.compile(r"^\[(.*)\]$")


# --- the door, synchronously ---------------------------------------------------------------

def call_tool(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """One MCP tool call as the drain caller. Raises on transport or tool error."""
    key = os.environ.get(KEY_ENV)
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set (see ~/.config/hearth/bankedfire.env)")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _run() -> dict[str, Any]:
        async with streamablehttp_client(DOOR_URL, headers={"X-Hearth-Key": key}, timeout=300) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool(tool, args)
                if res.isError:
                    text = " ".join(getattr(c, "text", str(c)) for c in res.content)
                    raise RuntimeError(f"{tool}: {text[:500]}")
                if getattr(res, "structuredContent", None):
                    return dict(res.structuredContent)
                text = "".join(getattr(c, "text", "") for c in res.content)
                return json.loads(text) if text.strip().startswith("{") else {"text": text}

    return asyncio.run(_run())


# --- the brief's local-work block ----------------------------------------------------------

def parse_local_work_block(body: str) -> tuple[dict[str, Any], str]:
    """(fields, intent) from a local-work brief body. Raises ValueError when malformed."""
    if "\n---" not in body and not body.startswith("---"):
        raise ValueError("local-work brief needs a front block terminated by a '---' line")
    head, _, intent = body.partition("\n---")
    if body.startswith("---"):
        head, intent = "", body[3:]
    fields: dict[str, Any] = {"criteria": []}
    current_list: Optional[str] = None
    for raw in head.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if current_list and line.lstrip().startswith("- "):
            fields[current_list].append(line.lstrip()[2:].strip())
            continue
        current_list = None
        if ":" not in line:
            raise ValueError(f"unparseable brief line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.split(" #", 1)[0].strip()
        if key == "criteria":
            current_list = "criteria"
            continue
        m = _LIST_RE.match(value)
        if m:
            fields[key] = [v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip()]
        else:
            fields[key] = value.strip("'\"")
    intent = intent.strip()
    if not intent:
        raise ValueError("local-work brief has no intent after the '---' line")
    for required in ("repo", "paths"):
        if not fields.get(required):
            raise ValueError(f"local-work brief lacks {required!r}")
    if not fields["criteria"]:
        raise ValueError("local-work brief lacks acceptance criteria")
    return fields, intent


def resolve_commit(repo: str, commit: Optional[str]) -> str:
    ref = (commit or "HEAD").strip()
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    out = subprocess.run(["git", "-C", repo, "rev-parse", ref], capture_output=True, text=True, timeout=20)
    if out.returncode:
        raise ValueError(f"cannot resolve {ref!r} in {repo}: {out.stderr.strip()}")
    return out.stdout.strip()


def submit_args_from_brief(body: str, *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    fields, intent = parse_local_work_block(body)
    args: dict[str, Any] = {
        "intent": intent,
        "acceptance_criteria": list(fields["criteria"]),
        "repo": fields["repo"],
        "base_commit": resolve_commit(fields["repo"], fields.get("commit")),
        "files": list(fields["paths"]) if isinstance(fields["paths"], list) else [fields["paths"]],
        "artifact_kind": fields.get("artifact_kind", "unified_diff"),
        "lane": fields.get("lane", "auto"),
        "task_family": fields.get("task_family", "code_fix"),
        "deadline_s": int(fields.get("deadline_s", 1200)),
    }
    if fields.get("target_path"):
        args["target_path"] = fields["target_path"]
    if fields.get("max_tokens"):
        args["max_tokens"] = int(fields["max_tokens"])
    if idempotency_key:
        args["idempotency_key"] = idempotency_key
    return args


# --- the three injected functions ----------------------------------------------------------

def manifest_path(work_id: str) -> Path:
    return _REPO_ROOT / "runs" / "operator" / work_id / "work-manifest.json"


def experiment_spec_from_brief(body: str, exp_id: str) -> dict[str, Any]:
    fields, intent = parse_local_work_block_lenient(body)
    for required in ("seat", "dropin", "campaign"):
        if not fields.get(required):
            raise ValueError(f"experiment brief lacks {required!r}")
    return {"id": exp_id, "seat": int(fields["seat"]), "dropin": fields["dropin"],
            "expect_model": fields.get("expect_model"), "campaign": fields["campaign"],
            "max_minutes": int(fields.get("max_minutes", 240)), "restore_by": fields.get("restore_by", "06:30"),
            "work": fields.get("work"), "intent": intent}


def parse_local_work_block_lenient(body: str) -> tuple[dict[str, Any], str]:
    """Same front-block grammar as local-work briefs, without local-work's required keys."""
    head, _, intent = body.partition("\n---")
    fields: dict[str, Any] = {}
    for raw in head.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.split(" #", 1)[0].strip().strip("'\"")
    return fields, intent.strip()


def submit_experiment(body: str, hint: str) -> dict[str, Any]:
    from fleet import experiment_linux
    exp_id = re.sub(r"[^A-Za-z0-9._-]", "-", hint)[:60] + "-" + datetime.now().strftime("%Y%m%dT%H%M")
    spec = experiment_spec_from_brief(body, exp_id)
    spec_dir = experiment_linux.EXP_ROOT / exp_id
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    env_args = [f"--setenv={k}={os.environ[k]}" for k in
                ("HEARTH_ROOT", "HEARTH_COORDINATION_DB", "HEARTH_EXECUTION_DIR", "PYTHONPATH", "PATH", "VLLM_API_KEY", "OMEN_ARC_TOKEN")
                if os.environ.get(k)]
    argv = ["systemd-run", "--user", "--collect", f"--unit=hearth-experiment-{exp_id}",
            f"--property=WorkingDirectory={_REPO_ROOT}", *env_args,
            sys.executable, "-m", "fleet.experiment_linux", "run", "--spec", str(spec_path)]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if out.returncode:
        return {"ok": False, "error": f"systemd-run failed: {(out.stderr or out.stdout).strip()[:300]}"}
    return {"ok": True, "plan_id": f"exp_{exp_id}", "work_id": None, "inbox_path": None,
            "result_path": str(spec_dir / "state.json"), "unit": f"hearth-experiment-{exp_id}"}


def deepagents_spec_from_brief(body: str, run_id: str) -> dict[str, Any]:
    fields, intent = parse_local_work_block_lenient(body)
    if not fields.get("source"):
        raise ValueError("deepagents brief lacks 'source' (the one file the agent may read/edit)")
    if not intent:
        raise ValueError("deepagents brief has no task after the '---' line")
    return {"id": run_id, "source": fields["source"], "task": intent,
            "backend": fields.get("backend", "omen-dense"),
            "report": str(fields.get("report", "false")).lower() in ("1", "true", "yes"),
            "max_report_words": int(fields["max_report_words"]) if fields.get("max_report_words") else None,
            "work": fields.get("work")}


def submit_deepagents(body: str, hint: str) -> dict[str, Any]:
    from fleet import deepagents_linux
    run_id = re.sub(r"[^A-Za-z0-9_-]", "-", hint)[:80] + "-" + datetime.now().strftime("%Y%m%dT%H%M")
    spec = deepagents_spec_from_brief(body, run_id)
    spec_dir = deepagents_linux.STATE_ROOT / run_id
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "spec.json"; spec_path.write_text(json.dumps(spec, indent=2))
    env_args = [f"--setenv={k}={os.environ[k]}" for k in
                ("HEARTH_ROOT", "PYTHONPATH", "PATH", "OMEN_ARC_TOKEN", "AM4_VLLM_TOKEN", "DEEPAGENTS_LINUX", "DEEPAGENTS_PYTHON")
                if os.environ.get(k)]
    argv = ["systemd-run", "--user", "--collect", f"--unit=hearth-deepagents-{run_id}",
            f"--property=WorkingDirectory={_REPO_ROOT}", *env_args,
            sys.executable, "-m", "fleet.deepagents_linux", "run", "--spec", str(spec_path)]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if out.returncode:
        return {"ok": False, "error": f"systemd-run failed: {(out.stderr or out.stdout).strip()[:300]}"}
    return {"ok": True, "plan_id": f"da_{run_id}", "work_id": None, "inbox_path": None,
            "result_path": str(spec_dir / "state.json"), "unit": f"hearth-deepagents-{run_id}"}


def submit_task(**kwargs: Any) -> dict[str, Any]:
    """The drain's submit hook. kwargs come from Brief.submit_kwargs() + prompt=body."""
    body = str(kwargs.get("prompt") or "")
    hint = str(kwargs.get("plan_id_hint") or "brief")
    if kwargs.get("task_class") == EXPERIMENT_TASK_CLASS:
        try:
            return submit_experiment(body, hint)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if kwargs.get("task_class") == DEEPAGENTS_TASK_CLASS:
        try:
            return submit_deepagents(body, hint)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        args = submit_args_from_brief(body, idempotency_key=f"bankedfire:{hint}")
        result = call_tool("submit_local_work", args)
    except Exception as exc:  # noqa: BLE001 -- the drain records the failure and frees the slot
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    work_id = result.get("work_id")
    if not work_id:
        return {"ok": False, "error": f"submit_local_work returned no work_id: {str(result)[:300]}"}
    return {"ok": True, "plan_id": work_id, "work_id": work_id, "inbox_path": None,
            "result_path": str(manifest_path(work_id)),
            "route": result.get("route"), "status": result.get("status")}


def task_status(plan_id: str, out_file: Optional[str] = None) -> dict[str, Any]:
    """The drain's status hook: HEARTH local-work status in the conductor's shape."""
    if str(plan_id).startswith("exp_"):
        from fleet import experiment_linux
        st = experiment_linux.status(str(plan_id)[4:])
        if st.get("phase") == "missing":
            return {"ok": False, "error": f"experiment state missing for {plan_id}"}
        if st.get("outcome") is None:
            return {"ok": True, "done": False, "status": st.get("phase")}
        spec = (json.loads((experiment_linux.EXP_ROOT / str(plan_id)[4:] / "spec.json").read_text())
                if (experiment_linux.EXP_ROOT / str(plan_id)[4:] / "spec.json").exists() else {})
        return {"ok": True, "done": True, "status": st.get("outcome"),
                "result": {"ok": st.get("outcome") == "succeeded",
                           "winner": f"experiment:seat{spec.get('seat')}:{spec.get('dropin')}",
                           "failure": None if st.get("outcome") == "succeeded" else st.get("outcome")},
                "result_path": str(experiment_linux.EXP_ROOT / str(plan_id)[4:] / "state.json")}
    if str(plan_id).startswith("da_"):
        from fleet import deepagents_linux
        st = deepagents_linux.status(str(plan_id)[3:])
        if st.get("phase") == "missing":
            return {"ok": False, "error": f"deepagents state missing for {plan_id}"}
        if st.get("outcome") is None:
            return {"ok": True, "done": False, "status": st.get("phase")}
        return {"ok": True, "done": True, "status": st.get("outcome"),
                "result": {"ok": st.get("outcome") == "succeeded", "winner": "deepagents:" + str(plan_id)[3:],
                           "failure": None if st.get("outcome") == "succeeded" else "delivery gate or runner failed"},
                "result_path": str(deepagents_linux.STATE_ROOT / str(plan_id)[3:] / "state.json")}
    if not re.fullmatch(r"work_[0-9a-f]{32}", str(plan_id)):
        # A slot adopted from the Windows era names a conductor plan, not a local-work item.
        # Nothing on this host can read its result, so resolve it honestly as "no winner" and
        # let the drain free the slot instead of holding it forever as status-unreachable.
        return {"ok": True, "done": True, "status": "unreachable-from-linux",
                "result": {"ok": False, "winner": None,
                           "failure": f"plan {plan_id!r} predates the Linux lane; no result reachable"},
                "result_path": None}
    try:
        manifest = call_tool("get_local_work", {"work_id": plan_id})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    status = str(manifest.get("status"))
    if status not in DONE:
        return {"ok": True, "done": False, "status": status}
    provider = str((manifest.get("route") or {}).get("provider") or "local-work")
    produced = status == "awaiting_review" or status in {"accepted", "rejected", "superseded"}
    return {"ok": True, "done": True, "status": status,
            "result": {"ok": produced, "winner": f"omen-local-work:{provider}",
                       "failure": manifest.get("failure")},
            "result_path": str(manifest_path(plan_id))}


def queue_status() -> dict[str, Any]:
    """Local-work items still producing, from the manifests on disk (read-only)."""
    running = 0
    root = _REPO_ROOT / "runs" / "operator"
    try:
        for m in root.glob("work_*/work-manifest.json"):
            try:
                d = json.loads(m.read_text())
                if d.get("status") in {"queued", "running"} and \
                        (d.get("caller") or {}).get("submitted_by") != DRAIN_CALLER_ID:
                    running += 1   # a human is waiting on this lane: unattended work yields
            except Exception:  # noqa: BLE001 -- an unreadable manifest counts as busy
                running += 1
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"operator runs unreadable: {exc}"}
    return {"ok": True, "queued": 0, "running": running, "done": None}


# --- ticks ---------------------------------------------------------------------------------

def dry_run() -> dict[str, Any]:
    """Every gate the tick would consult, and the brief it would pick. No dispatch, no ledger."""
    state = drain.load_arm_state(drain.default_arm_state_path())
    scope = state.get("scope")
    report: dict[str, Any] = {
        "armed": state.get("armed"), "scope": scope, "in_flight": state.get("in_flight"),
        "backend": drain.DRAIN_BACKEND,
        "queue": queue_status(),
        "occupancy": occ_mod.check_occupancy(drain.DRAIN_BACKEND),
        "backlog_root": str(backlog_sources.DEFAULT_BACKLOG_ROOT),
    }
    scans = {
        "authored": backlog_sources.authored_source(backlog_sources.DEFAULT_QUEUED_DIR),
        "refined": backlog_sources.refined_source(backlog_sources.DEFAULT_REFINE_DIR),
        "candidate": backlog_sources.candidate_source(backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH,
                                                      backlog_sources.DEFAULT_EXPERIMENT_RESULTS_PATH),
    }
    report["backlog_counts"] = {k: len(v) for k, v in scans.items()}
    brief = backlog_select.select_next(scope, scans) if scope in backlog_select.SCOPES else None
    if brief is not None:
        report["next"] = {"source": brief.source, "source_ref": brief.source_ref, "slug": brief.slug,
                          "task_class": brief.task_class}
        if brief.task_class == LOCAL_WORK_TASK_CLASS:
            try:
                args = submit_args_from_brief(brief.body)
                report["next"]["submit_args"] = {k: v for k, v in args.items() if k != "intent"}
            except Exception as exc:  # noqa: BLE001
                report["next"]["brief_error"] = f"{type(exc).__name__}: {exc}"
    return report


def in_use_by_lane(slots: list[dict[str, Any]]) -> dict[str, int]:
    """Drain-owned work in flight per lane: our slot records, plus drain-submitted local-work
    manifests still producing (belt and braces: the manifest is the door's truth)."""
    counts: dict[str, int] = {}
    seen = set()
    for rec in slots:
        counts[rec.get("lane", "deep")] = counts.get(rec.get("lane", "deep"), 0) + 1
        seen.add(rec.get("plan_id"))
    for m in (_REPO_ROOT / "runs" / "operator").glob("work_*/work-manifest.json"):
        try:
            d = json.loads(m.read_text())
        except Exception:  # noqa: BLE001
            continue
        if d.get("status") in {"queued", "running"} and (d.get("caller") or {}).get("submitted_by") == DRAIN_CALLER_ID \
                and d.get("work_id") not in seen:
            lane = (d.get("route") or {}).get("selected_lane") or "deep"
            counts[lane] = counts.get(lane, 0) + 1
    return counts


def reconcile_slots(arm_path: Path) -> list[dict[str, Any]]:
    """Write back every finished slot record through the drain's own write-back (ledgered), free it."""
    reports = []
    slots = load_slots()
    pending = list(slots)          # persisted after every freed record (persist-first)
    for rec in slots:
        status = task_status(rec["plan_id"])
        if not status.get("ok") or not status.get("done"):
            reports.append({"plan_id": rec["plan_id"], "lane": rec.get("lane"), "status": status.get("status") or status.get("error")})
            continue
        result_ok, winner, result_path = drain.backlog_dispatch.read_result(status)
        outcome = drain.backlog_dispatch.completion_outcome(result_ok, winner)
        state = drain.load_arm_state(arm_path)
        extra = drain._write_back(state, rec, outcome, payload={
            "plan_id": rec.get("plan_id"), "result_path": result_path or rec.get("result_path"),
            "winner": winner, "result_ok": result_ok, "source": rec.get("source"), "source_ref": rec.get("source_ref"),
            "lane": rec.get("lane")},
            arm_state_path=arm_path, corpus_root=Path(rec.get("corpus_root") or drain.DEFAULT_CORPUS_ROOT),
            queued_dir=drain.DEFAULT_QUEUED_DIR, dispatched_dir=drain.DEFAULT_DISPATCHED_DIR,
            done_dir=drain.DEFAULT_DONE_DIR, now=None, crash=lambda point: None)
        drain._record_tick(f"observed:{outcome}", {"lane": rec.get("lane"), "plan_id": rec.get("plan_id"), **extra})
        reports.append({"plan_id": rec["plan_id"], "lane": rec.get("lane"), "observed": outcome})
        pending = [r for r in pending if r is not rec]
        save_slots(pending)
    save_slots(pending)
    return reports


def tick() -> dict[str, Any]:
    """One Linux tick: reconcile drain-owned slots, then dispatch while lanes have room.
    Each dispatch is one drain.run_tick (its gates, its persist-first steps, its ledger row);
    the in_flight record it leaves in the arm file moves into our per-lane slots file so the
    next run_tick call in the same tick can dispatch again. Every other reason ends the tick."""
    arm_path = drain.default_arm_state_path()
    report: dict[str, Any] = {"reconciled": reconcile_slots(arm_path), "dispatched": [], "reason": None}
    caps = lane_slots()
    slots = load_slots()
    for _ in range(sum(caps.values()) + 1):
        state = drain.load_arm_state(arm_path)
        if not state.get("armed"):
            report["reason"] = "disarmed"; break
        scope = state.get("scope")
        scans = {
            "authored": backlog_sources.authored_source(backlog_sources.DEFAULT_QUEUED_DIR),
            "refined": backlog_sources.refined_source(backlog_sources.DEFAULT_REFINE_DIR),
            "candidate": backlog_sources.candidate_source(backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH,
                                                          backlog_sources.DEFAULT_EXPERIMENT_RESULTS_PATH),
        }
        nxt = backlog_select.select_next(scope, scans) if scope in backlog_select.SCOPES else None
        if nxt is None:
            report["reason"] = "no-candidates"; break
        lane = brief_lane(nxt)
        used = in_use_by_lane(slots)
        if used.get(lane, 0) >= caps.get(lane, 0):
            report["reason"] = f"lane-full:{lane}"; report["in_use"] = used; break
        if lane == "experiment" and (slots or any(used.values())):
            # an experiment swaps a seat: it never overlaps any drain-owned work on any lane
            report["reason"] = "experiment-waits-for-empty-lanes"; report["in_use"] = used; break
        result = drain.run_tick(arm_state_path=arm_path, submit_task_fn=submit_task,
                                task_status_fn=task_status, queue_status_fn=queue_status)
        report["reason"] = result["reason"]
        if not str(result["reason"]).startswith("dispatched:"):
            break
        state = drain.load_arm_state(arm_path)
        rec = state.get("in_flight")
        if rec:
            rec = dict(rec); rec["lane"] = lane
            slots.append(rec); save_slots(slots)
            state["in_flight"] = None; drain.save_arm_state(state, arm_path)
        report["dispatched"].append({"plan_id": (rec or {}).get("plan_id"), "lane": lane})
    report["slots"] = [{"plan_id": r.get("plan_id"), "lane": r.get("lane")} for r in slots]
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="gates and next brief only; nothing dispatched or ledgered")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(json.dumps(dry_run(), indent=2, default=str))
        return 0
    report = tick()
    print(json.dumps(report, indent=2, default=str) if args.json else f"bankedfire-linux tick: {report['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
