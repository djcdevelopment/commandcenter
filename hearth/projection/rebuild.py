"""from-zero knowledge rebuild (CQRS/ES standardization plan, step 5).

The rebuild button: replay the full projection DAG over the canonical corpus into a
STAGING directory, validate the complete staged set, then atomically swap every staged
file over the live one. By construction this bypasses corpus_guard's regression
comparison (the staging dir starts empty — there is no prior file in it to regress
against); the guard keeps gating every normal incremental `project()` call unchanged.

Orchestration lives here, thin: it reuses `hearth.toolsurface.knowledge.project()` for
the six event-derived projection kinds (capacity/findings/associations/coverage/
experiments/policy) and `hearth.projection.capacity.build_capacity_document` for the
ledger-native capacity.json, exactly as `project_capacity_knowledge` does — no projector
logic is duplicated.

Contract: `rebuild_knowledge(...) -> dict` with keys {ok, files, corpus_digest,
corpus_event_count, watermark}. On any failure before the swap, the live knowledge/ dir
is guaranteed byte-untouched (nothing in it is opened for writing until every staged
file has already validated). The staging directory is removed in both the success and
the failure path.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from hearth.toolsurface._scope import resolve_in_scope

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.workflow.corpus import Corpus  # noqa: E402
from tools.workflow.corpus_guard import check_fixture_taint  # noqa: E402
from tools.workflow.project_associations import materialize_associations  # noqa: E402
from tools.workflow.project_capacity import collect_event_files, materialize_knowledge  # noqa: E402
from tools.workflow.project_coverage import materialize_coverage  # noqa: E402
from tools.workflow.project_experiments import materialize_experiments  # noqa: E402
from tools.workflow.project_findings import materialize_findings  # noqa: E402
from tools.workflow.project_policy import materialize_policy  # noqa: E402

from hearth.projection.capacity import DEFAULT_LEDGER as _CAPACITY_DEFAULT_LEDGER
from hearth.projection.capacity import build_capacity_document

DEFAULT_SOURCES = ["runs"]
DEFAULT_OUT = "knowledge"
DEFAULT_LEDGER_PATH = str(_CAPACITY_DEFAULT_LEDGER)

STAGING_DIRNAME = ".staging-rebuild"

# Same dependency order project() uses for the event-derived kinds; capacity.json (the
# ledger-native one, distinct from capacity_estimates.json) has no upstream dependency
# on any of them and is projected independently, first, for tidiness.
_PROJECTION_KINDS = ("capacity", "findings", "associations", "coverage", "experiments", "policy")

# Every knowledge file each kind (including the ledger-native "capacity_json" pseudo-kind)
# may write. Used both to run the projectors and to validate the complete staged set.
_WRITTEN_FILES: dict[str, tuple[str, ...]] = {
    "capacity": ("capacity_estimates.json", "known_good_models.json",
                 "known_bad_models.json", "prediction_accuracy.json"),
    "findings": ("findings.json",),
    "associations": ("associations.json", "capabilities.json"),
    "coverage": ("coverage.json",),
    "experiments": ("experiment_candidates.json", "experiment_results.json"),
    "policy": ("policy.json",),
    "capacity_json": ("capacity.json",),
}

# Every file a from-zero rebuild is expected to produce, in a stable order.
EXPECTED_FILES: tuple[str, ...] = tuple(
    name for kind in (*_PROJECTION_KINDS, "capacity_json") for name in _WRITTEN_FILES[kind]
)


class RebuildValidationError(RuntimeError):
    """Raised when the staged knowledge set fails validation before the swap."""


def _aggregate_corpus(source_paths: list[Path]) -> Corpus:
    """Mirror hearth.toolsurface.knowledge._aggregate_corpus: fold every source root
    into one Corpus (union of event files, summed counts, max watermark, and a
    combined content-shaped digest independent of `sources` order)."""
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

    if len(per_root) == 1:
        combined_digest = per_root[0].corpus_digest
    else:
        import hashlib
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
    """Same restamp knowledge.py performs after each guarded write: add the two
    additive corpus provenance keys without touching anything else in the doc."""
    if not path.is_file():
        return
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        return
    document["corpus_digest"] = corpus.corpus_digest
    document["corpus_event_count"] = corpus.event_count
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _run_projections(staging_dir: Path, event_files: list[Path], corpus: Corpus) -> None:
    """Run every projection kind, in dependency order, into `staging_dir`.

    guard_write inside each materialize_* call will never see a regression here: the
    staging dir starts empty, so there is no prior file for any kind to compare
    against. This is the from-zero rebuild's whole point — the guard is bypassed by
    construction (an always-passing comparison), not disabled.
    """
    runners: dict[str, Callable[[], object]] = {
        "capacity": lambda: materialize_knowledge(event_files, staging_dir),
        "findings": lambda: materialize_findings(event_files, staging_dir),
        "associations": lambda: materialize_associations(event_files, staging_dir),
        "coverage": lambda: materialize_coverage(event_files, staging_dir),
        "experiments": lambda: materialize_experiments(event_files, staging_dir),
        "policy": lambda: materialize_policy(staging_dir / "findings.json", staging_dir),
    }
    for kind in _PROJECTION_KINDS:
        runners[kind]()
        for file_name in _WRITTEN_FILES[kind]:
            _restamp_written_file(staging_dir / file_name, corpus)


def _run_capacity_json(staging_dir: Path, ledger_path: Path) -> None:
    """The ledger-native capacity.json (JS2), materialized the same way
    project_capacity_knowledge does, but writing straight into staging (no guard —
    there is nothing in staging to regress against)."""
    document = build_capacity_document(ledger_path)
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "capacity.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _validate_staged(staging_dir: Path) -> None:
    """Every expected file must exist, parse as JSON, and carry a contract_version."""
    missing = [name for name in EXPECTED_FILES if not (staging_dir / name).is_file()]
    if missing:
        raise RebuildValidationError(f"staged rebuild is missing file(s): {missing}")

    for name in EXPECTED_FILES:
        path = staging_dir / name
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise RebuildValidationError(f"staged file {name} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict) or "contract_version" not in document:
            raise RebuildValidationError(
                f"staged file {name} is missing contract_version")


def rebuild_knowledge(sources: list[str] | None = None, out: str = DEFAULT_OUT,
                      ledger_path: str = DEFAULT_LEDGER_PATH,
                      allow_fixture_sources: bool = False) -> dict:
    """Replay the full projection DAG from zero and atomically swap it into `out`.

    Staging happens inside `<out>/.staging-rebuild/` (within the sandbox scope, so
    HEARTH_SCOPE containment applies to it exactly like every other resolved path).
    Every staged file is validated (exists, parses, carries contract_version) BEFORE
    any live file is touched; the swap itself is one `os.replace` per file, atomic on
    every platform this repo runs on (see tools/workflow/fsio.py). Staging is removed
    on both the success and the failure path — a crash mid-rebuild never leaves a live
    knowledge/ file torn, and never leaves the staging directory behind either.
    """
    source_paths = [resolve_in_scope(raw) for raw in (sources or DEFAULT_SOURCES)]
    out_dir = resolve_in_scope(out)
    ledger = resolve_in_scope(ledger_path)

    event_files = collect_event_files(source_paths)
    check_fixture_taint(source_paths + event_files, out_dir, allow=allow_fixture_sources)

    corpus = _aggregate_corpus(source_paths)

    staging_dir = out_dir / STAGING_DIRNAME
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        _run_projections(staging_dir, event_files, corpus)
        _run_capacity_json(staging_dir, ledger)
        _validate_staged(staging_dir)

        # Validation passed for every staged file: only now do we touch the live dir.
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in EXPECTED_FILES:
            os.replace(staging_dir / name, out_dir / name)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return {
        "ok": True,
        "files": list(EXPECTED_FILES),
        "corpus_digest": corpus.corpus_digest,
        "corpus_event_count": corpus.event_count,
        "watermark": corpus.watermark,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.rebuild",
        description="Rebuild the knowledge/ store from zero: replay the full "
                     "projection DAG into staging, validate, then atomically swap.",
    )
    parser.add_argument("--sources", nargs="+", default=None,
                        help="Event source roots (default: ['runs'])")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--allow-fixture-sources", action="store_true")
    args = parser.parse_args(argv)

    result = rebuild_knowledge(sources=args.sources, out=args.out, ledger_path=args.ledger,
                               allow_fixture_sources=args.allow_fixture_sources)
    print(f"rebuild: OK, {len(result['files'])} file(s), "
          f"corpus_digest={result['corpus_digest']}, "
          f"corpus_event_count={result['corpus_event_count']}, "
          f"watermark={result['watermark']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
