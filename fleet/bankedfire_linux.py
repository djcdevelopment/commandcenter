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
from datetime import datetime, timedelta
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
    raw = os.environ.get("BANKEDFIRE_SLOTS", "fast=3,deep=1,experiment=1,deepagents=1,tool=2")
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
        # AM4 tool-pair routes (2026-09-28) have their own lane: two seats, one job each, and they
        # must not consume the B70-bound deepagents slot.
        try:
            fields, _ = parse_local_work_block_lenient(brief.body)
        except Exception:  # noqa: BLE001
            fields = {}
        if str(fields.get("backend", "")).startswith("am4-tool"):   # a seat, or "am4-tool" = the sizer picks one
            return "tool"
        return "deepagents"
    if brief.task_class == PROOFING_TASK_CLASS:
        return "deep"   # proposals are drafted on the 27B, one at a time
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
        "deadline_s": int(fields.get("deadline_s", 2400)),   # follows the work.produce ceiling (sizing-map)
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


SIZED_TOOL_BACKEND = "am4-tool"   # brief `backend: am4-tool` -> the sizer picks the seat (ADR-0050)
TOOL_SEAT_SHORT = "am4-tool-4070ti"   # prefill seat: short answers, volume
TOOL_SEAT_LONG = "am4-tool-5070"      # decode seat: l/xl answers


def size_tool_seat(intent: str, source: str, max_report_words: Optional[int] = None) -> tuple[str, dict[str, Any]]:
    """(seat, sizer answer) for a tool chore: the 5070 when the sizer refines tool_execution to
    tool_long_output (bins l/xl), else the 4070 Ti. Always sizes (heuristic at minimum) because the
    brief asked for it by naming `am4-tool`; HEARTH_SIZER=npu upgrades the sizer, never disables it."""
    from hearth.sizer import size_request, sizer_mode
    try:
        size = Path(source).stat().st_size
    except OSError:
        size = 0
    text = intent if not max_report_words else f"max_report_words: {max_report_words}\n{intent}"
    mode = sizer_mode()
    answer = size_request(text, files=[{"path": source, "bytes": size}], payload_bytes=size + len(intent.encode("utf-8")),
                          task_family="tool_execution", mode=mode if mode != "off" else "heuristic") or {}
    seat = TOOL_SEAT_LONG if answer.get("task_family") == "tool_long_output" else TOOL_SEAT_SHORT
    return seat, answer


def deepagents_spec_from_brief(body: str, run_id: str) -> dict[str, Any]:
    fields, intent = parse_local_work_block_lenient(body)
    if not fields.get("source"):
        raise ValueError("deepagents brief lacks 'source' (the one file the agent may read/edit)")
    if not intent:
        raise ValueError("deepagents brief has no task after the '---' line")
    max_words = int(fields["max_report_words"]) if fields.get("max_report_words") else None
    backend = fields.get("backend", "omen-dense")
    spec = {"id": run_id, "source": fields["source"], "task": intent,
            "backend": backend,
            "report": str(fields.get("report", "false")).lower() in ("1", "true", "yes"),
            "max_report_words": max_words,
            "work": fields.get("work")}
    if backend == SIZED_TOOL_BACKEND:
        seat, answer = size_tool_seat(intent, fields["source"], max_words)
        spec["backend"] = seat
        spec["backend_requested"] = backend
        spec["sizer"] = answer
    return spec


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


PROOFING_TASK_CLASS = backlog_sources.CANDIDATE_TASK_CLASS   # "proofing": a priced candidate
_CANDIDATE_ID_RE = re.compile(r"Run experiment candidate '([^']+)'")   # candidate_prompt is byte-pinned
SKIP_DAYS = 7


# --- candidate (proofing) briefs ---------------------------------------------------------------
# A candidate brief is the drain's own prose ("Run experiment candidate '<id>' ..."); on the Windows
# conductor a builder answered it with proposals/<slug>.md. Here the deep lane drafts that proposal
# as a whole_file local-work candidate, so the deliverable arrives at awaiting_review with the same
# review gate as every other Linux brief (ADR-0048). The prompt is enriched from the derived
# candidate record; it is never edited (test_sources pins its bytes).

def candidate_id_from_body(body: str) -> str:
    m = _CANDIDATE_ID_RE.search(body)
    if not m:
        raise ValueError("proofing brief names no candidate id (candidate_prompt shape changed?)")
    return m.group(1)


