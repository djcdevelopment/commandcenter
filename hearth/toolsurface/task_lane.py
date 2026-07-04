"""HEARTH tool provider: submit_task / task_status (Banked Fire P3 · Task lane).

"One scheduler" (Banked Fire design principle #1): the conductor already owns
queued/async work (build fan-out, assay, promotion). HEARTH does not grow a
second scheduler for research briefs and simple builds — it opens a *door* to
the existing one. That door is the conductor's own inbox-file mechanism (the
same one fleet builds already use), reached over SSH with zero conductor-side
changes:

  1. ``submit_task`` writes ``inbox/<plan_id>.md`` on the conductor (base64
     over SSH — no local shell quoting of the prompt body). The file starts
     with an ``<!-- CCMETA {"builders": [...]} -->`` header (conductor_maf.py's
     own ``_extract_ccmeta`` format) naming which fleet worker(s) should build
     it, followed by the builder prompt as the body. plan_id is prefixed
     ``hearth-`` so anything landing on the fleet via HEARTH is recognizable at
     a glance (provenance in the id itself, not just a ledger cross-reference).
  2. The conductor's serve loop scans ``inbox/*.md`` every ~3s (SCAN=3 in
     scripts/conductor_maf.py) and dispatches through the SAME plan->build->assay
     pipeline every fleet build goes through — a "research brief" is just a
     builder prompt whose deliverable happens to be a written finding, not code.
  3. ``task_status`` polls ``runs/<plan_id>/result.json`` (also over SSH) and
     returns its parsed content once the conductor writes it.

CAUTION (repo instructions): another agent commits on cc-conductor. This module
writes ONLY inbox/<plan_id>.md there — it must never git-operate, restart
services, or touch anything else on that box.

Every call records a ledger event via the normal gateway wrapper (P1/P4
pattern): the wrapper ledgers args/result automatically, so submit_task's own
result doubles as the "submitted" observation and task_status's result as the
"result-fetched" observation — no separate record_event call needed here.
"""

from __future__ import annotations

import base64
import re
import subprocess
import time
import uuid
from typing import Callable, Optional

SSH_USER_HOST = "claude@100.74.110.91"
CONDUCTOR_REPO = "/home/claude/work/commandcenter"
INBOX_DIR = f"{CONDUCTOR_REPO}/inbox"
RUNS_DIR = f"{CONDUCTOR_REPO}/runs"
SSH_TIMEOUT_S = 15
PLAN_ID_PREFIX = "hearth-"
# The conductor's MAF workflow fans every run across a FanOutEdgeGroup that
# requires at least two targets — a single-builder run crashes on dispatch
# ("FanOutEdgeGroup must contain at least two targets") and never writes a
# result, lingering as a phantom in-flight run that also holds occupancy.
# Default to TWO local B70 builders (both openai/vllama runners) so the offload
# lane works without pulling in a frontier (claude/sonnet) builder. cc-builder-1
# is frontier and is only the last-resort padding tail. See _ensure_fanout_minimum.
DEFAULT_BUILDERS = ["am4-worker-1", "cc-builder-2"]
FANOUT_MIN_BUILDERS = 2
COMPANION_BUILDERS = ["cc-builder-2", "am4-worker-1", "cc-builder-1"]

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _run_ssh(remote_command: str, timeout_s: float = SSH_TIMEOUT_S,
            runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
            ) -> tuple[Optional[str], Optional[str]]:
    """Run one command on the conductor over SSH. Returns (stdout, error).

    `runner` defaults to `subprocess.run` looked up at call time (not bound as
    a default argument) so tests can `patch("subprocess.run", ...)` globally,
    matching the pattern in occupancy.py's own SSH probe.
    """
    active_runner = runner or subprocess.run
    try:
        completed = active_runner(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(timeout_s)}",
             SSH_USER_HOST, remote_command],
            capture_output=True, text=True, timeout=timeout_s + 5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return None, f"ssh exit {completed.returncode}: {(completed.stderr or '').strip()[:300]}"
    return completed.stdout, None


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "task"


def _new_plan_id(hint: Optional[str]) -> str:
    """hearth-<slug>-<8 hex> — the prefix is the provenance marker itself; the
    slug (from the caller's hint, if any) keeps the inbox human-scannable; the
    hex suffix guarantees uniqueness so retries never collide."""
    suffix = uuid.uuid4().hex[:8]
    if hint:
        return f"{PLAN_ID_PREFIX}{_slugify(hint)}-{suffix}"
    return f"{PLAN_ID_PREFIX}{suffix}"


def _ccmeta_header(builders: list[str]) -> str:
    import json
    return "<!-- CCMETA\n" + json.dumps({"builders": builders}) + "\n-->\n"


