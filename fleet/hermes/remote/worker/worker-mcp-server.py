#!/usr/bin/env python3
"""MCP control surface for a commandcenter fleet *worker* node.

Golden-image contract: this file ships in every builder VM image.
Capture fabric is baked in — every tool call lands in ~/.comms/capture.ndjson
with ZERO instrumentation inside the tool bodies. Born-captured.

S0: node_status · ping · check_claude_auth
S1: claim_task · run_plan · get_progress · watch_progress · get_run_log
S2: git_setup_branch · git_commit_push · git_branch_status
S3: assay_grade_branch · assay_compare_branches  (assay role only)

Transport: SSH stdio (default) or streamable-http (MCP_TRANSPORT=http).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import timezone
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# Dynamic assay (imp01): actually runs a candidate's tests + import-checks it, so
# the grade reflects behavior, not just filenames. Shipped as a sibling module in
# this same scripts/ dir; if it is absent we fall back to static-only scoring
# (resilience — a missing module must never break the assay).
try:
    from dynamic_assay import assay_workspace as _dynamic_assay  # type: ignore
except Exception:
    _dynamic_assay = None

# imp02/imp03: structural completion signal + supervised (reaped) agent launch.
# Sibling modules; if absent we fall back to the legacy detached launch + the
# "## DONE" log-scrape (resilience — a missing module never breaks builds).
try:
    import completion_signal as _completion_signal  # type: ignore  # imp02 (sentinel read/schema)
except Exception:
    _completion_signal = None
try:
    import agent_supervisor as _agent_supervisor    # type: ignore  # imp03 (process-group reaper)
except Exception:
    _agent_supervisor = None

HOME           = Path.home()
ROOT           = Path(os.environ.get("FLEET_WORKER_ROOT", Path(__file__).resolve().parent.parent))

# Non-login SSH sessions don't source ~/.profile, so CLAUDE_CODE_OAUTH_TOKEN may be missing.
# Bootstrap it here so every subprocess (including claude run_plan) inherits the token.
if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    _profile = HOME / ".profile"
    if _profile.exists():
        for _line in _profile.read_text().splitlines():
            if "CLAUDE_CODE_OAUTH_TOKEN" in _line and "=" in _line:
                _token = _line.split("=", 1)[1].strip().strip("'\"")
                if _token:
                    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = _token
                break

NODE_JSON      = ROOT / "node.json"
PROJECT_DIR    = HOME / "projects"
COMMS_DIR      = HOME / ".comms"
TASKS_DIR      = COMMS_DIR / "tasks"
PROGRESS_FILE  = COMMS_DIR / "progress.md"
CAPTURE_FILE   = COMMS_DIR / "capture.ndjson"
CLAUDE_BIN     = str(HOME / ".local" / "bin" / "claude")
# Pin the build agent to Sonnet (build tasks don't need Opus — cost/speed).
# Uses the `sonnet` alias so each node runs its newest Sonnet; override via env.
BUILDER_MODEL  = os.environ.get("BUILDER_MODEL", "sonnet")
DONE_DIR       = COMMS_DIR / "done"                              # imp02: structural completion sentinels
BUILD_MAX_WAIT = int(os.environ.get("BUILD_MAX_WAIT", "440"))   # imp03: agent hard timeout (< conductor 450s ceiling)
BUILD_BUDGET_GRACE = int(os.environ.get("BUILD_BUDGET_GRACE", "30"))  # openai runner self-exits this many s before the hard kill (honest rc=0, not a killed rc=-1)
RUNNER_CONFIG_FILE = ROOT / "runner.json"                        # agent-agnostic: per-node build runner
NODE_NAME      = os.environ.get("NODE_NAME") or socket.gethostname()   # fleet name may differ from hostname (e.g. am4-worker-1 on host am4); conductor injects

FLEET_CAPTURE_INSTRUCTIONS = "This node is part of the commandcenter fleet: a MAF-orchestrated network whose core purpose is that ALL agent-to-agent communication is centrally PERSISTED and OBSERVABLE. Every tool call here is born-captured to a durable append-only corpus AND emitted as an OpenTelemetry span to the shared visibility pane (Jaeger). You are observed by design: the persisted corpus of how this ecosystem works is the asset. Capture is the product; learning from it is the commodity.\n\nTo integrate correctly, HONOR THE VISIBILITY: pass a W3C 'traceparent' argument on EVERY tool call so your work correlates into one trace/story instead of loose events, and propagate that trace context to any child work you spawn. The problem being solved is variable; this capture+trace construct is invariant. Instrument the shape, not the task."

mcp = FastMCP("fleet-worker-node", instructions=FLEET_CAPTURE_INSTRUCTIONS)


# ── inline capture fabric ─────────────────────────────────────────────────────
# Inlined so this file deploys as a single artifact.
# Canonical source: commandcenter/spine/capture.py

class _CaptureFabric:
    def __init__(self, log_path: Path, caller: str):
        self.log_path = log_path
        self.caller = caller
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    @contextlib.contextmanager
    def capture(self, tool: str, payload: dict, traceparent: str = "", caller: Optional[str] = None):
        cid = str(uuid.uuid4())[:8]
        t0 = time.perf_counter()
        self._append({
            "capture_id": cid, "side": "request",
            "ts": datetime.datetime.now(timezone.utc).isoformat(),
            "traceparent": traceparent,
            "caller": caller or self.caller,
            "target": NODE_NAME, "tool": tool,
            "payload": payload, "error": None, "elapsed_ms": None,
        })
        holder: dict = {}
        err: Optional[str] = None
        try:
            yield holder
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            self._append({
                "capture_id": cid, "side": "response",
                "ts": datetime.datetime.now(timezone.utc).isoformat(),
                "traceparent": traceparent,
                "caller": caller or self.caller,
                "target": NODE_NAME, "tool": tool,
                "payload": holder.get("result"), "error": err, "elapsed_ms": elapsed,
            })


FABRIC = _CaptureFabric(CAPTURE_FILE, caller=NODE_NAME)


# ── inline traceparent utils ──────────────────────────────────────────────────

_TP_PATTERN = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def _child_traceparent(parent: str) -> str:
    m = _TP_PATTERN.match(parent or "")
    if not m:
        return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
    trace_id = m.group(1)
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"


def _extract_trace_id(tp: str) -> str:
    m = _TP_PATTERN.match(tp or "")
    return m.group(1) if m else ""


# ── helpers ───────────────────────────────────────────────────────────────────

def _cmd(args: list[str], timeout: int = 10) -> str:
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
        return (p.stdout or p.stderr).strip()
    except Exception as exc:
        return f"(unavailable: {exc})"


def _stamp(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n{text}timestamp: {ts}\n")


def _runner_config() -> dict:
    """Per-node build-agent runner config (agent-agnostic). Defaults to Claude.

    Reads ~/fleet-worker-node/runner.json when present, e.g.:
      {"runner":"openai","base_url":"http://am4...:8090/v1","model":"vllama-planner",
       "token_file":"/home/claude/.config/fleet/hermes.token","max_steps":24}
    Absent file -> {"runner":"claude"} (unchanged behavior). BUILDER_RUNNER env overrides."""
    cfg = {"runner": os.environ.get("BUILDER_RUNNER", "claude"),
           "base_url": "", "model": "", "token_file": "", "max_steps": 24}
    try:
        if RUNNER_CONFIG_FILE.exists():
            cfg.update(json.loads(RUNNER_CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


# ── resources ─────────────────────────────────────────────────────────────────

@mcp.resource("worker://node")
def node_resource() -> str:
    if NODE_JSON.exists():
        return NODE_JSON.read_text(encoding="utf-8")
    return json.dumps({"node": NODE_NAME, "note": "no node.json"}, indent=2)


@mcp.resource("worker://progress")
def progress_resource() -> str:
    return PROGRESS_FILE.read_text(encoding="utf-8") if PROGRESS_FILE.exists() else "(no progress log yet)"


# ── S0 tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def node_status(traceparent: str = "") -> dict[str, Any]:
    """Return this worker's identity, OS, resources, and tool availability."""
    with FABRIC.capture("node_status", {"traceparent": traceparent}, traceparent=traceparent) as h:
        du = shutil.disk_usage(str(ROOT))
        try:
            load = os.getloadavg()
        except (OSError, AttributeError):
            load = None
        claude_path = shutil.which("claude") or str(HOME / ".local/bin/claude")
        result = {
            "node": NODE_NAME,
            "user": _cmd(["whoami"]),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "loadavg": load,
            "disk_free_gib": round(du.free / 1024**3, 1),
            "disk_total_gib": round(du.total / 1024**3, 1),
            "tools": {
                "claude": claude_path if Path(claude_path).exists() else "(absent)",
                "git": shutil.which("git") or "(absent)",
                "python3": shutil.which("python3") or "(absent)",
            },
            "trace_id": _extract_trace_id(traceparent),
            "time": int(time.time()),
        }
        h["result"] = result
        return result