def load_candidate_record(candidate_id: str, path: Optional[Path] = None) -> dict[str, Any]:
    path = path or backlog_sources.DEFAULT_EXPERIMENT_CANDIDATES_PATH
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read candidate list {path}: {type(exc).__name__}") from exc
    for row in doc.get("candidates") or ():
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id:
            return row
    raise ValueError(f"stale candidate: {candidate_id!r} is not in {Path(path).name}")


def proofing_args_from_brief(body: str, *, idempotency_key: str, candidates_path: Optional[Path] = None,
                             repo: Optional[str] = None) -> dict[str, Any]:
    """submit_local_work arguments for one priced candidate. Pure apart from `git rev-parse`."""
    candidate_id = candidate_id_from_body(body)
    rec = load_candidate_record(candidate_id, candidates_path)
    slug = backlog_sources.safe_slug(candidate_id)
    subject = rec.get("subject") or {}
    lines = [body.strip(), "", "Candidate record (knowledge/experiment_candidates.json):",
             f"- candidate_id: {candidate_id}", f"- experiment_type: {rec.get('experiment_type')}",
             f"- subject: builder={subject.get('builder_id')} model={subject.get('model_id')} backend={subject.get('backend')} "
             f"task_kind={subject.get('task_kind')} metric={subject.get('metric')}",
             f"- question: {rec.get('question')}", f"- evidence_sought: {rec.get('evidence_sought')}",
             f"- gate: {rec.get('gate')}", f"- risk_accepted: {rec.get('risk_accepted')}",
             f"- confidence: {rec.get('confidence')}", f"- last_observed: {rec.get('last_observed')}", "",
             f"Write `proposals/{slug}.md`: an experiment proposal with sections Hypothesis, Workload shape "
             "(model, backend, prompt depth, concurrency, measurement), Evidence that would move the belief, "
             "Stop rule, and Runnable on omen-linux (name the lane — omen-vllm, omen-dense-27b, am4-vllm, fx99-vllm — "
             "or say `not runnable on omen-linux` and why). Run nothing; propose only."]
    criteria = [f"The proposal names the candidate_id {candidate_id} verbatim in its first section.",
                "The proposal states the hypothesis and the evidence sought as separate sections.",
                "The proposal states which omen-linux lane can run it, or says `not runnable on omen-linux` and why."]
    repo = repo or str(_REPO_ROOT)
    return {"intent": "\n".join(lines), "acceptance_criteria": criteria, "repo": repo,
            "base_commit": resolve_commit(repo, "HEAD"), "files": ["knowledge/README.md"],
            "artifact_kind": "whole_file", "target_path": f"proposals/{slug}.md", "lane": "deep",
            "task_family": "drafting", "max_tokens": 6144, "deadline_s": 2400, "idempotency_key": idempotency_key}


def submit_proofing(body: str, hint: str) -> dict[str, Any]:
    args = proofing_args_from_brief(body, idempotency_key=f"bankedfire:{hint}")
    result = call_tool("submit_local_work", args)
    work_id = result.get("work_id")
    if not work_id:
        return {"ok": False, "error": f"submit_local_work returned no work_id: {str(result)[:300]}"}
    return {"ok": True, "plan_id": work_id, "work_id": work_id, "inbox_path": None,
            "result_path": str(manifest_path(work_id)), "route": result.get("route"), "status": result.get("status")}


# --- skips: candidates the tick will not offer again for a while ---------------------------------

def skips_path() -> Path:
    return Path(os.environ.get("HEARTH_ROOT", str(Path.home() / "hearth-production"))) / "var" / "bankedfire_linux_skips.json"


def load_skips(path: Optional[Path] = None) -> list[dict[str, Any]]:
    p = path or skips_path()
    try:
        return list(json.loads(p.read_text()).get("skips") or [])
    except (OSError, ValueError):
        return []


def add_skip(source_ref: str, reason: str, *, days: int = SKIP_DAYS, path: Optional[Path] = None,
             now: Optional[datetime] = None) -> None:
    p = path or skips_path(); now = now or datetime.utcnow()
    rows = load_skips(p)
    rows.append({"source": "candidate", "source_ref": source_ref, "reason": reason[:200],
                 "at": now.isoformat(timespec="seconds") + "Z",
                 "until": (now + timedelta(days=days)).isoformat(timespec="seconds") + "Z"})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps({"schema": "bankedfire-linux-skips.v1", "skips": rows}, indent=2)); os.replace(tmp, p)