def _ensure_fanout_minimum(builders: list[str]) -> list[str]:
    """Return >= FANOUT_MIN_BUILDERS distinct builders, preserving caller order.

    The conductor's MAF workflow fans every run across a FanOutEdgeGroup that
    requires at least two targets. A single-builder run therefore crashes on
    dispatch and never produces a result — it shows as forever "in-flight" and
    its ``nodes.json`` (written before the crash) reads as phantom occupancy.
    Padding a short list with distinct local-first companions lets an offloaded
    single-builder task actually run. De-duplicates while preserving order.

    The clean fix is conductor-side (treat a single-builder run as a direct
    assignment instead of a fan-out), but that is concurrently-owned code; this
    keeps the HEARTH task lane working without touching the conductor.
    """
    ordered: list[str] = []
    for b in builders:
        if b not in ordered:
            ordered.append(b)
    for companion in COMPANION_BUILDERS:
        if len(ordered) >= FANOUT_MIN_BUILDERS:
            break
        if companion not in ordered:
            ordered.append(companion)
    return ordered


def submit_task(prompt: str, builders: list[str] | None = None,
               plan_id_hint: str | None = None) -> dict:
    """Submit a research brief / simple build to the fleet via the conductor inbox.

    Writes ``inbox/<plan_id>.md`` on cc-conductor with a CCMETA builder-pin
    header (default: two local B70 builders, ``["am4-worker-1", "cc-builder-2"]``)
    and `prompt` as the body. Returns immediately with the plan_id — the
    conductor's own serve loop picks it up within one ~3s scan and runs it
    through the normal build/assay pipeline. Poll ``task_status(plan_id)`` for
    the result.

    A single-builder request is padded up to the conductor's fan-out minimum
    (>= 2 targets) — a one-builder run crashes on dispatch and never returns
    (see _ensure_fanout_minimum). The returned ``builders`` reflect what was
    actually written.

    Zero conductor-side changes: this is the same inbox mechanism every fleet
    build already uses, so no scheduler is duplicated (Banked Fire design
    principle #1).
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    chosen_builders = list(DEFAULT_BUILDERS) if builders is None else list(builders)
    if not chosen_builders or not all(isinstance(b, str) and b.strip() for b in chosen_builders):
        raise ValueError("builders must be a non-empty list of non-empty strings")
    # Pad to the conductor's fan-out minimum so a single-builder request runs
    # instead of crashing on dispatch (see _ensure_fanout_minimum).
    chosen_builders = _ensure_fanout_minimum(chosen_builders)

    plan_id = _new_plan_id(plan_id_hint)
    body = _ccmeta_header(chosen_builders) + prompt
    b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
    remote_path = f"{INBOX_DIR}/{plan_id}.md"
    # mkdir -p is a no-op if inbox/ already exists (it always does); base64 -d
    # avoids any shell-quoting of the prompt body across the SSH hop.
    remote_command = (
        f"mkdir -p {INBOX_DIR} && "
        f"echo {b64} | base64 -d > {remote_path} && "
        f"echo written"
    )
    started = time.monotonic()
    stdout, error = _run_ssh(remote_command)
    duration_ms = round((time.monotonic() - started) * 1000)

    if error is not None:
        return {"ok": False, "error": error, "plan_id": plan_id,
                "builders": chosen_builders, "duration_ms": duration_ms}
    return {
        "ok": True,
        "plan_id": plan_id,
        "builders": chosen_builders,
        "inbox_path": remote_path,
        "result_path": f"{RUNS_DIR}/{plan_id}/result.json",
        "duration_ms": duration_ms,
    }


def task_status(plan_id: str) -> dict:
    """Check whether a submitted task has a result yet.

    Reads ``runs/<plan_id>/result.json`` on the conductor over SSH. While the
    task is still queued/running there is no result file yet — that is
    reported as ``{"ok": true, "done": false}``, not an error; an SSH failure
    (conductor unreachable) is a distinct ``{"ok": false, "error": ...}``.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")

    result_path = f"{RUNS_DIR}/{plan_id}/result.json"
    remote_command = (
        f"if [ -f {result_path} ]; then cat {result_path}; else echo __HEARTH_NO_RESULT__; fi"
    )
    stdout, error = _run_ssh(remote_command)
    if error is not None:
        return {"ok": False, "error": error, "plan_id": plan_id, "done": False}

    if stdout is not None and stdout.strip() == "__HEARTH_NO_RESULT__":
        return {"ok": True, "done": False, "plan_id": plan_id, "result_path": result_path}

    import json
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "error": f"non-JSON result.json: {exc}",
                "plan_id": plan_id, "done": False}
    return {"ok": True, "done": True, "plan_id": plan_id, "result_path": result_path,
            "result": parsed}


def get_tools() -> list[Callable]:
    return [submit_task, task_status]