@mcp.tool()
def ping(message: str = "ok", traceparent: str = "") -> dict[str, Any]:
    """Liveness echo over the MCP transport."""
    with FABRIC.capture("ping", {"message": message, "traceparent": traceparent}, traceparent=traceparent) as h:
        result = {"pong": message, "node": NODE_NAME, "trace_id": _extract_trace_id(traceparent), "time": int(time.time())}
        h["result"] = result
        return result


@mcp.tool()
def check_claude_auth(traceparent: str = "") -> dict[str, Any]:
    """Check if Claude CLI is installed and authenticated on this node.

    Used by the conductor to gate dispatch — a VM in 'needs-auth' state must not receive tasks.
    Returns authed=True only when both the binary is present AND credentials are configured.
    """
    with FABRIC.capture("check_claude_auth", {"traceparent": traceparent}, traceparent=traceparent) as h:
        claude_path = shutil.which("claude") or str(HOME / ".local/bin/claude")
        binary_exists = Path(claude_path).exists()

        if not binary_exists:
            result = {
                "authed": False,
                "reason": "claude binary not found",
                "node": NODE_NAME,
                "claude_path": claude_path,
            }
            h["result"] = result
            return result

        # Check for auth config files (claude setup-token writes credentials here)
        config_candidates = [
            HOME / ".config" / "anthropic" / "CLAUDE.json",
            HOME / ".config" / "claude" / "settings.json",
            HOME / ".claude" / "settings.json",
        ]
        cred_found = next((str(p) for p in config_candidates if p.exists()), None)

        # Also accept CLAUDE_CODE_OAUTH_TOKEN in environment
        env_token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))

        authed = bool(cred_found or env_token)

        # Get version if binary works
        version = _cmd([claude_path, "--version"], timeout=5)

        result = {
            "authed": authed,
            "node": NODE_NAME,
            "claude_path": claude_path,
            "claude_version": version,
            "cred_file": cred_found,
            "env_token": env_token,
            "reason": "ready" if authed else "claude binary found but not authenticated — run: claude",
        }
        h["result"] = result
        return result