def candidate_exclusions(now: Optional[datetime] = None, *, skips: Optional[Path] = None,
                         worth_path: Optional[Path] = None, candidates_path: Optional[Path] = None) -> frozenset:
    """Unexpired skips plus priced ids that no longer exist in the derived candidate list.
    Stale ids are computed, never written: a rebuild that resurrects an id un-stales it."""
    now = now or datetime.utcnow(); stamp = now.isoformat(timespec="seconds") + "Z"
    out = {r["source_ref"] for r in load_skips(skips) if r.get("source") == "candidate" and str(r.get("until", "")) > stamp}
    try:
        worth = json.loads(Path(worth_path or backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH).read_text()).get("entries") or []
        known = {c.get("candidate_id") for c in json.loads(Path(candidates_path or backlog_sources.DEFAULT_EXPERIMENT_CANDIDATES_PATH).read_text()).get("candidates") or []}
    except (OSError, ValueError):
        return frozenset(out)
    out |= {e["candidate_id"] for e in worth if isinstance(e, dict) and isinstance(e.get("candidate_id"), str)
            and e.get("status") != backlog_sources.CANDIDATE_RETIRED and e["candidate_id"] not in known}
    return frozenset(out)


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
    if kwargs.get("task_class") == PROOFING_TASK_CLASS:
        try:
            return submit_proofing(body, hint)
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
                                                      backlog_sources.DEFAULT_EXPERIMENT_RESULTS_PATH,
                                                      exclude_refs=candidate_exclusions()),
    }
    report["excluded"] = sorted(candidate_exclusions())
    report["backlog_counts"] = {k: len(v) for k, v in scans.items()}
    brief = backlog_select.select_next(scope, scans) if scope in backlog_select.SCOPES else None
    if brief is not None:
        report["next"] = {"source": brief.source, "source_ref": brief.source_ref, "slug": brief.slug,
                          "task_class": brief.task_class}
        if brief.task_class in (LOCAL_WORK_TASK_CLASS, PROOFING_TASK_CLASS):
            try:
                args = (submit_args_from_brief(brief.body) if brief.task_class == LOCAL_WORK_TASK_CLASS
                        else proofing_args_from_brief(brief.body, idempotency_key="dry-run"))
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


# --- AM4 profile follows the queue (T4, 2026-09-28) --------------------------------------------
# The tool-pair seats exist only while AM4 serves that profile, and the dense 27B only while it
# serves the other one. The tick switches AM4 to tool-pair when tool-lane briefs are queued and
# nothing else drain-owned is in flight on AM4, and back to dense-tp2 once the tool queue and the
# tool slots are empty. Both switches go through am4-profile over the direct cable and are recorded
# in the tick report; a failed switch leaves the profile file saying failed:<target>.
AM4_SSH = "10.44.0.2"
AM4_PROFILE_WAIT_S = 240


def am4_profile() -> Optional[str]:
    from fleet import deepagents_linux
    return deepagents_linux.am4_profile()


