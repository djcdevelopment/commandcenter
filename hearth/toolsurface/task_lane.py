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
     it, followed by the builder prompt as the body. Since M3 (token hole #1,
     2026-09-03) the same header also carries ``task_class``, ``est_tokens``
     and ``est_tokens_source`` so the conductor can copy them into
     ``runs/<plan_id>/result.json`` (hole #2, conductor-side) and hindsight can
     join estimate to actual instead of falling back to a flat default.
     plan_id is prefixed ``hearth-`` so anything landing on the fleet via HEARTH
     is recognizable at a glance (provenance in the id itself, not just a
     ledger cross-reference).
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

from hearth.toolsurface._scope import resolve_in_scope

SSH_USER_HOST = "claude@cc-conductor.mshome.net"  # local Hyper-V switch; machine lanes don't ride the tailnet (ADR-0014)
CONDUCTOR_REPO = "/home/claude/work/commandcenter"
INBOX_DIR = f"{CONDUCTOR_REPO}/inbox"
RUNS_DIR = f"{CONDUCTOR_REPO}/runs"
SSH_TIMEOUT_S = 15
PLAN_ID_PREFIX = "hearth-"
# The conductor's MAF workflow fans every run across a FanOutEdgeGroup that
# requires at least two targets — a single-builder run crashes on dispatch
# ("FanOutEdgeGroup must contain at least two targets") and never writes a
# result, lingering as a phantom in-flight run that also holds occupancy.
# Default to TWO local builders (both openai runners) so the offload lane works
# without pulling in a frontier (claude/sonnet) builder. cc-builder-1 has no
# runner.json, so it falls back to the METERED claude runner — it is only the
# last-resort padding tail. See _ensure_fanout_minimum.
#
# Re-pointed 2026-08-29 after a live sweep found every local builder aimed at a
# dead backend: am4-worker-1 -> am4 oxen :8090 (answers, every model
# ready:false), cc-builder-2 -> the same oxen over the tailnet, cc-builder-3 ->
# OMEN Ollama :11434 (OllamaBoot disabled for good, ADR-0034). The whole local
# task lane was dark. omen-arc is not the fix — llama-server binds 127.0.0.1:8082,
# so the builder VMs (Default Switch, 172.19.240.0/20) cannot see it at all.
# fx99-ollama (192.168.12.220:11434) IS reachable from the VMs and was proved
# with a real decode, so cc-builder-2/3 runner.json now point there
# (qwen2.5-coder:7b / qwen2.5:14b; old values saved as runner.json.bak-2026-08-29).
# Moving cc-builder-2 off its tailnet URL also restores ADR-0014/0015 compliance.
# When llama-swap lands (ADR-0040 phase 2) it should also listen on the Default
# Switch address so these builders get the B70 rung instead of an 8 GB sidecar.
DEFAULT_BUILDERS = ["cc-builder-2", "cc-builder-3"]
FANOUT_MIN_BUILDERS = 2
COMPANION_BUILDERS = ["cc-builder-2", "cc-builder-3", "cc-builder-1"]

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


# --- Token hole #1 (M3, 2026-09-03) -------------------------------------------
# Every submit_task call used to leave est_tokens empty, so hindsight
# (hearth/scheduler/hindsight.py) joined each fleet run to a flat
# DEFAULT_EST_TOKENS = 2000 and the token objective in the solver never saw a
# per-job number. estimate_tokens() is a *derived* estimate — a labeled guess,
# never a measurement — and est_tokens_source says which it was so a consumer
# can weight "caller" (the caller knew) differently from "derived" (this
# heuristic). Calibrating the heuristic against actual tokens_out is hole #2's
# job once the conductor copies these stamps into result.json.
CHARS_PER_TOKEN = 4.0
# Expected builder OUTPUT per task_class — the part of the bill the prompt length
# cannot tell you. Authored intent, not receipts: a research brief is a 1–2 page
# proposal; a build emits code + tests + a commit; a proofing run is a retest
# whose deliverable is a short report. Unknown classes get hindsight's own flat
# default (DEFAULT_EST_TOKENS = 2000) so a derived estimate is never *below*
# what the join would have assumed anyway.
OUTPUT_ALLOWANCE_TOKENS: dict[str, int] = {
    "research": 2000,
    "build": 4000,
    "proofing": 1000,
}
DEFAULT_OUTPUT_ALLOWANCE_TOKENS = 2000
EST_TOKENS_SOURCE_CALLER = "caller"
EST_TOKENS_SOURCE_DERIVED = "derived"


def estimate_tokens(prompt: str, task_class: str | None = None) -> int:
    """Pure, deterministic token estimate for a task lane brief.

    ``ceil(len(prompt) / CHARS_PER_TOKEN)`` for the prompt body (the ~4 chars
    per token rule of thumb for English prose and code) plus the task_class
    output allowance from ``OUTPUT_ALLOWANCE_TOKENS`` (default
    ``DEFAULT_OUTPUT_ALLOWANCE_TOKENS`` for None/unknown). No I/O, no clock,
    no randomness — the same inputs always give the same number, so a dry-run
    and the real submit agree. This is an estimate for capacity planning and
    the scheduler's token objective, not a measurement; results stamp it with
    ``est_tokens_source = "derived"`` so consumers can tell.
    """
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    prompt_tokens = -(-len(prompt) // int(CHARS_PER_TOKEN))  # ceil division
    allowance = OUTPUT_ALLOWANCE_TOKENS.get(task_class or "", DEFAULT_OUTPUT_ALLOWANCE_TOKENS)
    return int(prompt_tokens + allowance)


def _validate_est_tokens(est_tokens: object, where: str = "est_tokens") -> Optional[int]:
    """Return est_tokens as a non-negative int, or None when absent.

    Accepts ints and integral floats (JSON-over-MCP hands integral numbers back
    as either); rejects bools, negatives, and non-integral values with a
    ValueError naming the field, so a bad manifest item fails before any inbox
    write.
    """
    if est_tokens is None:
        return None
    if isinstance(est_tokens, bool) or not isinstance(est_tokens, (int, float)):
        raise ValueError(f"{where} must be a non-negative integer when provided")
    if isinstance(est_tokens, float) and not est_tokens.is_integer():
        raise ValueError(f"{where} must be a non-negative integer when provided")
    value = int(est_tokens)
    if value < 0:
        raise ValueError(f"{where} must be a non-negative integer when provided")
    return value


def _ccmeta_header(builders: list[str], task_class: str | None = None,
                   est_tokens: int | None = None,
                   est_tokens_source: str | None = None) -> str:
    """Render the conductor's CCMETA header.

    ``builders`` is what the conductor reads today. ``task_class``,
    ``est_tokens`` and ``est_tokens_source`` ride along (omitted when None) so
    the conductor can copy them into ``result.json`` later (token hole #2) —
    the header is the only channel that reaches the run directory, since the
    HEARTH ledger stores just a digest of this tool's result.
    """
    import json
    meta: dict = {"builders": builders}
    if task_class is not None:
        meta["task_class"] = task_class
    if est_tokens is not None:
        meta["est_tokens"] = est_tokens
    if est_tokens_source is not None:
        meta["est_tokens_source"] = est_tokens_source
    return "<!-- CCMETA\n" + json.dumps(meta) + "\n-->\n"


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
               plan_id_hint: str | None = None, task_class: str | None = None,
               est_tokens: int | None = None) -> dict:
    """Submit a research brief / simple build to the fleet via the conductor inbox.

    Writes ``inbox/<plan_id>.md`` on cc-conductor with a CCMETA builder-pin
    header (default: ``DEFAULT_BUILDERS`` — the two local builders currently
    pointed at a live local rung; re-pointed 2026-08-29 to cc-builder-2/3 on
    fx99-ollama after every local builder was found aimed at a dead backend,
    see the roster comment above) and `prompt` as the body. Returns
    immediately with the plan_id — the conductor's own serve loop picks it up
    within one ~3s scan and runs it through the normal build/assay pipeline.
    Poll ``task_status(plan_id)`` for the result.

    A single-builder request is padded up to the conductor's fan-out minimum
    (>= 2 targets) — a one-builder run crashes on dispatch and never returns
    (see _ensure_fanout_minimum). The returned ``builders`` reflect what was
    actually written.

    ``task_class`` overrides the gateway's static derivation in the ledger
    event. ``est_tokens`` is the caller's estimate of the job's token cost;
    when absent it is DERIVED with ``estimate_tokens(prompt, task_class)`` so
    no submit ever leaves the field empty (token hole #1). The result carries
    ``est_tokens`` and ``est_tokens_source`` (``"caller"`` | ``"derived"``),
    and both — plus ``task_class`` — ride the CCMETA header so the conductor
    can copy them into ``result.json`` (hole #2, conductor-side).

    Zero conductor-side changes: this is the same inbox mechanism every fleet
    build already uses, so no scheduler is duplicated (Banked Fire design
    principle #1).
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    chosen_builders = list(DEFAULT_BUILDERS) if builders is None else list(builders)
    if not chosen_builders or not all(isinstance(b, str) and b.strip() for b in chosen_builders):
        raise ValueError("builders must be a non-empty list of non-empty strings")
    if task_class is not None and (not isinstance(task_class, str) or not task_class.strip()):
        raise ValueError("task_class must be a non-empty string when provided")
    # Pad to the conductor's fan-out minimum so a single-builder request runs
    # instead of crashing on dispatch (see _ensure_fanout_minimum).
    chosen_builders = _ensure_fanout_minimum(chosen_builders)

    # Token hole #1: never leave est_tokens empty. A caller-supplied value is
    # kept verbatim and labeled; an absent one is derived and labeled as such.
    caller_est = _validate_est_tokens(est_tokens)
    if caller_est is None:
        est_tokens_value = estimate_tokens(prompt, task_class)
        est_tokens_source = EST_TOKENS_SOURCE_DERIVED
    else:
        est_tokens_value = caller_est
        est_tokens_source = EST_TOKENS_SOURCE_CALLER
    stamps: dict = {"est_tokens": est_tokens_value, "est_tokens_source": est_tokens_source}
    if task_class is not None:
        stamps["task_class"] = task_class
        # The gateway wrapper lifts this key into the ledger event's task_class
        # and pops it before the caller sees the result.
        stamps["_ledger_task_class"] = task_class

    plan_id = _new_plan_id(plan_id_hint)
    body = _ccmeta_header(chosen_builders, task_class=task_class,
                          est_tokens=est_tokens_value,
                          est_tokens_source=est_tokens_source) + prompt
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
        # A failed write is still a classed, estimated dispatch attempt — the
        # ledger event should not lose its task_class because SSH hiccupped.
        return {"ok": False, "error": error, "plan_id": plan_id,
                "builders": chosen_builders, "duration_ms": duration_ms, **stamps}
    return {
        "ok": True,
        "plan_id": plan_id,
        "builders": chosen_builders,
        "inbox_path": remote_path,
        "result_path": f"{RUNS_DIR}/{plan_id}/result.json",
        "duration_ms": duration_ms,
        **stamps,
    }


def task_status(plan_id: str, out_file: str | None = None) -> dict:
    """Check whether a submitted task has a result yet.

    Reads ``runs/<plan_id>/result.json`` on the conductor over SSH. While the
    task is still queued/running there is no result file yet — that is
    reported as ``{"ok": true, "done": false}``, not an error; an SSH failure
    (conductor unreachable) is a distinct ``{"ok": false, "error": ...}``.

    ``out_file`` (G3/A5 queue-and-forget): when given AND the run has completed,
    the full result text is written to that HEARTH_SCOPE-sandboxed path and the
    tool returns only a small ACK — ``{ok, done, plan_id, result_path, out_file,
    bytes_written, ...}`` plus a couple of cheap scalars (``winner``,
    ``result_ok``) lifted from the result so the caller can branch on success
    without reading the file. The big result blob never enters the caller's
    context. A run that has not finished writes NO file (still ``done:false``);
    an SSH failure writes no file either.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    if out_file is not None and (not isinstance(out_file, str) or not out_file.strip()):
        raise ValueError("out_file must be a non-empty string when provided")

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

    if out_file is not None:
        # Land the full result text in a scoped file and hand back only an ACK —
        # a caller draining many queue-and-forget tasks never holds the blobs.
        raw = stdout if isinstance(stdout, str) else ""
        target = resolve_in_scope(out_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = raw.encode("utf-8")
        target.write_bytes(data)
        ack = {
            "ok": True, "done": True, "plan_id": plan_id,
            "result_path": result_path, "out_file": str(target),
            "bytes_written": len(data),
        }
        # Cheap scalars only (never the whole blob) so the caller can tell
        # success from failure without reading the file back.
        try:
            parsed = json.loads(raw)
            ack["parse_ok"] = True
            if isinstance(parsed, dict):
                if "winner" in parsed:
                    ack["winner"] = parsed["winner"]
                if "ok" in parsed:
                    ack["result_ok"] = parsed["ok"]
        except (json.JSONDecodeError, TypeError):
            ack["parse_ok"] = False
        return ack

    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "error": f"non-JSON result.json: {exc}",
                "plan_id": plan_id, "done": False}
    return {"ok": True, "done": True, "plan_id": plan_id, "result_path": result_path,
            "result": parsed}


def queue_status() -> dict:
    """Queue-depth probe (G3/A5): the conductor's backlog in ONE SSH round-trip,
    so a queue-and-forget caller can gauge load without polling each plan_id.

    A run is a ``runs/<id>/`` directory and its ONLY terminal marker is
    ``result.json`` (ADR-0033) — the same definition hearth.toolsurface.patrol
    sweeps, so what reads busy here is exactly what the guard dog can reach and
    masters_pet can heal. Corollaries worth knowing: a *directory rename* (the
    hand-triage ``<id>.crashed`` gesture) releases nothing — nothing in this repo
    or on the conductor reads that suffix, and renaming also breaks task_status
    and the conductor's own checkpoint resume, so don't; write the result (or let
    masters_pet stub it) instead.

    Returns ``{ok, queued, running, running_undispatched, done, hearth_queued}``:
      - ``queued``   — ``inbox/*.md`` awaiting the conductor's next ~3s scan.
      - ``running``  — ``runs/<id>/`` dirs with no ``result.json`` yet. NOTE a
        crashed/phantom run that never wrote a result is indistinguishable from
        a live one by file presence alone, so a count that never falls is
        suspect, not necessarily busy — ``patrol``/``masters_pet`` are what tell
        the two apart, and they now see every dir this counts.
      - ``running_undispatched`` — the subset of ``running`` that has no
        ``nodes.json``: the conductor made the dir but never pinned a builder
        graph (e.g. its "no build workers available" abort). These can never
        finish on their own; a non-zero count that persists is pure phantom
        occupancy awaiting a heal.
      - ``done``     — ``runs/<id>/`` dirs that have a ``result.json``.
      - ``hearth_queued`` — the subset of ``queued`` whose id carries the
        ``hearth-`` provenance prefix (submitted through this door).
    """
    snippet = (
        f'q=$(ls -1 {INBOX_DIR}/*.md 2>/dev/null | wc -l | tr -d " "); '
        f'hq=$(ls -1 {INBOX_DIR}/{PLAN_ID_PREFIX}*.md 2>/dev/null | wc -l | tr -d " "); '
        f'r=0; d=0; u=0; '
        f'if [ -d {RUNS_DIR} ]; then '
        f'for x in {RUNS_DIR}/*/; do [ -e "$x" ] || continue; '
        f'if [ -f "${{x}}result.json" ]; then d=$((d+1)); '
        f'else r=$((r+1)); [ -f "${{x}}nodes.json" ] || u=$((u+1)); fi; done; fi; '
        f'echo "queued=$q running=$r done=$d hearth_queued=$hq running_undispatched=$u"'
    )
    stdout, error = _run_ssh(snippet)
    if error is not None:
        return {"ok": False, "error": error}
    counts = {"queued": 0, "running": 0, "running_undispatched": 0,
              "done": 0, "hearth_queued": 0}
    for token in (stdout or "").split():
        key, sep, val = token.partition("=")
        if sep and key in counts:
            try:
                counts[key] = int(val)
            except ValueError:
                pass
    return {"ok": True, **counts}


def submit_batch(manifest: list[dict]) -> dict:
    """Submit many briefs in one call (G3/A5 queue-and-forget).

    ``manifest`` is a list of items shaped like submit_task's own args:
    ``{"prompt": str, "builders"?: list[str], "task_class"?: str,
    "plan_id_hint"?: str, "est_tokens"?: int}``. Each item is submitted through
    the SAME submit_task inbox mechanism — no second scheduler, no
    conductor-side change (Banked Fire principle #1) — so an item without
    ``est_tokens`` gets a derived estimate exactly as a single submit would
    (token hole #1). The whole manifest is
    validated up front, so a malformed item fails the call before ANY inbox
    file is written; once validation passes every item is attempted and its
    per-item outcome is returned, so a mid-batch SSH failure is visible per task
    instead of collapsing the batch.

    Ledger/dashboard visibility: this call is wrapped and ledgered by the
    gateway exactly like submit_task, and every later task_status poll is
    likewise ledgered — so queue-and-forget adds NO blind spot; the batch
    submission and each task's completion-fetch both land in the ledger the same
    way single submits do (and roll up into knowledge/capacity.json on the next
    rebuild). It deliberately does NOT emit a parallel record_event stream,
    which would double-count against the wrapper's own ledgering.

    Returns ``{ok, count, submitted, plan_ids}``: ``ok`` is True only if every
    item was written; ``plan_ids`` are the ids actually created, in order.
    """
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("manifest must be a non-empty list of task items")
    # Validate the WHOLE manifest before writing anything, so a bad item never
    # leaves half a batch on the conductor.
    for i, item in enumerate(manifest):
        if not isinstance(item, dict):
            raise ValueError(f"manifest[{i}] must be a dict")
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"manifest[{i}].prompt must be a non-empty string")
        builders = item.get("builders")
        if builders is not None and (
            not isinstance(builders, list)
            or not all(isinstance(b, str) and b.strip() for b in builders)
        ):
            raise ValueError(f"manifest[{i}].builders must be a list of non-empty strings")
        for key in ("task_class", "plan_id_hint"):
            val = item.get(key)
            if val is not None and (not isinstance(val, str) or not val.strip()):
                raise ValueError(f"manifest[{i}].{key} must be a non-empty string when provided")
        _validate_est_tokens(item.get("est_tokens"), where=f"manifest[{i}].est_tokens")

    submitted: list[dict] = []
    plan_ids: list[str] = []
    all_ok = True
    for item in manifest:
        res = submit_task(
            prompt=item["prompt"],
            builders=item.get("builders"),
            plan_id_hint=item.get("plan_id_hint"),
            task_class=item.get("task_class"),
            est_tokens=item.get("est_tokens"),
        )
        submitted.append(res)
        if res.get("ok"):
            plan_ids.append(res["plan_id"])
        else:
            all_ok = False
    return {"ok": all_ok, "count": len(manifest), "submitted": submitted, "plan_ids": plan_ids}


def get_tools() -> list[Callable]:
    return [submit_task, task_status, queue_status, submit_batch]