# ── S1 tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def claim_task(task_id: str, claim_token: str, traceparent: str = "") -> dict[str, Any]:
    """Atomic single-writer claim for a task.

    Uses O_CREAT|O_EXCL to guarantee only one process ever claims a given task_id.
    Returns refused=True if already claimed by anyone — including this node.

    State machine: new → claimed (one-way). A claimed task cannot be re-claimed.
    This is the double-claim guard (the L12 trap).
    """
    with FABRIC.capture("claim_task", {"task_id": task_id, "claim_token": claim_token, "traceparent": traceparent}, traceparent=traceparent) as h:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        lock_file = TASKS_DIR / f"{task_id}.lock"

        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            record = {
                "task_id": task_id,
                "claim_token": claim_token,
                "node": NODE_NAME,
                "claimed_at": datetime.datetime.now(timezone.utc).isoformat(),
                "traceparent": traceparent,
                "trace_id": _extract_trace_id(traceparent),
            }
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
            result = {
                "claimed": True, "refused": False,
                "task_id": task_id, "node": NODE_NAME,
                "claim_token": claim_token,
                "trace_id": _extract_trace_id(traceparent),
            }
        except FileExistsError:
            try:
                existing = json.loads(lock_file.read_text())
            except Exception:
                existing = {}
            result = {
                "claimed": False, "refused": True,
                "task_id": task_id, "node": NODE_NAME,
                "existing_claim": existing,
                "reason": "task already claimed — double-claim refused",
            }

        h["result"] = result
        return result