def am4_switch(target: str) -> dict[str, Any]:
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", AM4_SSH, f"~/bin/am4-profile {target}"],
                             capture_output=True, text=True, timeout=AM4_PROFILE_WAIT_S + 30)
        return {"target": target, "rc": out.returncode, "tail": (out.stdout + out.stderr)[-300:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"target": target, "rc": None, "error": f"{type(exc).__name__}: {exc}"}


def am4_profile_wanted(tool_queued: int, slots: list[dict[str, Any]], live: Optional[str]) -> Optional[str]:
    """The profile the queue wants, or None when nothing should change."""
    tool_in_flight = any(r.get("lane") == "tool" for r in slots)
    if tool_queued and live == "dense-tp2" and not tool_in_flight:
        return "tool-pair"
    if not tool_queued and not tool_in_flight and live == "tool-pair":
        return "dense-tp2"
    return None


def tick() -> dict[str, Any]:
    """One Linux tick: reconcile drain-owned slots, then dispatch while lanes have room.
    Each dispatch is one drain.run_tick (its gates, its persist-first steps, its ledger row);
    the in_flight record it leaves in the arm file moves into our per-lane slots file so the
    next run_tick call in the same tick can dispatch again. Every other reason ends the tick."""
    arm_path = drain.default_arm_state_path()
    report: dict[str, Any] = {"reconciled": reconcile_slots(arm_path), "dispatched": [], "reason": None}
    caps = lane_slots()
    slots = load_slots()
    excl = candidate_exclusions()
    report["excluded"] = sorted(excl); report["skipped"] = []
    ran_drain = 0
    # AM4 profile follows the queue (see am4_profile_wanted)
    try:
        queued_scan = backlog_sources.authored_source(backlog_sources.DEFAULT_QUEUED_DIR)
        tool_queued = sum(1 for b in queued_scan.briefs if brief_lane(b) == "tool")
        live = am4_profile()
        want = am4_profile_wanted(tool_queued, slots, live)
        report["am4_profile"] = {"live": live, "tool_queued": tool_queued, "switch": None}
        if want and drain.load_arm_state(arm_path).get("armed"):
            report["am4_profile"]["switch"] = am4_switch(want)
    except Exception as exc:  # noqa: BLE001 -- the profile step never blocks the OMEN lanes
        report["am4_profile"] = {"error": f"{type(exc).__name__}: {exc}"}
    for _ in range(sum(caps.values()) + 1):
        state = drain.load_arm_state(arm_path)
        if not state.get("armed"):
            report["reason"] = "disarmed"; break
        scope = state.get("scope")
        scans = {
            "authored": backlog_sources.authored_source(backlog_sources.DEFAULT_QUEUED_DIR),
            "refined": backlog_sources.refined_source(backlog_sources.DEFAULT_REFINE_DIR),
            "candidate": backlog_sources.candidate_source(backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH,
                                                          backlog_sources.DEFAULT_EXPERIMENT_RESULTS_PATH,
                                                          exclude_refs=excl),
        }
        nxt = backlog_select.select_next(scope, scans) if scope in backlog_select.SCOPES else None
        if nxt is None:
            report["reason"] = "no-candidates"; break
        lane = brief_lane(nxt)
        used = in_use_by_lane(slots)
        if used.get("experiment", 0):
            # 2026-09-27 20:56Z: the first live tick dispatched the seat-0 experiment and then a
            # deepagents brief in the same loop; the swap took the 27B away and the delivery died
            # ("rendered prompt check failed: 500"). While an experiment holds a seat, nothing else
            # dispatches, whatever the other lanes' caps say.
            report["reason"] = "experiment-in-flight"; report["in_use"] = used; break
        if used.get(lane, 0) >= caps.get(lane, 0):
            report["reason"] = f"lane-full:{lane}"; report["in_use"] = used; break
        if lane == "experiment" and (slots or any(used.values())):
            # an experiment swaps a seat: it never overlaps any drain-owned work on any lane
            report["reason"] = "experiment-waits-for-empty-lanes"; report["in_use"] = used; break
        result = drain.run_tick(arm_state_path=arm_path, submit_task_fn=submit_task,
                                task_status_fn=task_status, queue_status_fn=queue_status,
                                exclude_refs=excl)
        ran_drain += 1
        report["reason"] = result["reason"]
        if not str(result["reason"]).startswith("dispatched:"):
            detail = result.get("detail") or {}
            if result["reason"] == "no-op:dispatch-failed" and detail.get("source") == "candidate" and detail.get("source_ref"):
                # a candidate that cannot be dispatched must not be re-picked every 30 minutes
                add_skip(str(detail["source_ref"]), f"dispatch-failed: {detail.get('submit_error')}")
                report["skipped"].append({"source_ref": detail["source_ref"], "days": SKIP_DAYS})
            break
        state = drain.load_arm_state(arm_path)
        rec = state.get("in_flight")
        if rec:
            rec = dict(rec); rec["lane"] = lane
            slots.append(rec); save_slots(slots)
            state["in_flight"] = None; drain.save_arm_state(state, arm_path)
        report["dispatched"].append({"plan_id": (rec or {}).get("plan_id"), "lane": lane})
        if lane == "experiment":
            report["reason"] = "dispatched:experiment-holds-the-seats"; break
    report["slots"] = [{"plan_id": r.get("plan_id"), "lane": r.get("lane")} for r in slots]
    if ran_drain == 0:
        # ADR-0006: every tick is ledgered, including the no-ops. The Linux tick decides
        # no-candidates / experiment-in-flight / lane-full before drain.run_tick runs, and five
        # such ticks on 2026-09-27 (22:29Z..00:01Z) left no row. Record them here.
        report["ledger_event_id"] = drain._record_tick(
            f"no-op:{report['reason']}",
            {"backend": drain.DRAIN_BACKEND, "lane": "bankedfire_linux", "excluded": report.get("excluded", []),
             "in_use": report.get("in_use", {}), "slots": report["slots"]})
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
