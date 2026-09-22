"""HEARTH tool provider: build-request receipts.

This is the Hearth-native control-plane lane for engineering work receipts. It
keeps the Comfy FieldLab receipt layout compatible with the existing PowerShell
scripts while exposing the lifecycle as MCP tools:

create -> inspect/list/update -> execute/delegate -> close.

The authored request is immutable. Updates and execution/closure records are
append-only in ``*.events.jsonl``; ``*.receipt.json`` is the current projection.

DELEGATION (B-02). ``execute_build_request(mode="delegate")`` renders the receipt
as a self-contained fleet brief and hands it to the existing task lane
(``task_lane.submit_task``) with the receipt's required deliverables as
``requires`` and the caller's lifetime as ``max_age_s``. No second scheduler: the
conductor still dispatches. ``update_build_request(sync_delegation=True)`` is the
poll — a human or a cadence caller drives it; nothing here fires on a timer, and
nothing here ever marks a receipt ``done``.

Every delegation state change is an appended event plus a rewritten projection,
so a fresh process reconstructs the delegation from ``*.receipt.json`` alone. The
one deliberate exception is the harvest guard: ``harvested`` is written to disk
BEFORE ``harvest_fn`` is called (persist-first), so a crash mid-harvest cannot be
replayed into a second harvest by the next sync. That write carries no event; the
outcome event (``delegation_completed``) records what actually happened.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from tools.workflow.assay_acceptance import check_lap_acceptance

from hearth.toolsurface import fleet_harvest, task_lane
from hearth.toolsurface._scope import resolve_in_scope, scope_root
from hearth.toolsurface.backends import BackendConfigError, load_pool, select_backend
from hearth.toolsurface.task_expectations import validate_max_age_s, validate_requires

# Receipts moved 2026-08-24: the comfy tree was retired and the old default
# (C:\work\comfy\...) no longer resolved, so the lane would have recreated a
# retired path and orphaned the existing ledger. The 57 receipts + ledger.jsonl
# were moved to this path, which fieldlab/.gitignore keeps out of the repo.
DEFAULT_RECEIPT_DIR = Path(os.environ.get(
    "HEARTH_BUILD_REQUEST_DIR",
    r"C:\work\baseline\fieldlab\runs\build-requests",
))
FINAL_STATUSES = {"done", "failed", "blocked", "cancelled"}
OPEN_STATUSES = {"open", "running", *FINAL_STATUSES}
SECRET_KEY_RE = re.compile(r"(token|secret|password|apikey|api_key|access[_-]?key|credential)", re.I)
# A credential prefix must not be the suffix of an ordinary identifier, such as
# task-family or disk-cache. Sensitive dictionary fields remain fully redacted.
SECRET_VALUE_RE = re.compile(
    r"(ya29\.[A-Za-z0-9._-]+|(?<![A-Za-z0-9_])sk-[A-Za-z0-9._-]+|gh[pousr]_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+)",
)
_ID_RE = re.compile(r"^br-\d{8}-\d{6}-[a-f0-9]{8}$")

# --- Delegation vocabulary (B-02) --------------------------------------------
# Deliverable globs reuse B-01's `requires` validator verbatim (relative, no
# drive letter, no '..', no NUL/newline, non-empty) because the SAME list is what
# rides submit_task's CCMETA header to the conductor - two validators would let a
# receipt hold a glob the task lane refuses. Two bounds are added on top: a
# tighter count cap, and a literal-backslash ban (a deliverable is matched with
# fnmatch against `git ls-tree` output, which is always POSIX-separated, so a
# backslash glob can never match anything and would read as a silent miss).
MAX_DELIVERABLES = 32
_BACKSLASH_RE = re.compile(r"\\")
# Never let a Windows absolute path (or a UNC share) reach a fleet worker: the
# worker's world is a read-only checkout at ~/commandcenter-src, so a drive-letter
# path is at best meaningless and at worst leaks this box's layout.
_WINDOWS_PATH_RE = re.compile(r"((?<![A-Za-z0-9_])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9_.$-]+\\)")

# Observed run states (what `status_fn` told us), deliberately coarse: the
# delegation record stores a decision-relevant summary, not the conductor's blob.
DELEGATION_PENDING = "pending"
DELEGATION_UNREACHABLE = "unreachable"
DELEGATION_DONE = "done"
# Lifecycle of the delegation record itself.
DELEGATION_SUBMITTED = "submitted"
DELEGATION_COMPLETED = "completed"
# Terminal results of one delegation. Every one of these is an outcome someone can
# act on; none of them is `done` - closing the receipt stays a human/agent act.
RESULT_ACCEPTED = "accepted"
RESULT_ACCEPTANCE_FAILED = "acceptance_failed"
RESULT_NO_WINNER = "no_winner"
RESULT_WINNER_BRANCH_MISSING = "winner_branch_missing"
RESULT_HARVEST_FAILED = "harvest_failed"
RESULT_HARVEST_INCOMPLETE = "harvest_incomplete"

FLEET_BRANCH_PREFIX = "fleet"   # fleet_harvest.DEST_PREFIX
FLEET_LAP = "lap1"
ORIGIN_PREFIX = "origin/"

DELEGATION_PREAMBLE = (
    "HEARTH BUILD-REQUEST BRIEF - a real build dispatched through the fleet task lane.\n"
    "You have READ-ONLY source at ~/commandcenter-src. Ground every claim in the ACTUAL\n"
    "code and docs and cite real repo-relative paths; do NOT invent files, tools or APIs.\n"
    "If a path named below is missing from your checkout, say so in the deliverable\n"
    "instead of guessing. Write ONLY the required deliverables listed below and commit\n"
    "ONLY those files - nothing else in the tree.\n"
)
DELEGATION_POSTAMBLE = (
    "Deliverables, and nothing else, must be committed: {deliverables}.\n"
    "Acceptance is checked mechanically against the branch file list: a lap missing any\n"
    "one of those paths fails acceptance and is not ranked."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_dir(root: str | None = None) -> Path:
    path = Path(root) if root else DEFAULT_RECEIPT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths(receipt_id: str, root: str | None = None) -> dict[str, Path]:
    if not _ID_RE.match(receipt_id):
        raise ValueError("receipt_id must look like br-YYYYMMDD-HHMMSS-xxxxxxxx")
    base = _receipt_dir(root)
    return {
        "request": base / f"{receipt_id}.request.md",
        "receipt": base / f"{receipt_id}.receipt.json",
        "events": base / f"{receipt_id}.events.jsonl",
        "ledger": base / "ledger.jsonl",
    }


def _new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"br-{stamp}-{uuid.uuid4().hex[:8]}"


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, inner in value.items():
            redacted[key] = "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else _redact(inner)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


def _git(repo: str, args: list[str], timeout_s: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=env,
    )


def _repo_state(repo: str) -> dict:
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo must be a non-empty path")
    repo_path = str(Path(repo).resolve())
    inside = _git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise ValueError(f"repo is not a git worktree: {repo}")
    head = _git(repo_path, ["rev-parse", "HEAD"]).stdout.strip()
    branch = _git(repo_path, ["branch", "--show-current"]).stdout.strip()
    status = _git(repo_path, ["status", "--porcelain=v1"]).stdout.splitlines()
    entries = []
    for line in status:
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        entries.append({"status": line[:2].strip(), "path": path})
    return {
        "path": repo_path,
        "branch": branch,
        "head": head,
        "dirty": bool(entries),
        "entries": entries,
        "changed_files": [entry["path"] for entry in entries],
    }


def _commit_range(before: str, after: str, repo: str) -> list[str]:
    if not before or not after or before == after:
        return []
    proc = _git(repo, ["rev-list", "--reverse", f"{before}..{after}"])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []


def _event(paths: dict[str, Path], receipt_id: str, kind: str, data: dict) -> None:
    row = _redact({
        "receipt_id": receipt_id,
        "event": kind,
        "utc": _utc_now(),
        **data,
    })
    with paths["events"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _write_projection(paths: dict[str, Path], projection: dict) -> dict:
    safe = _redact(projection)
    paths["receipt"].write_text(json.dumps(safe, indent=2, sort_keys=True), encoding="utf-8")
    with paths["ledger"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(safe, sort_keys=True) + "\n")
    return safe


def _load_projection(paths: dict[str, Path]) -> dict:
    if not paths["receipt"].is_file():
        raise ValueError(f"receipt not found: {paths['receipt']}")
    return json.loads(paths["receipt"].read_text(encoding="utf-8"))


def _select_backend(backend: str | None, task: str | None) -> tuple[Optional[str], str, dict]:
    try:
        chosen, reason, occupancy = select_backend(load_pool(), backend=backend, task=task)
    except BackendConfigError as exc:
        raise ValueError(f"backend routing failed: {exc}") from exc
    return chosen.name, reason, occupancy


def _validate_criteria(status: str, validation: list[dict] | None,
                       criteria: list[str]) -> list[dict]:
    rows = list(validation or [])
    known = {str(row.get("criterion", "")): row for row in rows}
    for criterion in criteria:
        if criterion not in known:
            rows.append({"criterion": criterion, "status": "not_run", "evidence": ""})
    if status == "done":
        missing = [
            row.get("criterion", "")
            for row in rows
            if row.get("status") != "passed" or not str(row.get("evidence", "")).strip()
        ]
        if missing:
            raise ValueError("done requires passed validation evidence for every criterion: "
                             + "; ".join(missing))
    return rows


def _validate_deliverables(deliverables, where: str = "deliverables") -> Optional[list[str]]:
    """Return ``deliverables`` as a list of relative glob strings, or None.

    Delegates the traversal rules to ``task_expectations.validate_requires`` - the
    same validator ``submit_task(requires=...)`` applies - and adds two bounds of
    its own (see MAX_DELIVERABLES). Raises ValueError; never returns a partially
    accepted list.
    """
    if deliverables is None:
        return None
    if not isinstance(deliverables, list) or not deliverables:
        raise ValueError(f"{where} must be a non-empty list of relative glob strings")
    if len(deliverables) > MAX_DELIVERABLES:
        raise ValueError(f"{where} must hold at most {MAX_DELIVERABLES} patterns")
    for i, item in enumerate(deliverables):
        if isinstance(item, str) and _BACKSLASH_RE.search(item):
            raise ValueError(
                f"{where}[{i}] must use '/' separators: a deliverable glob is matched "
                f"against git's POSIX paths, so a backslash can never match")
    return validate_requires(deliverables, where=where)


def _resolve_repo(repo: str | None) -> str:
    """The repo path for a receipt: inside the HEARTH_SCOPE roots, and a directory.

    ``None`` means the primary sandbox root (``scope_root()``). Anything else is
    resolved by the scope authority (``resolve_in_scope`` -> ``in_any_scope``),
    which refuses '..' and anything that lands outside every root, symlinks and
    junctions followed. This retires the old ``C:\\work\\comfy`` default, a path
    that no longer exists.
    """
    if repo is None:
        resolved = scope_root()
    else:
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError("repo must be a non-empty path")
        resolved = resolve_in_scope(repo)
    if not resolved.is_dir():
        raise ValueError(f"repo is not an existing directory: {resolved}")
    return str(resolved)


def _callable_or(value, default: Callable, where: str) -> Callable:
    """Injection seam. ``None`` means the real collaborator.

    Annotated ``object | None`` at the tool boundary rather than ``Callable``
    on purpose: FastMCP builds each tool's JSON schema from its signature and
    pydantic cannot render a CallableSchema (verified: PydanticInvalidForJsonSchema),
    so a Callable annotation would refuse to register the tool at all. The type is
    enforced here instead of by the schema.
    """
    if value is None:
        return default
    if not callable(value):
        raise ValueError(f"{where} must be callable when provided")
    return value


def _request_body(paths: dict[str, Path]) -> str:
    """The authored request text, read back out of the immutable ``*.request.md``.

    Sliced between the ``## Request`` and ``## Acceptance Criteria`` markers
    ``create_build_request`` wrote, so the brief carries the author's words and
    NOT the receipt header (which holds this box's absolute repo path). A receipt
    whose markdown has no such section cannot be delegated - refused loudly rather
    than falling back to a body that would leak the header.
    """
    text = paths["request"].read_text(encoding="utf-8")
    start = text.find("## Request")
    end = text.find("## Acceptance Criteria")
    if start < 0 or end < 0 or end <= start:
        raise ValueError(
            f"request markdown has no '## Request' section to delegate: {paths['request']}")
    return text[start + len("## Request"):end].strip()


def _delegation_brief(projection: dict, request_body: str, deliverables: list[str]) -> str:
    """Render the self-contained fleet brief for one receipt (pure).

    Mirrors campaign/mechnet_exerciser.py's brief discipline: read-only source at
    ~/commandcenter-src, cite real paths, one bounded deliverable set, commit only
    that. The repository is named by its DIRECTORY NAME only - a fleet worker has
    no C: drive, and the brief travels off this box.
    """
    repo_hint = Path(projection.get("repo") or "").name or "the repository"
    criteria = [c for c in (projection.get("acceptance_criteria") or [])]
    brief = "\n".join([
        DELEGATION_PREAMBLE,
        f"# {projection.get('title', '')}",
        "",
        request_body,
        "",
        "## Acceptance criteria",
        "",
        *[f"{i}. {criterion}" for i, criterion in enumerate(criteria, 1)],
        "",
        "## Required deliverables",
        "",
        *[f"- {glob}" for glob in deliverables],
        "",
        "## Repository",
        "",
        f"{repo_hint} - your read-only checkout is ~/commandcenter-src; refer to files "
        f"by repo-relative path only.",
        "",
        DELEGATION_POSTAMBLE.format(deliverables=", ".join(deliverables)),
    ])
    leak = _WINDOWS_PATH_RE.search(brief)
    if leak:
        raise ValueError(
            f"the delegated brief would carry a Windows absolute path ({leak.group(0)!r}); "
            f"author the request with repo-relative paths - the fleet worker only has "
            f"~/commandcenter-src")
    return brief


def create_build_request(title: str, request: str,
                         acceptance_criteria: list[str],
                         repo: str | None = None,
                         lane: str = "hearth",
                         backend: str | None = None,
                         task: str | None = None,
                         execute: bool = False,
                         receipt_dir: str | None = None,
                         deliverables: list[str] | None = None) -> dict:
    """Create a Hearth build-request receipt and optionally mark it running.

    The original request and acceptance criteria are written once to
    ``*.request.md`` and never mutated. Backend selection is recorded at create
    time, honoring an explicit backend pin when supplied.

    ``repo`` defaults to the primary HEARTH_SCOPE root and must resolve inside the
    sandbox roots and exist as a directory (the old ``C:\\work\\comfy`` default was
    retired with the comfy tree). ``deliverables`` are the relative glob patterns a
    delegated build MUST produce; they are validated with the task lane's own
    ``requires`` rules and are what ``mode="delegate"`` sends to the fleet and what
    the acceptance check matches a winning lap against.
    """
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not request.strip():
        raise ValueError("request must be non-empty")
    if not acceptance_criteria or not all(isinstance(c, str) and c.strip()
                                         for c in acceptance_criteria):
        raise ValueError("acceptance_criteria must be a non-empty list of strings")
    deliverable_globs = _validate_deliverables(deliverables)
    repo_before = _repo_state(_resolve_repo(repo))
    backend_name, routed_by, occupancy = _select_backend(backend, task)
    receipt_id = _new_id()
    paths = _paths(receipt_id, receipt_dir)
    created = _utc_now()
    request_body = "\n".join([
        f"# {title}",
        "",
        f"Receipt: {receipt_id}",
        f"Lane: {lane}",
        f"Backend: {backend_name or ''}",
        f"Routed by: {routed_by}",
        f"Repo: {repo_before['path']}",
        f"Branch: {repo_before['branch']}",
        f"Head: {repo_before['head']}",
        f"Created UTC: {created}",
        "",
        "## Request",
        "",
        request,
        "",
        "## Acceptance Criteria",
        "",
        *[f"- {criterion}" for criterion in acceptance_criteria],
        "",
    ])
    paths["request"].write_text(_redact(request_body), encoding="utf-8")
    projection = {
        "schema_version": 2,
        "receipt_id": receipt_id,
        "id": receipt_id,
        "title": title,
        "lane": lane,
        "status": "running" if execute else "open",
        "summary": "",
        "repo": repo_before["path"],
        "backend": backend_name,
        "routing_reason": routed_by,
        "task": task,
        "occupancy": occupancy,
        "acceptance_criteria": list(acceptance_criteria),
        "deliverables": list(deliverable_globs or []),
        "created_utc": created,
        "updated_utc": created,
        "repo_before": repo_before,
        "repo_after": None,
        "pre_existing_dirty_files": repo_before["changed_files"],
        "changed_files": [],
        "request_changed_files": [],
        "commits": [],
        "validation": [
            {"criterion": criterion, "status": "not_run", "evidence": ""}
            for criterion in acceptance_criteria
        ],
        "execution": {
            "backend": backend_name,
            "routing_reason": routed_by,
            "tool_calls": [],
            "evidence": [],
            "result": None,
        },
        "request_path": str(paths["request"]),
        "receipt_path": str(paths["receipt"]),
        "events_path": str(paths["events"]),
        "ledger_path": str(paths["ledger"]),
    }
    _event(paths, receipt_id, "created", {
        "title": title,
        "lane": lane,
        "backend": backend_name,
        "routing_reason": routed_by,
        "repo_before": repo_before,
        "deliverables": list(deliverable_globs or []),
    })
    if execute:
        _event(paths, receipt_id, "execution_started", {
            "backend": backend_name,
            "routing_reason": routed_by,
            "note": "receipt opened for agent-driven or delegated execution",
        })
    return _write_projection(paths, projection)


def get_build_request(receipt_id: str, receipt_dir: str | None = None) -> dict:
    """Return the current projection for one build-request receipt."""
    return _load_projection(_paths(receipt_id, receipt_dir))


def list_build_requests(status: str | None = None, limit: int = 50,
                        receipt_dir: str | None = None) -> dict:
    """List build-request receipt projections, newest first."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if status is not None and status not in OPEN_STATUSES:
        raise ValueError(f"status must be one of {sorted(OPEN_STATUSES)}")
    root = _receipt_dir(receipt_dir)
    rows = []
    for path in sorted(root.glob("*.receipt.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if status is None or item.get("status") == status:
            rows.append(item)
        if len(rows) >= limit:
            break
    return {"ok": True, "count": len(rows), "requests": rows}


def _delegation_of(projection: dict) -> Optional[dict]:
    record = (projection.get("execution") or {}).get("delegation")
    return record if isinstance(record, dict) else None


def _observed_state(observed) -> str:
    """Collapse a ``task_status`` result to one of three decision-relevant states.

    ``unreachable`` is deliberately NOT the same as ``pending``: an SSH failure is
    absence of observation, not evidence the run is still going, and the two must
    be distinguishable in the receipt.
    """
    if not isinstance(observed, dict) or not observed.get("ok"):
        return DELEGATION_UNREACHABLE
    return DELEGATION_DONE if observed.get("done") else DELEGATION_PENDING


def _apply_deliverable_evidence(projection: dict, deliverables: list[str],
                                branch: str) -> list[str]:
    """Pass ONLY the acceptance criteria that name a deliverable glob verbatim.

    Deliverable presence is evidence for exactly one claim: "the file the criterion
    names exists on the winning branch". A criterion that says nothing about a
    deliverable is left ``not_run`` - a human or agent must still supply its
    evidence, and ``close_build_request(status="done")`` keeps refusing until they
    do. Returns the criteria that were passed.
    """
    passed: list[str] = []
    for row in projection.get("validation") or []:
        criterion = str(row.get("criterion", ""))
        matched = [glob for glob in deliverables if glob and glob in criterion]
        if not matched:
            continue
        if row.get("status") == "passed" and str(row.get("evidence", "")).strip():
            # A human already evidenced this one; "the file exists" is the weaker
            # claim, so it does not overwrite the stronger record.
            continue
        row["status"] = "passed"
        row["evidence"] = f"{branch}: {', '.join(matched)} matched"
        passed.append(criterion)
    return passed


def _complete_delegation(paths: dict[str, Path], receipt_id: str, projection: dict,
                         delegation: dict, result: str, winner, blocked: bool,
                         evidence: str | None = None) -> dict:
    """Terminate one delegation: one ``delegation_completed`` event, one write."""
    now = _utc_now()
    delegation["state"] = DELEGATION_COMPLETED
    delegation["completed_at"] = now
    delegation["result"] = result
    if evidence:
        projection["execution"]["evidence"].append(_redact(evidence))
    if blocked:
        projection["status"] = "blocked"
    projection["updated_utc"] = now
    _event(paths, receipt_id, "delegation_completed", {
        "plan_id": delegation.get("plan_id"),
        "result": result,
        "winner_present": bool(winner),
        "winner": winner,
        "missing_globs": list(delegation.get("missing_globs") or []),
        "harvested": bool(delegation.get("harvested")),
        "branches_count": len(delegation.get("branches") or []),
    })
    written = _write_projection(paths, projection)
    return {**written, "delegation_result": result}


def _sync_delegation(paths: dict[str, Path], receipt_id: str, projection: dict,
                     status_fn: object | None, harvest_fn: object | None,
                     list_files_fn: object | None) -> dict:
    """One poll of a delegated receipt. Never marks ``done``; never dispatches."""
    delegation = _delegation_of(projection)
    if delegation is None:
        raise ValueError("sync_delegation requires a delegated receipt: call "
                         "execute_build_request(mode='delegate') first")
    # Checked BEFORE the closed-receipt guard: the terminal results below set the
    # receipt to `blocked`, which is a final status, so the "already synced" answer
    # has to come first or a cadence caller would get an exception instead.
    if delegation.get("state") == DELEGATION_COMPLETED:
        return {**projection, "already_synced": True}
    current_status = projection.get("status", "open")
    if current_status in FINAL_STATUSES:
        raise ValueError(f"receipt is already closed as {current_status}")
    plan_id = delegation.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("delegation record carries no plan_id")
    deliverables = [g for g in (delegation.get("requires") or []) if isinstance(g, str)]
    if not deliverables:
        raise ValueError("delegation record carries no required deliverables; "
                         "acceptance cannot be judged mechanically")

    # Every seam is type-checked BEFORE the first side effect, so a bad argument
    # can never leave a half-synced receipt behind.
    observe = _callable_or(status_fn, task_lane.task_status, "status_fn")
    harvest = _callable_or(harvest_fn, fleet_harvest.harvest_fleet_run, "harvest_fn")
    if list_files_fn is not None and not callable(list_files_fn):
        raise ValueError("list_files_fn must be callable when provided")

    observed = observe(plan_id)
    state = _observed_state(observed)
    now = _utc_now()
    delegation["last_sync"] = now

    if state != DELEGATION_DONE:
        # The `delegated` event already established "submitted, not yet done", so
        # the FIRST pending observation is not a change - only a real transition
        # earns an event. That is what keeps a 30s polling cadence from writing a
        # line per poll for hours.
        previous = delegation.get("last_status") or DELEGATION_PENDING
        delegation["last_status"] = state
        changed = state != previous
        if changed:
            _event(paths, receipt_id, "delegation_synced", {
                "plan_id": plan_id, "status": state, "previous": previous,
                "error": observed.get("error") if isinstance(observed, dict) else None,
            })
        projection["updated_utc"] = now
        written = _write_projection(paths, projection)
        return {**written, "delegation_status_changed": changed,
                "delegation_last_status": state}

    delegation["last_status"] = DELEGATION_DONE
    payload = observed.get("result") if isinstance(observed.get("result"), dict) else {}
    winner = payload.get("winner")

    if delegation.get("promotion_policy") == "manual":
        # Review-only work stays on the local farmer. Legacy harvest may push to
        # GitHub, so it is never entered for this policy, including sync replay.
        promotion = payload.get("promotion") or {}
        if promotion.get("promoted") is not False or promotion.get("status") != "awaiting_review":
            raise RuntimeError("manual promotion policy was not confirmed by conductor result")
        delegation["candidates"] = promotion.get("candidates", [])
        delegation["promotion"] = promotion
        delegation["state"] = DELEGATION_COMPLETED
        delegation["result"] = "awaiting_review"
        delegation["completed_at"] = now
        projection["updated_utc"] = now
        _event(paths, receipt_id, "delegation_awaiting_review", {
            "plan_id": plan_id, "promotion": promotion, "harvested": False})
        return _write_projection(paths, projection)

    # (a) Harvest exactly once, PERSIST-FIRST. The flag reaches disk before the
    # side effect, so a crash inside harvest_fn (or a second concurrent sync that
    # re-reads the projection afterwards) can never harvest twice. The honest
    # residual: two syncs that both LOAD the projection before either writes can
    # still both harvest - the window is the read-to-write gap, and the gateway is
    # a single writer today. A crashed harvest is not retried automatically; it
    # lands as `harvest_incomplete` for a human, because silently re-running a
    # failing fetch/push on a cadence is how you hammer a remote.
    if not delegation.get("harvested"):
        delegation["harvested"] = True
        delegation["harvest_attempted_at"] = now
        projection["updated_utc"] = now
        _write_projection(paths, projection)
        harvested = harvest(plan_id)
        workers = (harvested or {}).get("workers") or []
        delegation["harvest"] = {
            "ok": bool((harvested or {}).get("ok")),
            "count": (harvested or {}).get("count", len(workers)),
            "error": (harvested or {}).get("error"),
            "note": (harvested or {}).get("note"),
        }
        delegation["branches"] = [w.get("github_branch") for w in workers
                                  if isinstance(w, dict) and w.get("github_branch")]

    record = delegation.get("harvest")
    if not isinstance(record, dict):
        return _complete_delegation(
            paths, receipt_id, projection, delegation, RESULT_HARVEST_INCOMPLETE,
            winner, blocked=True,
            evidence=f"harvest of {plan_id} was started but never recorded an outcome; "
                     f"it is not retried automatically")
    if not record.get("ok"):
        return _complete_delegation(
            paths, receipt_id, projection, delegation, RESULT_HARVEST_FAILED,
            winner, blocked=True,
            evidence=f"harvest of {plan_id} failed: {record.get('error')}")

    branches = list(delegation.get("branches") or [])
    # (c) No winner still gets its harvest: the branches are exactly what a human
    # needs in order to curate a run the assay could not crown, and the harvest is
    # an idempotent non-force mirror.
    if not winner:
        return _complete_delegation(
            paths, receipt_id, projection, delegation, RESULT_NO_WINNER, winner,
            blocked=True,
            evidence=f"run {plan_id} finished with no winner; "
                     f"{len(branches)} branch(es) harvested for curation")
    expected = f"{FLEET_BRANCH_PREFIX}/{plan_id}/{winner}/{FLEET_LAP}"
    if expected not in branches:
        return _complete_delegation(
            paths, receipt_id, projection, delegation, RESULT_WINNER_BRANCH_MISSING,
            winner, blocked=True,
            evidence=f"winner {winner!r} has no harvested branch {expected}; "
                     f"harvested: {branches}")

    # (b) Acceptance against the winning lap.
    branch = ORIGIN_PREFIX + expected
    delegation["winner"] = winner
    delegation["winner_branch"] = branch
    acceptance = check_lap_acceptance(branch, deliverables, list_files_fn)
    if not acceptance.get("passed"):
        delegation["missing_globs"] = list(acceptance.get("missing_globs") or [])
        return _complete_delegation(
            paths, receipt_id, projection, delegation, RESULT_ACCEPTANCE_FAILED,
            winner, blocked=True,
            evidence=f"{branch} is missing required deliverables: "
                     f"{delegation['missing_globs']}")
    delegation["missing_globs"] = []
    passed = _apply_deliverable_evidence(projection, deliverables, branch)
    delegation["criteria_passed"] = passed
    return _complete_delegation(
        paths, receipt_id, projection, delegation, RESULT_ACCEPTED, winner,
        blocked=False,
        evidence=f"{branch} carries every required deliverable; "
                 f"{len(passed)} criterion/criteria passed on deliverable presence")


def update_build_request(receipt_id: str, status: str | None = None,
                         summary: str | None = None,
                         validation: list[dict] | None = None,
                         tool_call: dict | None = None,
                         evidence: str | None = None,
                         receipt_dir: str | None = None,
                         sync_delegation: bool = False,
                         status_fn: object | None = None,
                         harvest_fn: object | None = None,
                         list_files_fn: object | None = None) -> dict:
    """Append an update event and refresh the build-request projection.

    ``sync_delegation=True`` turns this call into one poll of a delegated receipt
    instead of a manual edit (the other arguments are then unused): it reads the
    run's state, and once the run is done it harvests the fleet branches exactly
    once and checks the winning lap against the receipt's required deliverables.

    What one sync can do, and nothing more:
      * still running -> refresh ``delegation.last_sync``/``last_status``; an event
        (``delegation_synced``) only when the observed state actually CHANGED.
      * done -> harvest once (persist-first), then one terminal
        ``delegation_completed`` event carrying ``accepted`` |
        ``acceptance_failed`` | ``no_winner`` | ``winner_branch_missing`` |
        ``harvest_failed`` | ``harvest_incomplete``. Every result but ``accepted``
        blocks the receipt.
      * a later sync -> ``already_synced: True``, no calls, no events, no writes.

    Acceptance passes ONLY the criteria whose text names a required deliverable
    verbatim; everything else stays ``not_run``, so closing ``done`` still needs a
    human's evidence. This tool never marks a receipt ``done`` and never dispatches
    anything on a cadence - a caller drives every poll.

    ``status_fn``/``harvest_fn``/``list_files_fn`` are test seams defaulting to
    ``task_lane.task_status``, ``fleet_harvest.harvest_fleet_run`` and the real
    ``git ls-tree``; they are typed ``object`` because a Callable annotation cannot
    be rendered as a JSON schema (see ``_callable_or``).
    """
    paths = _paths(receipt_id, receipt_dir)
    projection = _load_projection(paths)
    if sync_delegation:
        return _sync_delegation(paths, receipt_id, projection,
                                status_fn, harvest_fn, list_files_fn)
    current_status = projection.get("status", "open")
    if current_status in FINAL_STATUSES:
        raise ValueError(f"receipt is already closed as {current_status}")
    if status is not None:
        if status not in {"open", "running", "blocked", "failed", "cancelled"}:
            raise ValueError("update status must be open, running, blocked, failed, or cancelled")
        projection["status"] = status
    if summary is not None:
        projection["summary"] = summary
    if validation is not None:
        projection["validation"] = _validate_criteria(
            projection["status"], validation, projection["acceptance_criteria"])
    if tool_call is not None:
        projection["execution"]["tool_calls"].append(_redact(tool_call))
    if evidence is not None:
        projection["execution"]["evidence"].append(_redact(evidence))
    projection["updated_utc"] = _utc_now()
    _event(paths, receipt_id, "updated", {
        "status": projection["status"],
        "summary": summary,
        "validation": validation,
        "tool_call": tool_call,
        "evidence": evidence,
    })
    return _write_projection(paths, projection)


def _delegate_build_request(paths: dict[str, Path], receipt_id: str, projection: dict,
                            backend: str | None, task: str | None, evidence: str,
                            builders: list[str] | None, max_age_s: int | None,
                            task_class: str, submit_fn: object | None,
                            run_policy: dict | None = None) -> dict:
    """Hand one receipt to the fleet task lane. Exactly one delegation per receipt.

    Everything that can refuse - a second delegation, missing/invalid deliverables,
    a bad lifetime, a brief that would carry a Windows path - refuses BEFORE the
    submit call and before any projection write, so a refused delegate leaves the
    receipt byte-identical.
    """
    if _delegation_of(projection) is not None:
        # No submit, no event, no write: a receipt is delegated once, and a retry
        # must not fan a second run out across the fleet.
        return {**projection, "duplicate_delegation": True}
    deliverables = _validate_deliverables(projection.get("deliverables") or None)
    if not deliverables:
        raise ValueError(
            "mode='delegate' requires deliverables on the receipt: a delegated build "
            "with no required deliverables cannot be accepted mechanically")
    if builders is not None and (not isinstance(builders, list) or not builders
                                 or not all(isinstance(b, str) and b.strip()
                                            for b in builders)):
        raise ValueError("builders must be a non-empty list of non-empty strings")
    if not isinstance(task_class, str) or not task_class.strip():
        raise ValueError("task_class must be a non-empty string")
    lifetime = validate_max_age_s(max_age_s)
    submit = _callable_or(submit_fn, task_lane.submit_task, "submit_fn")
    backend_name, routed_by, occupancy = _select_backend(
        backend or projection.get("backend"), task or projection.get("task"))

    prompt = _delegation_brief(projection, _request_body(paths), deliverables)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    # The receipt id is already constrained to [a-z0-9-] by _ID_RE, so it is a safe
    # plan_id hint as-is: the run on the conductor names the receipt that ordered it.
    plan_id_hint = receipt_id

    error: Optional[str] = None
    result: dict = {}
    try:
        raw = submit(prompt, builders=builders, plan_id_hint=plan_id_hint,
                     task_class=task_class, requires=deliverables, max_age_s=lifetime, **(run_policy or {}))
        result = raw if isinstance(raw, dict) else {}
        if not result.get("ok"):
            error = str(result.get("error") or "submit_task returned ok:false")
        elif not result.get("plan_id"):
            error = "submit_task returned ok:true with no plan_id"
    except Exception as exc:  # a raising task lane is a failed delegation, not a crash
        error = f"{type(exc).__name__}: {exc}"

    now = _utc_now()
    projection["backend"] = backend_name
    projection["routing_reason"] = routed_by
    projection["occupancy"] = occupancy
    projection["execution"]["backend"] = backend_name
    projection["execution"]["routing_reason"] = routed_by
    projection["execution"]["tool_calls"].append({
        "tool": "execute_build_request", "mode": "delegate",
        "backend": backend_name, "routing_reason": routed_by,
        "ok": error is None,
    })
    projection["updated_utc"] = now

    if error is not None:
        # Loud, not silent: the receipt is blocked and the error is on the record.
        # No delegation record is written, so a fixed lane can delegate again.
        projection["status"] = "blocked"
        projection["execution"]["evidence"].append(_redact(f"delegation failed: {error}"))
        _event(paths, receipt_id, "delegation_failed", {
            "error": error, "task_class": task_class, "requires": deliverables,
            "max_age_s": lifetime, "prompt_sha256": prompt_sha256,
            "plan_id": result.get("plan_id"),
        })
        return {**_write_projection(paths, projection), "delegation_failed": True,
                "error": _redact(error)}

    delegation = {
        **(run_policy or {}),
        "plan_id": result["plan_id"],
        "builders": result.get("builders") or builders,
        "requires": deliverables,
        "max_age_s": lifetime,
        "task_class": task_class,
        "submitted_at": now,
        "state": DELEGATION_SUBMITTED,
        "harvested": False,
        "completed_at": None,
        "result": None,
    }
    projection["status"] = "running"
    projection["execution"]["delegation"] = delegation
    projection["execution"]["evidence"].append(
        _redact(evidence or f"delegated to the fleet task lane as {result['plan_id']}"))
    # The prompt body never enters the event log - the receipt already holds the
    # authored request, and a sha256 is enough to prove WHICH brief was sent.
    _event(paths, receipt_id, "delegated", {**delegation, "backend": backend_name,
                                            "routing_reason": routed_by,
                                            "prompt_sha256": prompt_sha256})
    return _write_projection(paths, projection)


def execute_build_request(receipt_id: str, mode: str = "manual",
                          backend: str | None = None,
                          task: str | None = None,
                          evidence: str = "",
                          receipt_dir: str | None = None,
                          builders: list[str] | None = None,
                          max_age_s: int | None = None,
                          task_class: str = "build",
                          submit_fn: object | None = None,
                          promotion_policy: str | None = None, runner_preset: str | None = None,
                          operator: str | None = None) -> dict:
    """Record execution start, or actually delegate the request to the fleet.

    ``manual``/``agent`` record that the caller is doing the work: backend
    selection and provenance land on the receipt and nothing is dispatched.

    ``delegate`` DOES dispatch. It renders the receipt as a self-contained fleet
    brief (read-only source at ~/commandcenter-src, cite real paths, commit only
    the deliverables), submits it through ``task_lane.submit_task`` with the
    receipt's ``deliverables`` as ``requires`` and ``max_age_s`` as the declared
    lifetime, and records the resulting ``plan_id`` on the receipt. It requires
    deliverables (an unmechanizable acceptance is not delegable), happens exactly
    ONCE per receipt (a second call returns ``duplicate_delegation`` and submits
    nothing), and on failure blocks the receipt with the error rather than leaving
    a half-written delegation. Poll it with
    ``update_build_request(..., sync_delegation=True)``.

    ``submit_fn`` is a test seam defaulting to ``task_lane.submit_task``; it is
    typed ``object`` because a Callable annotation cannot be rendered as a JSON
    schema (see ``_callable_or``). This tool never marks work done.
    """
    if mode not in {"manual", "agent", "delegate"}:
        raise ValueError("mode must be manual, agent, or delegate")
    paths = _paths(receipt_id, receipt_dir)
    projection = _load_projection(paths)
    if projection.get("status") in FINAL_STATUSES:
        return {**projection, "duplicate_execution": True}
    if mode == "delegate":
        run_policy = {k: v for k, v in (("promotion_policy", promotion_policy),
                      ("runner_preset", runner_preset), ("operator", operator)) if v is not None}
        return _delegate_build_request(paths, receipt_id, projection, backend, task,
                                       evidence, builders, max_age_s, task_class,
                                       submit_fn, run_policy)
    backend_name, routed_by, occupancy = _select_backend(
        backend or projection.get("backend"), task or projection.get("task"))
    projection["status"] = "running"
    projection["backend"] = backend_name
    projection["routing_reason"] = routed_by
    projection["occupancy"] = occupancy
    projection["execution"]["backend"] = backend_name
    projection["execution"]["routing_reason"] = routed_by
    projection["execution"]["evidence"].append(_redact(evidence or f"execution mode={mode}"))
    projection["execution"]["tool_calls"].append({
        "tool": "execute_build_request",
        "mode": mode,
        "backend": backend_name,
        "routing_reason": routed_by,
    })
    projection["updated_utc"] = _utc_now()
    _event(paths, receipt_id, "execution_started", {
        "mode": mode,
        "backend": backend_name,
        "routing_reason": routed_by,
        "occupancy": occupancy,
        "evidence": evidence,
    })
    return _write_projection(paths, projection)


def close_build_request(receipt_id: str, status: str,
                        summary: str,
                        validation: list[dict],
                        commits: list[str] | None = None,
                        changed_files: list[str] | None = None,
                        receipt_dir: str | None = None) -> dict:
    """Close a build-request receipt with validation evidence and repo state.

    ``status='done'`` is refused unless every acceptance criterion has a
    ``passed`` validation row with evidence. Re-closing an already-final receipt
    returns the existing projection with ``duplicate_close`` instead of adding a
    second closure event.
    """
    if status not in FINAL_STATUSES:
        raise ValueError(f"status must be one of {sorted(FINAL_STATUSES)}")
    if not summary.strip():
        raise ValueError("summary must be non-empty")
    paths = _paths(receipt_id, receipt_dir)
    projection = _load_projection(paths)
    if projection.get("status") in FINAL_STATUSES:
        return {**projection, "duplicate_close": True}
    rows = _validate_criteria(status, validation, projection["acceptance_criteria"])
    repo_after = _repo_state(projection["repo"])
    repo_before = projection["repo_before"]
    detected_commits = _commit_range(repo_before.get("head"), repo_after.get("head"), projection["repo"])
    before_dirty = set(projection.get("pre_existing_dirty_files") or [])
    after_dirty = set(repo_after["changed_files"])
    request_changed = sorted(after_dirty - before_dirty)
    final_changed = sorted(set(changed_files or []) | set(request_changed))
    final_commits = list(dict.fromkeys([*(commits or []), *detected_commits]))

    projection["status"] = status
    projection["summary"] = summary
    projection["validation"] = rows
    projection["repo_after"] = repo_after
    projection["changed_files"] = final_changed
    projection["request_changed_files"] = final_changed
    projection["commits"] = final_commits
    projection["updated_utc"] = _utc_now()
    projection["execution"]["result"] = status
    _event(paths, receipt_id, "closed", {
        "status": status,
        "summary": summary,
        "validation": rows,
        "repo_after": repo_after,
        "commits": final_commits,
        "request_changed_files": final_changed,
    })
    return _write_projection(paths, projection)


def get_tools() -> list[Callable]:
    return [
        create_build_request,
        get_build_request,
        list_build_requests,
        update_build_request,
        execute_build_request,
        close_build_request,
    ]