@mcp.tool()
def run_plan(plan: str, plan_id: str = "spine-stub-001", workdir: str = "",
             traceparent: str = "", mode: str = "build", runner_preset: str = "",
             max_age_s: int | None = None) -> dict[str, Any]:
    """Deliver a plan and launch a Claude agent to execute it.

    The plan is written to ~/projects/plans/<plan_id>.md, then a Claude agent
    is launched non-interactively. Progress logs to ~/.comms/progress.md.

    workdir: working directory for the agent (default: ~/projects). Pass the
    farmer-workspace path from git_setup_branch so outputs land in the right branch.

    traceparent is propagated into the agent's environment for child spans.
    """
    with FABRIC.capture("run_plan", {"plan_id": plan_id, "traceparent": traceparent}, traceparent=traceparent) as h:
        if max_age_s is not None and (type(max_age_s) is not int or max_age_s < 1):
            raise ValueError('positive job deadline required')
        run_limit = min(BUILD_MAX_WAIT, max_age_s) if max_age_s is not None else BUILD_MAX_WAIT
        COMMS_DIR.mkdir(parents=True, exist_ok=True)
        plan_dir = PROJECT_DIR / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        resolved_runner = None
        if runner_preset:
            from runner_presets import resolve
            rcfg, resolved_runner = resolve(ROOT, runner_preset, plan_dir / f"{plan_id}.runner.json")
        else:
            if plan_id.startswith(("hermes-", "hearth-hermes-")):
                raise ValueError("Hermes runs require a per-run preset")
            rcfg = _runner_config()

        plan_file = plan_dir / f"{plan_id}.md"
        plan_file.write_text(plan, encoding="utf-8")
        _stamp(PROGRESS_FILE, f"## PLAN_RECEIVED: {plan_id}\nplan_file: {plan_file}\n")

        log_file = COMMS_DIR / f"run-{plan_id}.log"
        env = {
            **os.environ,
            "PATH": f"{HOME}/.local/bin:{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
            "TRACEPARENT": traceparent,
            "TASK_ID": plan_id,
        }

        # Derive a child traceparent for the agent span
        agent_tp = _child_traceparent(traceparent)

        run_cwd = Path(workdir) if workdir else PROJECT_DIR
        run_cwd.mkdir(parents=True, exist_ok=True)

        # Give the agent eyes: point it at the read-only source reference (if seeded)
        # so it can read the existing code + the spec it is extending before building.
        ref_note = ""
        if SRC_REFERENCE.exists() and any(SRC_REFERENCE.iterdir()):
            ref_note = (
                f"A READ-ONLY reference copy of the existing commandcenter source is at "
                f"{SRC_REFERENCE}. Consult it (and the plan/spec) to understand the code you "
                f"are extending BEFORE you build. Do NOT modify anything under {SRC_REFERENCE}; "
                f"write ALL of your output inside your working directory ({run_cwd}). "
            )

        if mode == "plan":
            # Planning mode: the agent is a PLANNER, not a builder - produce ONLY the plan
            # document (plan.md) and write no code. (No build "eyes"/ref_note here.)
            prompt = (
                f"Follow the instructions in {plan_file}. They ask you to PLAN, not build: produce "
                f"ONLY a single file named plan.md in {run_cwd}, and write NO code, tests, config, or "
                f"other files. Do not implement the idea; only plan it. "
                f"Before starting, append '## STARTED: {plan_id}' with a timestamp to {PROGRESS_FILE}. "
                f"When plan.md is written, append '## DONE: {plan_id}' with a one-line summary to {PROGRESS_FILE}."
            )
        else:
            prompt = (
                f"Execute the plan in {plan_file}. "
                f"{ref_note}"
                f"Before starting, append '## STARTED: {plan_id}' with a timestamp to {PROGRESS_FILE}. "
                f"After finishing each step, append a brief '## PROGRESS' note to {PROGRESS_FILE}. "
                f"When fully done, append '## DONE: {plan_id}' with a summary to {PROGRESS_FILE}."
            )

        # Agent-agnostic runner selection. Default = Claude Code CLI (Anthropic).
        # runner "openai" drives any OpenAI /v1 backend (AM4 Qwen3 via hermes, OMEN
        # Ollama, or a frontier API) through the self-contained tool-calling agent.
        runner = rcfg.get("runner", "claude")
        if runner in ("openai", "hearth"):
            _agent = str(Path(__file__).resolve().parent / ("agent_hearth.py" if runner == "hearth" else "agent_openai.py"))
            agent_cmd = [sys.executable, _agent,
                         "--plan-file", str(plan_file), "--workdir", str(run_cwd),
                         "--base-url", rcfg.get("base_url", ""),
                         "--model", rcfg.get("model", ""),
                         "--token-file", rcfg.get("token_file", ""),
                         "--task-id", plan_id, "--reference", str(SRC_REFERENCE),
                         "--max-steps", str(rcfg.get("max_steps", 24)),
                         "--budget-s", str(max(1, run_limit - BUILD_BUDGET_GRACE))]
        else:
            agent_cmd = [CLAUDE_BIN, "--model", BUILDER_MODEL,
                         "--dangerously-skip-permissions", "-p", prompt]

        # Prefer the supervised, structurally-signalled launch (imp02+imp03): a
        # detached launcher supervises the agent in its own process group (kills
        # the group on timeout — no orphans) and writes ~/.comms/done/<id>.json on
        # exit. Fall back to the legacy detached launch (+ "## DONE" scrape) if the
        # launcher/reaper module is missing.
        _launcher = str(Path(__file__).resolve().parent / "build_agent_launch.py")
        supervised = _agent_supervisor is not None and os.path.exists(_launcher)
        if runner_preset and not supervised:
            raise ValueError('preset jobs require the hard-timeout supervisor')
        if supervised:
            DONE_DIR.mkdir(parents=True, exist_ok=True)
            launch_cmd = [sys.executable, _launcher, plan_id, str(DONE_DIR),
                          str(run_limit), str(run_cwd), "--"] + agent_cmd
        else:
            launch_cmd = agent_cmd

        proc = subprocess.Popen(
            launch_cmd,
            cwd=str(run_cwd),
            stdin=subprocess.DEVNULL,
            stdout=open(log_file, "w"),
            stderr=subprocess.STDOUT,
            env={**env, "TRACEPARENT": agent_tp},
            start_new_session=True,
        )

        result = {
            "run_id": plan_id,
            "plan_file": str(plan_file),
            "agent_pid": proc.pid,
            "log_file": str(log_file),
            "progress_file": str(PROGRESS_FILE),
            "status": "launched",
            "runner": runner,
            "runner_preset": runner_preset or None,
            "resolved_runner": resolved_runner,
            "runner_model": rcfg.get("model") if runner in ("openai", "hearth") else BUILDER_MODEL,
            "supervised": supervised,
            "hard_timeout_s": run_limit,
            "signal_file": str(DONE_DIR / f"{plan_id}.json") if supervised else None,
            "trace_id": _extract_trace_id(traceparent),
            "agent_traceparent": agent_tp,
            "time": int(time.time()),
        }
        h["result"] = result
        return result


