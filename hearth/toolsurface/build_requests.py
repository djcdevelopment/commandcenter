"""HEARTH tool provider: build-request receipts.

This is the Hearth-native control-plane lane for engineering work receipts. It
keeps the Comfy FieldLab receipt layout compatible with the existing PowerShell
scripts while exposing the lifecycle as MCP tools:

create -> inspect/list/update -> execute/delegate -> close.

The authored request is immutable. Updates and execution/closure records are
append-only in ``*.events.jsonl``; ``*.receipt.json`` is the current projection.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from hearth.toolsurface.backends import BackendConfigError, load_pool, select_backend

DEFAULT_RECEIPT_DIR = Path(os.environ.get(
    "HEARTH_BUILD_REQUEST_DIR",
    r"C:\work\comfy\fieldlab\runs\build-requests",
))
FINAL_STATUSES = {"done", "failed", "blocked", "cancelled"}
OPEN_STATUSES = {"open", "running", *FINAL_STATUSES}
SECRET_KEY_RE = re.compile(r"(token|secret|password|apikey|api_key|access[_-]?key|credential)", re.I)
SECRET_VALUE_RE = re.compile(
    r"(ya29\.[A-Za-z0-9._-]+|sk-[A-Za-z0-9._-]+|gh[pousr]_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+)",
)
_ID_RE = re.compile(r"^br-\d{8}-\d{6}-[a-f0-9]{8}$")


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


def create_build_request(title: str, request: str,
                         acceptance_criteria: list[str],
                         repo: str = r"C:\work\comfy",
                         lane: str = "hearth",
                         backend: str | None = None,
                         task: str | None = None,
                         execute: bool = False,
                         receipt_dir: str | None = None) -> dict:
    """Create a Hearth build-request receipt and optionally mark it running.

    The original request and acceptance criteria are written once to
    ``*.request.md`` and never mutated. Backend selection is recorded at create
    time, honoring an explicit backend pin when supplied.
    """
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not request.strip():
        raise ValueError("request must be non-empty")
    if not acceptance_criteria or not all(isinstance(c, str) and c.strip()
                                         for c in acceptance_criteria):
        raise ValueError("acceptance_criteria must be a non-empty list of strings")
    repo_before = _repo_state(repo)
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


def update_build_request(receipt_id: str, status: str | None = None,
                         summary: str | None = None,
                         validation: list[dict] | None = None,
                         tool_call: dict | None = None,
                         evidence: str | None = None,
                         receipt_dir: str | None = None) -> dict:
    """Append an update event and refresh the build-request projection."""
    paths = _paths(receipt_id, receipt_dir)
    projection = _load_projection(paths)
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


def execute_build_request(receipt_id: str, mode: str = "manual",
                          backend: str | None = None,
                          task: str | None = None,
                          evidence: str = "",
                          receipt_dir: str | None = None) -> dict:
    """Record execution start/delegation for a build request.

    ``mode`` may be ``manual`` or ``agent`` for work performed by the caller, or
    ``delegate`` to record that Hearth routed the request to a backend/task lane.
    This tool records backend selection and provenance; it does not silently mark
    work done.
    """
    if mode not in {"manual", "agent", "delegate"}:
        raise ValueError("mode must be manual, agent, or delegate")
    paths = _paths(receipt_id, receipt_dir)
    projection = _load_projection(paths)
    if projection.get("status") in FINAL_STATUSES:
        return {**projection, "duplicate_execution": True}
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
