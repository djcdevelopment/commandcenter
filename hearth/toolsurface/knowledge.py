"""HEARTH tool provider: knowledge store (Stream H-B).

The ONLY writers to knowledge/ go through here — and they are thin wrappers over the
EXISTING workflow-ontology machinery (tools/workflow/*), imported directly, not
reimplemented. Validation, corpus-regression guard, and fixture-taint guard all keep
running exactly as they do today because we call the same materialize_* entry points
the projector CLIs call.

Sandboxed to HEARTH_SCOPE: event sources, the events ledger, and the knowledge out-dir
all resolve inside the sandbox.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hearth.toolsurface._scope import resolve_in_scope
from hearth.projection.capacity import DEFAULT_LEDGER as _CAPACITY_DEFAULT_LEDGER
from hearth.projection.capacity import build_capacity_document

# The workflow machinery lives at the repo root this module ships in. Make it importable
# no matter what cwd the gateway runs from (mirrors how tests/ relies on repo-root cwd).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.workflow.append_event import append_event  # noqa: E402
from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor  # noqa: E402
from tools.workflow.project_associations import materialize_associations  # noqa: E402
from tools.workflow.project_capacity import collect_event_files, materialize_knowledge  # noqa: E402
from tools.workflow.project_coverage import materialize_coverage  # noqa: E402
from tools.workflow.project_experiments import materialize_experiments  # noqa: E402
from tools.workflow.project_findings import materialize_findings  # noqa: E402
from tools.workflow.project_policy import materialize_policy  # noqa: E402
from tools.workflow.validate_events import ValidationError  # noqa: E402

DEFAULT_EVENTS_PATH = "runs/hearth/events.jsonl"
DEFAULT_SOURCES = ["runs"]
DEFAULT_OUT = "knowledge"

# Projection order matters: policy consumes the findings.json materialized upstream.
_PROJECTION_KINDS = ("capacity", "findings", "associations", "coverage", "experiments", "policy")

# Keys worth echoing back per materialized document — a digest, never the whole store.
_SUMMARY_KEYS = (
    "observation_count", "decision_count", "unresolved_refs", "finding_counts",
    "association_count", "capability_count", "requalification_due",
    "source_findings", "plan_count", "rule_counts", "gap_counts", "beliefs_changed",
)


def _mtime_iso(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _summarize(document: dict) -> dict:
    summary = {key: document[key] for key in _SUMMARY_KEYS if key in document}
    summary["contract_version"] = document.get("contract_version")
    return summary


def record_event(event: dict, events_path: str = DEFAULT_EVENTS_PATH) -> dict:
    """Validate a workflow event and append it to a sandboxed events.jsonl ledger."""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    target = resolve_in_scope(events_path)
    try:
        append_event(target, event)  # existing machinery: validates, then appends
    except ValidationError as exc:
        raise ValueError(f"invalid workflow event: {exc}") from exc
    return {
        "appended": True,
        "events_path": str(target),
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
    }


def project(kinds: list[str] | None = None, sources: list[str] | None = None,
            out: str = DEFAULT_OUT, allow_fixture_sources: bool = False) -> dict:
    """Run the existing knowledge projectors over event sources; per-kind ok/error summary."""
    requested = list(kinds) if kinds else ["all"]
    if "all" in requested:
        requested = list(_PROJECTION_KINDS)
    unknown = sorted(set(requested) - set(_PROJECTION_KINDS))
    if unknown:
        raise ValueError(f"unknown projection kind(s): {', '.join(unknown)} "
                         f"(valid: {', '.join(_PROJECTION_KINDS)}, or 'all')")

    source_paths = [resolve_in_scope(raw) for raw in (sources or DEFAULT_SOURCES)]
    out_dir = resolve_in_scope(out)
    event_files = collect_event_files(source_paths)
    # Same guard the projector CLIs run: fixture sources must not pour into repo knowledge/.
    check_fixture_taint(source_paths + event_files, out_dir, allow=allow_fixture_sources)

    # policy projects FROM findings.json, not from events — keep dependency order.
    ordered = [kind for kind in _PROJECTION_KINDS if kind in requested]
    runners = {
        "capacity": lambda: materialize_knowledge(event_files, out_dir),
        "findings": lambda: materialize_findings(event_files, out_dir),
        "associations": lambda: materialize_associations(event_files, out_dir),
        "coverage": lambda: materialize_coverage(event_files, out_dir),
        "experiments": lambda: materialize_experiments(event_files, out_dir),
        "policy": lambda: materialize_policy(out_dir / "findings.json", out_dir),
    }

    results: dict[str, dict] = {}
    for kind in ordered:
        try:
            output = runners[kind]()
        except Exception as exc:  # guard refusals and projector faults both belong in the digest
            results[kind] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            continue
        if kind in ("capacity", "associations", "experiments"):
            summary = {name: _summarize(doc) for name, doc in output.items()}
        elif kind == "policy":
            summary = _summarize(output["content"])
            summary["changes"] = len(output["changes"])
        else:
            summary = _summarize(output)
        results[kind] = {"ok": True, "summary": summary}
    return {
        "out": str(out_dir),
        "event_files": len(event_files),
        "kinds": results,
        "ok": all(entry["ok"] for entry in results.values()),
    }


def _query_knowledge_file(file_name: str, knowledge_dir: str) -> dict:
    path = resolve_in_scope(knowledge_dir) / file_name
    if not path.is_file():
        return {"available": False, "path": str(path)}
    return {
        "available": True,
        "path": str(path),
        "mtime": _mtime_iso(path),
        "content": json.loads(path.read_text(encoding="utf-8-sig")),
    }


def query_capabilities(knowledge_dir: str = DEFAULT_OUT) -> dict:
    """Return the materialized capabilities.json (with file mtime) from the sandbox."""
    return _query_knowledge_file("capabilities.json", knowledge_dir)


def query_findings(knowledge_dir: str = DEFAULT_OUT) -> dict:
    """Return the materialized findings.json (with file mtime) from the sandbox."""
    return _query_knowledge_file("findings.json", knowledge_dir)


def query_beliefs_summary(knowledge_dir: str = DEFAULT_OUT) -> dict:
    """Compact cross-file digest of the belief store: counts and mtimes, not full contents."""
    directory = resolve_in_scope(knowledge_dir)
    files = {
        "capabilities.json": ("capability_count",),
        "findings.json": ("observation_count", "finding_counts"),
        "capacity_estimates.json": ("observation_count",),
        "policy.json": ("rule_counts", "source_findings"),
        "known_good_models.json": (),
        "known_bad_models.json": (),
    }
    summary: dict[str, dict] = {}
    for file_name, keys in files.items():
        path = directory / file_name
        if not path.is_file():
            summary[file_name] = {"available": False}
            continue
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        entry: dict = {"available": True, "mtime": _mtime_iso(path),
                       "contract_version": document.get("contract_version")}
        for key in keys:
            if key in document:
                entry[key] = document[key]
        if "entries" in document:
            entry["entry_count"] = len(document["entries"])
        summary[file_name] = entry
    return {"knowledge_dir": str(directory), "files": summary}


def project_capacity_knowledge(
    ledger_path: str = str(_CAPACITY_DEFAULT_LEDGER), out: str = DEFAULT_OUT,
) -> dict:
    """Project the HEARTH ledger into knowledge/capacity.json (JS2 scheduler input).

    Buckets ledger events by (task_class, node, model, tool) and writes duration/
    token distributions per bucket. Read-only over the ledger; the only write is
    capacity.json inside the sandboxed knowledge dir.
    """
    ledger = resolve_in_scope(ledger_path)
    document = build_capacity_document(ledger)
    out_dir = resolve_in_scope(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "capacity.json"
    # Route through the same corpus regression guard every other knowledge projector uses
    # (CQRS/ES plan step 2 — closes the LIVE BLIND SPOT: this was a bare write_text bypassing
    # corpus_guard entirely). Guards on evidence_watermark + bucket_count.
    guard_write(target, document, make_extractor("bucket_count"))
    return {
        "path": str(target),
        "evidence_watermark": document["evidence_watermark"],
        "bucket_count": len(document["buckets"]),
    }


def query_capacity(knowledge_dir: str = DEFAULT_OUT) -> dict:
    """Return the materialized capacity.json (with file mtime) from the sandbox."""
    return _query_knowledge_file("capacity.json", knowledge_dir)


def get_tools() -> list[Callable]:
    return [
        record_event, project, query_capabilities, query_findings, query_beliefs_summary,
        project_capacity_knowledge, query_capacity,
    ]