def _scan_done_signals() -> dict:
    """Return {task_id: signal_dict} for every structural completion sentinel (imp02).

    Pure filesystem read of ~/.comms/done/*.json — no log scraping. Best-effort:
    a malformed/partial sentinel is skipped, never raises."""
    signals: dict = {}
    try:
        if not DONE_DIR.exists():
            return signals
        for sf in DONE_DIR.glob("*.json"):
            if _completion_signal is not None:
                sig = _completion_signal.read_signal(sf.stem, DONE_DIR)
            else:
                try:
                    sig = json.loads(sf.read_text(encoding="utf-8"))
                except Exception:
                    sig = None
            if sig is not None:
                signals[sf.stem] = sig
    except Exception:
        pass
    return signals


@mcp.tool()
def get_progress(tail_chars: int = 2000, traceparent: str = "") -> dict[str, Any]:
    """Read the worker's progress log + structural completion sentinels.

    done_signals (imp02) is the structural done channel the conductor prefers;
    done_runs (the "## DONE" scrape) is kept as a fallback."""
    with FABRIC.capture("get_progress", {"tail_chars": tail_chars}, traceparent=traceparent) as h:
        done_signals = _scan_done_signals()
        if not PROGRESS_FILE.exists():
            result: dict = {"exists": False, "content": "", "line_count": 0, "mtime": None,
                            "done_runs": [], "done_signals": done_signals}
            h["result"] = result
            return result
        content = PROGRESS_FILE.read_text(encoding="utf-8")
        tail = content[-tail_chars:] if len(content) > tail_chars else content
        lines = content.strip().split("\n") if content.strip() else []
        done_lines = [ln for ln in lines if ln.startswith("## DONE:")]
        result = {
            "exists": True,
            "line_count": len(lines),
            "content": tail,
            "mtime": int(PROGRESS_FILE.stat().st_mtime),
            "done_runs": done_lines,
            "done_signals": done_signals,
            "node": NODE_NAME,
            "time": int(time.time()),
        }
        h["result"] = result
        return result


