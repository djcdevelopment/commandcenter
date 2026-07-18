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

import hashlib
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
from tools.workflow.corpus import Corpus  # noqa: E402
from tools.workflow.fsio import atomic_write_json  # noqa: E402
from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor  # noqa: E402
from tools.workflow.project_associations import materialize_associations  # noqa: E402
from tools.workflow.project_capacity import collect_event_files, materialize_knowledge  # noqa: E402
from tools.workflow.project_coverage import materialize_coverage  # noqa: E402
from tools.workflow.project_experiments import materialize_experiments  # noqa: E402
from tools.workflow.project_findings import materialize_findings  # noqa: E402
from tools.workflow.project_policy import materialize_policy  # noqa: E402
from tools.workflow.validate_events import ValidationError  # noqa: E402

from hearth.projection.economics import build_offload_document  # noqa: E402
from hearth.projection.rebuild import rebuild_knowledge as _rebuild_knowledge  # noqa: E402

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


def _aggregate_corpus(source_paths: list[Path]) -> Corpus:
    """Build one Corpus over every resolved source root (files or dirs).

    `sources` may name several roots (default is just ["runs"], but callers can pass
    more). Corpus.enumerate operates on a single root, so aggregate: union the event
    files, sum event counts, take the max watermark, and fold every root's digest
    input together (sorted, so order of `sources` doesn't matter) into one combined
    corpus_digest — this is the fingerprint of "everything project() actually read".
    """
    per_root = [Corpus.enumerate(path) for path in source_paths]
    all_event_files = tuple(sorted(
        {f for corpus in per_root for f in corpus.event_files},
        key=lambda p: p.as_posix(),
    ))
    total_events = sum(corpus.event_count for corpus in per_root)
    watermark = None
    for corpus in per_root:
        if corpus.watermark is not None and (watermark is None or corpus.watermark > watermark):
            watermark = corpus.watermark

    # Combine per-root digests deterministically. Absolute root paths must NOT enter
    # the hash (they vary per checkout/sandbox and would break digest stability over
    # identical content); each per-root digest is already content-shaped, so the sorted
    # set of digests is a stable fingerprint regardless of `sources` order or mount point.
    # A single root passes through unchanged, so it matches Corpus.enumerate exactly.
    if len(per_root) == 1:
        combined_digest = per_root[0].corpus_digest
    else:
        hasher = hashlib.sha256()
        for digest in sorted(corpus.corpus_digest for corpus in per_root):
            hasher.update(f"{digest}\n".encode("utf-8"))
        combined_digest = f"sha256:{hasher.hexdigest()}"

    return Corpus(
        root=source_paths[0] if len(source_paths) == 1 else Path(""),
        event_files=all_event_files,
        event_count=total_events,
        watermark=watermark,
        corpus_digest=combined_digest,
    )


def _restamp_written_file(path: Path, corpus: Corpus) -> None:
    """Re-write an already-materialized knowledge file with the two additive corpus keys.

    materialize_* functions (tools/workflow/project_*.py) build their own output dict
    and write it themselves (via guard_write or a bare write_text) before returning —
    their public signatures are out of scope for this change (plan step 4, task 2/3).
    The least invasive injection point is therefore here, one level up: read back what
    was just written, add corpus_digest/corpus_event_count, and rewrite. Only these two
    keys change, so this never re-triggers (or needs to re-satisfy) corpus_guard's
    regression check — the guarded write already happened with the real content.
    """
    if not path.is_file():
        return
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        return
    document["corpus_digest"] = corpus.corpus_digest
    document["corpus_event_count"] = corpus.event_count
    atomic_write_json(path, document)


def record_event(event: dict, events_path: str = DEFAULT_EVENTS_PATH) -> dict:
    """Validate a workflow event and append it to a sandboxed events.jsonl ledger.

    Note: the gateway wrapper also ledgers this call itself in the kernel ledger.
    That double-write is intentional — two facts, not one fact twice (ADR-0011).
    """
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

    # Canonical corpus fingerprint for THIS run — same source roots the projectors below
    # actually read. Stamped into every materialized doc so a doc's own provenance can be
    # checked against the corpus that produced it (CQRS-ES-STANDARDIZATION.md step 4).
    corpus = _aggregate_corpus(source_paths)

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
    # Every knowledge file a given kind may write — restamped with corpus provenance
    # after the kind's own (guarded) write already landed the real content.
    written_files = {
        "capacity": ("capacity_estimates.json", "known_good_models.json",
                     "known_bad_models.json", "prediction_accuracy.json"),
        "findings": ("findings.json",),
        "associations": ("associations.json", "capabilities.json"),
        "coverage": ("coverage.json",),
        "experiments": ("experiment_candidates.json", "experiment_results.json"),
        "policy": ("policy.json",),
    }

    results: dict[str, dict] = {}
    for kind in ordered:
        try:
            output = runners[kind]()
        except Exception as exc:  # guard refusals and projector faults both belong in the digest
            results[kind] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            continue
        for file_name in written_files.get(kind, ()):
            _restamp_written_file(out_dir / file_name, corpus)
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
        "corpus_digest": corpus.corpus_digest,
        "corpus_event_count": corpus.event_count,
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


def project_offload_knowledge(
    ledger_path: str = str(_CAPACITY_DEFAULT_LEDGER), out: str = DEFAULT_OUT,
) -> dict:
    """Project the HEARTH ledger into knowledge/offload.json (S2 executor economics).

    Buckets inference-class local_generate events by executor backend with a
    sunk/trial/unknown cost-class map, making the offload ratio and $-saved
    estimate queryable. Read-only over the ledger; the only write is
    offload.json inside the sandboxed knowledge dir, corpus-guarded like
    capacity.json.
    """
    ledger = resolve_in_scope(ledger_path)
    document = build_offload_document(ledger)
    out_dir = resolve_in_scope(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "offload.json"
    guard_write(target, document, make_extractor("bucket_count"))
    return {
        "path": str(target),
        "evidence_watermark": document["evidence_watermark"],
        "bucket_count": len(document["buckets"]),
    }


def query_offload(knowledge_dir: str = DEFAULT_OUT) -> dict:
    """Return the materialized offload.json (with file mtime) from the sandbox."""
    return _query_knowledge_file("offload.json", knowledge_dir)


def rebuild_knowledge(sources: list[str] | None = None, out: str = DEFAULT_OUT,
                      ledger_path: str = str(_CAPACITY_DEFAULT_LEDGER),
                      allow_fixture_sources: bool = False) -> dict:
    """Rebuild the entire knowledge/ store from zero (CQRS/ES plan step 5).

    Replays the full projection DAG (every kind project() runs, plus the ledger-native
    capacity.json) over the canonical corpus into a staging directory, validates the
    complete staged set, then atomically swaps it into `out`. Unlike project(), this
    is not an incremental write: staging starts empty, so corpus_guard's regression
    comparison never fires for a from-zero rebuild — that is the point, not a bypass
    of the guard's intent. A crash mid-rebuild leaves the live knowledge/ dir
    byte-untouched; staging is always cleaned up.
    """
    return _rebuild_knowledge(sources=sources, out=out, ledger_path=ledger_path,
                              allow_fixture_sources=allow_fixture_sources)


def get_tools() -> list[Callable]:
    return [
        record_event, project, query_capabilities, query_findings, query_beliefs_summary,
        project_capacity_knowledge, query_capacity, project_offload_knowledge, query_offload,
        rebuild_knowledge,
    ]