@mcp.tool()
async def watch_progress(last_mtime: float = 0.0, timeout: int = 120, traceparent: str = "") -> dict[str, Any]:
    """Block until progress.md changes, then return new content.

    Pass last_mtime=0 on first call; use returned mtime on each subsequent call.
    On timeout returns changed=False — conductor retries.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if PROGRESS_FILE.exists():
            current_mtime = PROGRESS_FILE.stat().st_mtime
            if current_mtime > last_mtime:
                content = PROGRESS_FILE.read_text(encoding="utf-8")
                lines = content.strip().split("\n") if content.strip() else []
                done_lines = [ln for ln in lines if ln.startswith("## DONE:")]
                return {
                    "changed": True, "content": content, "mtime": current_mtime,
                    "done_runs": done_lines, "node": NODE_NAME,
                }
        await asyncio.sleep(0.5)

    if PROGRESS_FILE.exists():
        content = PROGRESS_FILE.read_text(encoding="utf-8")
        return {"changed": False, "content": content, "mtime": PROGRESS_FILE.stat().st_mtime,
                "done_runs": [], "node": NODE_NAME}
    return {"changed": False, "content": "", "mtime": 0.0, "done_runs": [], "node": NODE_NAME}


@mcp.tool()
def get_run_log(plan_id: str = "spine-stub-001", tail_chars: int = 2000) -> dict[str, Any]:
    """Read stdout/stderr log for a specific run."""
    log_file = COMMS_DIR / f"run-{plan_id}.log"
    if not log_file.exists():
        return {"exists": False, "content": "", "plan_id": plan_id}
    content = log_file.read_text(encoding="utf-8", errors="replace")
    tail = content[-tail_chars:] if len(content) > tail_chars else content
    return {
        "exists": True, "plan_id": plan_id, "log_file": str(log_file),
        "size_bytes": log_file.stat().st_size, "content": tail,
        "mtime": int(log_file.stat().st_mtime),
    }


# ── Farmer Repo / git tools ───────────────────────────────────────────────────

FARMER_REPO_SSH = os.environ.get(
    "FARMER_REPO_SSH",
    "claude@172.29.14.180:/home/claude/work/commandcenter/farmer-repo",
)
FARMER_CLONE_DIR = HOME / "farmer-workspace"

# ── read-only source reference ("eyes") ───────────────────────────────────────
# A build agent needs to READ the existing commandcenter source + the spec it is
# extending before it builds. We mirror the conductor's source tree to a sibling
# dir OUTSIDE the git workspace, so the agent can read it but it never gets
# committed into (or inflates the score of) the build branch. Derived from
# FARMER_REPO_SSH so there is one source of truth for "where the conductor is".
SRC_REFERENCE = HOME / "commandcenter-src"
_farmer_userhost, _, _farmer_path = FARMER_REPO_SSH.partition(":")
# .../commandcenter/farmer-repo -> .../commandcenter
_src_remote_path = str(Path(_farmer_path).parent) if _farmer_path else ""
SRC_REMOTE = f"{_farmer_userhost}:{_src_remote_path}/" if _src_remote_path else ""
# Keep the mirror lean + source-only (the venv alone is ~980M).
_SRC_EXCLUDES = [
    ".venv", "farmer-repo", "runs", "__pycache__", "*.pyc",
    "*.log", "capture.ndjson", ".git", "node_modules", "*.png",
]


def _seed_source_reference() -> dict:
    """Incrementally mirror the conductor's source tree to SRC_REFERENCE.

    Best-effort and NON-FATAL: if the sync fails, the build still runs (the agent
    just won't have the reference). Resilience over completeness.
    """
    if not SRC_REMOTE:
        return {"ok": False, "reason": "no SRC_REMOTE derivable"}
    SRC_REFERENCE.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-az", "--delete", "--timeout=60",
           "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"]
    for ex in _SRC_EXCLUDES:
        cmd += ["--exclude", ex]
    cmd += [SRC_REMOTE, str(SRC_REFERENCE) + "/"]
    out = _cmd(cmd, timeout=90)
    ok = "(unavailable" not in out and "error" not in out.lower()
    return {"ok": ok, "path": str(SRC_REFERENCE), "remote": SRC_REMOTE, "detail": out[-200:]}


@mcp.tool()
def git_setup_branch(plan_id: str, lap: int = 1, traceparent: str = "") -> dict[str, Any]:
    """Clone Farmer Repo and checkout the branch for this worker + plan.

    Creates branch ccfarm/{plan_id}/{node}/{lap} if it doesn't exist.
    Returns the local workspace path ready for build output.
    """
    branch = f"ccfarm/{plan_id}/{NODE_NAME}/lap{lap}"
    workspace = FARMER_CLONE_DIR / plan_id

    if workspace.exists():
        # Pull latest + switch branch
        out = _cmd(["git", "-C", str(workspace), "fetch", "origin"], timeout=30)
        try:
            _cmd(["git", "-C", str(workspace), "checkout", branch], timeout=10)
            _cmd(["git", "-C", str(workspace), "pull", "--ff-only", "origin", branch], timeout=15)
        except Exception:
            _cmd(["git", "-C", str(workspace), "checkout", "-b", branch], timeout=10)
    else:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        _cmd(["git", "clone", FARMER_REPO_SSH, str(workspace),
              "-o", "origin", "--no-local"], timeout=60)
        _cmd(["git", "-C", str(workspace), "config",
              "user.email", f"{NODE_NAME}@commandcenter"], timeout=5)
        _cmd(["git", "-C", str(workspace), "config",
              "user.name", NODE_NAME], timeout=5)
        try:
            _cmd(["git", "-C", str(workspace), "checkout", "-b", branch], timeout=10)
        except Exception:
            _cmd(["git", "-C", str(workspace), "checkout", branch], timeout=10)

    # Give the agent eyes: mirror the read-only source reference (best-effort).
    seed = _seed_source_reference()

    return {
        "branch": branch,
        "workspace": str(workspace),
        "node": NODE_NAME,
        "plan_id": plan_id,
        "lap": lap,
        "ready": True,
        "source_reference": seed.get("path") if seed.get("ok") else None,
        "source_reference_ok": seed.get("ok", False),
    }


@mcp.tool()
def git_commit_push(plan_id: str, message: str = "", lap: int = 1,
                    traceparent: str = "") -> dict[str, Any]:
    """Stage all changes in the farmer workspace, commit, and push to Farmer Repo."""
    branch = f"ccfarm/{plan_id}/{NODE_NAME}/lap{lap}"
    workspace = FARMER_CLONE_DIR / plan_id

    if not workspace.exists():
        return {"ok": False, "error": f"workspace not found: {workspace}"}

    commit_msg = message or f"build: {NODE_NAME} lap{lap} for {plan_id}"
    _cmd(["git", "-C", str(workspace), "add", "-A"], timeout=10)
    status = _cmd(["git", "-C", str(workspace), "status", "--short"], timeout=5)

    # The build agent often commits its own work, leaving a clean tree. The old
    # code returned here WITHOUT pushing on a clean tree — so a self-committed
    # build never reached the remote and the assay scored it F/0. Fix: commit any
    # uncommitted changes, then ALWAYS push the branch so the agent's own commits
    # land too. A truly-empty build still pushes (branch == origin/main) so the
    # assay can score it an honest F instead of crashing on a missing branch.
    committed_now = False
    if status.strip():
        _cmd(["git", "-C", str(workspace), "commit", "-m", commit_msg], timeout=15)
        committed_now = True

    push_out = _cmd(["git", "-C", str(workspace), "push", "origin", branch,
                     "--set-upstream"], timeout=30)

    # How much did this branch actually produce (commits beyond the seed)?
    ahead = _cmd(["git", "-C", str(workspace), "rev-list", "--count",
                  "origin/main..HEAD"], timeout=5)
    try:
        commits_ahead = int(ahead.strip())
    except (ValueError, AttributeError):
        commits_ahead = -1

    log = _cmd(["git", "-C", str(workspace), "log", "--oneline", "-3"], timeout=5)
    return {
        "ok": True,
        "branch": branch,
        "committed": committed_now,
        "pushed": True,
        "commits_ahead": commits_ahead,
        "empty_build": commits_ahead == 0,
        "message": commit_msg,
        "workspace": str(workspace),
        "push_out": push_out.strip()[-300:],
        "log": log.strip(),
    }


@mcp.tool()
def git_branch_status(plan_id: str, lap: int = 1, traceparent: str = "") -> dict[str, Any]:
    """Return git status + recent log for this worker's Farmer Repo branch."""
    branch = f"ccfarm/{plan_id}/{NODE_NAME}/lap{lap}"
    workspace = FARMER_CLONE_DIR / plan_id

    if not workspace.exists():
        return {"exists": False, "branch": branch, "workspace": str(workspace)}

    status = _cmd(["git", "-C", str(workspace), "status", "--short"], timeout=5)
    log = _cmd(["git", "-C", str(workspace), "log", "--oneline", "-5"], timeout=5)
    return {
        "exists": True,
        "branch": branch,
        "workspace": str(workspace),
        "status": status.strip(),
        "log": log.strip(),
    }


# ── Assay / QA tools (S3) ────────────────────────────────────────────────────
# Active when node has role "assay". Grades builder branches from Farmer Repo.

ASSAY_WORKSPACE = HOME / "assay-workspace"


def _assay_fetch_branch(plan_id: str, worker: str, lap: int) -> Path:
    """Clone or update a builder's branch into the local assay workspace."""
    branch = f"ccfarm/{plan_id}/{worker}/lap{lap}"
    dest = ASSAY_WORKSPACE / plan_id / worker
    if dest.exists():
        _cmd(["git", "-C", str(dest), "fetch", "origin"], timeout=30)
        _cmd(["git", "-C", str(dest), "checkout", branch], timeout=10)
        _cmd(["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"], timeout=10)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _cmd(["git", "clone", FARMER_REPO_SSH, str(dest),
              "--branch", branch, "--no-local", "--depth", "1"], timeout=60)
    return dest


def _score_workspace(path: Path) -> dict:
    """Quality score for a builder workspace.

    Behavior-first: when the workspace has Python evidence (tests or importable
    modules) we run the dynamic assay (execute pytest + import-check via
    dynamic_assay.assay_workspace) and drive the grade from its behavior_score.
    The old filename-based static score is KEPT in the output so the delta stays
    visible (capture the delta — never silently overwrite the signal). If the
    dynamic assay is unavailable, or there is no Python evidence to run, we fall
    back to the static score.
    """
    all_files = [f for f in path.rglob("*") if f.is_file() and ".git" not in f.parts]
    code_exts = {".py", ".js", ".ts", ".sh", ".yaml", ".yml", ".json", ".md"}
    code_files = [f for f in all_files if f.suffix in code_exts]
    has_retro  = any("retro" in f.name.lower() for f in all_files)
    has_result = any("result" in f.name.lower() or "output" in f.name.lower()
                     for f in all_files)
    has_tests  = any("test" in f.name.lower() for f in all_files)

    # Read retro/result text for summary
    retro_text = ""
    for f in all_files:
        if "retro" in f.name.lower() or "result" in f.name.lower():
            try:
                retro_text = f.read_text(errors="replace")[:500]
                break
            except Exception:
                pass

    # Static rubric (0–100) — filename heuristic, kept for the delta.
    static_score = 0
    static_score += min(len(code_files) * 5, 40)      # up to 40pts: file count
    static_score += 20 if has_result else 0            # 20pts: result/output present
    static_score += 20 if has_retro  else 0            # 20pts: retro present
    static_score += 10 if has_tests  else 0            # 10pts: tests present
    static_score += 10 if len(code_files) >= 3 else 0  # 10pts: substance (≥3 code files)
    static_score = min(static_score, 100)

    # Dynamic assay — actually execute the candidate (isolated + timed out inside).
    dyn = None
    if _dynamic_assay is not None:
        try:
            dyn = _dynamic_assay(str(path))
        except Exception:
            dyn = None

    has_py_evidence = bool(dyn) and (
        dyn.get("tests_found", 0) > 0
        or (dyn.get("imports_ok", 0) + dyn.get("imports_failed", 0)) > 0
    )
    if has_py_evidence:
        score = int(dyn["behavior_score"])
        assay_mode = "behavior"
    else:
        score = static_score
        assay_mode = "static" if dyn is None else "static-fallback"

    risk = round(1.0 - score / 100, 2)
    if   score >= 80: grade = "A"
    elif score >= 65: grade = "B"
    elif score >= 50: grade = "C"
    elif score >= 30: grade = "D"
    else:             grade = "F"

    return {
        "score": score,
        "grade": grade,
        "risk_score": risk,
        # visibility: how the grade was reached + the static baseline for the delta
        "assay_mode": assay_mode,
        "static_score": static_score,
        "behavior_score": (int(dyn["behavior_score"]) if dyn else None),
        "tests_found": (dyn.get("tests_found") if dyn else None),
        "tests_passed": (dyn.get("tests_passed") if dyn else None),
        "tests_failed": (dyn.get("tests_failed") if dyn else None),
        "imports_ok": (dyn.get("imports_ok") if dyn else None),
        "imports_failed": (dyn.get("imports_failed") if dyn else None),
        "assay_summary": (dyn.get("summary") if dyn else None),
        # static signals (kept for backward-compat + the delta)
        "file_count": len(all_files),
        "code_files": len(code_files),
        "has_retro": has_retro,
        "has_result": has_result,
        "has_tests": has_tests,
        "retro_excerpt": retro_text,
    }


@mcp.tool()
def assay_grade_branch(plan_id: str, worker: str, lap: int = 1,
                       traceparent: str = "") -> dict[str, Any]:
    """Fetch a builder's Farmer Repo branch and return quality grade + risk score.

    Grades the build output using a rubric: file count, result presence,
    retro presence, test presence. Returns grade (A-F) and risk_score (0-1).
    """
    try:
        workspace = _assay_fetch_branch(plan_id, worker, lap)
        scores = _score_workspace(workspace)
        branch = f"ccfarm/{plan_id}/{worker}/lap{lap}"
        return {
            "ok": True,
            "plan_id": plan_id,
            "worker": worker,
            "branch": branch,
            "lap": lap,
            "grader": NODE_NAME,
            **scores,
        }
    except Exception as e:
        return {"ok": False, "plan_id": plan_id, "worker": worker, "error": str(e)}


@mcp.tool()
def assay_compare_branches(plan_id: str, workers: list[str], lap: int = 1,
                           traceparent: str = "") -> dict[str, Any]:
    """Grade all named workers' branches for a plan and return ranked results.

    Fetches each branch, scores it, ranks by score descending.
    Returns winner + full scoreboard.
    """
    results = []
    for worker in workers:
        try:
            workspace = _assay_fetch_branch(plan_id, worker, lap)
            scores = _score_workspace(workspace)
            results.append({"worker": worker, "branch": f"ccfarm/{plan_id}/{worker}/lap{lap}",
                            **scores})
        except Exception as e:
            results.append({"worker": worker, "ok": False, "error": str(e),
                            "score": -1, "grade": "F", "risk_score": 1.0})

    results.sort(key=lambda r: r.get("score", -1), reverse=True)
    winner = results[0]["worker"] if results else None
    return {
        "plan_id": plan_id,
        "lap": lap,
        "grader": NODE_NAME,
        "winner": winner,
        "scoreboard": results,
    }


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    COMMS_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("http", "streamable-http"):
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
