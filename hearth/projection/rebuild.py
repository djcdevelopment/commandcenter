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
from hearth.projection.economics import build_offload_document
from hearth.projection.freshness import DEFAULT_MAX_LAG_HOURS as _DEFAULT_MAX_LAG_HOURS

DEFAULT_SOURCES = ["runs"]
DEFAULT_OUT = "knowledge"
DEFAULT_LEDGER_PATH = str(_CAPACITY_DEFAULT_LEDGER)

# Write-side bridge defaults, mirrored from hearth.projection.ledger_adapter so the
# timer path can advance the corpus before replaying it (see main()).
DEFAULT_BRIDGE_TARGET = "runs/hearth-gateway/events.jsonl"
DEFAULT_BRIDGE_CURSOR = "hearth/var/projection_cursor.json"
DEFAULT_MAX_LAG_HOURS = _DEFAULT_MAX_LAG_HOURS

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
    "offload_json": ("offload.json",),
}

# Every file a from-zero rebuild is expected to produce, in a stable order.
EXPECTED_FILES: tuple[str, ...] = tuple(
    name for kind in (*_PROJECTION_KINDS, "capacity_json", "offload_json") for name in _WRITTEN_FILES[kind]
)


class RebuildValidationError(RuntimeError):
    """Raised when the staged knowledge set fails validation before the swap."""


def _aggregate_corpus(source_paths: list[Path]) -> Corpus:
    """Mirror hearth.toolsurface.knowledge._aggregate_corpus: fold every source root
    into one Corpus (union of event files, summed counts, max watermark, and a
    combined content-shaped digest independent of `sources` order).

    KNOWN, DELIBERATELY UNFIXED HERE: the watermark fold below is a lexical string
    max, the same shape repaired in capacity.py/economics.py. It carries the same
    exposure and, unlike the ledger, the runs corpus is ALREADY heterogeneous --
    all four spellings are live in runs/**/events.jsonl today (`.ffffff+00:00`,
    `.ffffffZ`, `+00:00`, `Z`). It is nonetheless not wrong right now: the lexical
    max and the parsed-instant max agree on this corpus (both
    2026-07-04T05:50:00+00:00, verified 2026-07-18), because a flip needs two
    events sharing a second but spelled differently, and the winning second is
    uncontested.

    Left alone on purpose, for two reasons:
    - Blast radius. This fold is REPORTED-only -- returned as result["watermark"]
      and printed. It is never stamped into a document (only corpus_digest and
      corpus_event_count are, via _restamp_written_file), so it never reaches
      corpus_guard. The lexical maxes that DO feed a guarded evidence_watermark are
      elsewhere: project_associations.evidence_watermark, project_learning.
      _evidence_watermark, project_policy._watermark, and the origin at
      tools/workflow/corpus.py. Repairing one site without those is theatre.
    - Repairing them is not a drive-by. corpus_guard._watermark_regressed already
      compares stored watermarks by parsed instant, so if any document currently
      carries a lexically-too-NEW watermark, the corrected (instant-earlier) value
      reads as a regression and BLOCKS the write. That needs a deliberate change
      with an authored override, not a quiet edit inside a rebuild refactor."""
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


def _run_offload_json(staging_dir: Path, ledger_path: Path) -> None:
    """The ledger-native offload.json (S2), materialized straight into staging."""
    document = build_offload_document(ledger_path)
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "offload.json").write_text(
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
        _run_offload_json(staging_dir, ledger)
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
    parser.add_argument("--skip-bridge", action="store_true",
                        help="Do not advance the ledger->corpus bridge before replaying. "
                             "Replays whatever the corpus already holds.")
    parser.add_argument("--bridge-target", default=DEFAULT_BRIDGE_TARGET,
                        help="Run stream the bridge appends mapped ledger events to")
    parser.add_argument("--bridge-cursor", default=DEFAULT_BRIDGE_CURSOR)
    parser.add_argument("--max-lag-hours", type=float, default=DEFAULT_MAX_LAG_HOURS,
                        help="Freshness threshold; exceeding it fails the run")
    parser.add_argument("--skip-freshness", action="store_true",
                        help="Skip the post-rebuild freshness guard (not recommended)")
    args = parser.parse_args(argv)

    # Advance the write side FIRST, or the replay below faithfully reproduces a
    # frozen corpus and reports ok. This is the step whose absence froze five
    # projections for 26 days: `ledger_adapter` was reachable only as a hand-run
    # CLI, and its cursor sat at line 3 of a 20,110-line ledger. It lives here in
    # main() rather than inside rebuild_knowledge() on purpose -- rebuild_knowledge
    # is a pure read-side replay with a byte-untouched-on-failure contract, and
    # appending to the event store is a write-side concern (ADR-0010's direction:
    # the adapter is the one sanctioned bridge between the two bounded contexts).
    bridge_failed = False
    if args.skip_bridge:
        print("bridge: SKIPPED (--skip-bridge)")
    else:
        try:
            from hearth.projection.ledger_adapter import project_ledger
            bridge = project_ledger(
                ledger_path=resolve_in_scope(args.ledger),
                target_path=resolve_in_scope(args.bridge_target),
                cursor_path=resolve_in_scope(args.bridge_cursor),
            )
            print(f"bridge: {bridge['processed']} projected, "
                  f"{bridge.get('filtered', 0)} self-monitoring filtered, "
                  f"{bridge['skipped']} already current, {len(bridge['errors'])} error(s)")
            for message in bridge["errors"][:10]:
                print(f"  bridge error: {message}")
            if len(bridge["errors"]) > 10:
                print(f"  ... {len(bridge['errors']) - 10} more bridge error(s)")
            bridge_failed = bool(bridge["errors"])
        except Exception as exc:
            # Never silent: a dead bridge means the rebuild below is about to
            # replay a stale corpus, which is exactly the failure mode this
            # whole path exists to prevent.
            print(f"bridge: FAILED {exc}")
            bridge_failed = True

    result = rebuild_knowledge(sources=args.sources, out=args.out, ledger_path=args.ledger,
                               allow_fixture_sources=args.allow_fixture_sources)
    print(f"rebuild: OK, {len(result['files'])} file(s), "
          f"corpus_digest={result['corpus_digest']}, "
          f"corpus_event_count={result['corpus_event_count']}, "
          f"watermark={result['watermark']}")

    # S5: the timer path also refreshes the dashboard from the projections it
    # just rebuilt. Failure here never fails the rebuild.
    try:
        from hearth.projection.dashboard import write_dashboard
        o_path = resolve_in_scope("HEARTH-DASHBOARD.html")
        k_dir = resolve_in_scope(args.out)
        l_path = resolve_in_scope(args.ledger)
        dash_res = write_dashboard(o_path, k_dir, l_path)
        print(f"dashboard: OK {dash_res['path']} ({dash_res['bytes']} bytes)")
    except Exception as exc:
        print(f"dashboard: FAILED {exc}")

    # Keep the aggregate boundary-traffic view on the same six-hour freshness
    # cadence. It reads the canonical ledger directly and never affects the
    # projection swap; like the main dashboard, rendering failure is non-fatal.
    try:
        from hearth.projection.call_mix_dashboard import write_dashboard as write_call_mix
        mix_out = resolve_in_scope("HEARTH-CALL-MIX.html")
        ledger = resolve_in_scope(args.ledger)
        sentinel = resolve_in_scope("hearth/var/sentinel/ollama-direct.ndjson")
        mix_res = write_call_mix(mix_out, ledger, sentinel)
        print(
            f"call-mix dashboard: OK {mix_res['path']} "
            f"({mix_res['bytes']} bytes, {mix_res['events']} events)"
        )
    except Exception as exc:
        print(f"call-mix dashboard: FAILED {exc}")

    # Runs LAST so the staleness verdict is the final word and owns the exit
    # code. The guard that makes the 26-day freeze impossible to repeat quietly. A
    # from-zero rebuild cannot self-detect staleness (replaying a frozen corpus
    # succeeds), so the check has to come from outside the replay and compare
    # against something that moves: the ledger head.
    stale = False
    if args.skip_freshness:
        print("freshness: SKIPPED (--skip-freshness)")
    else:
        try:
            from hearth.projection.freshness import check_freshness, format_report
            report = check_freshness(
                knowledge_dir=resolve_in_scope(args.out),
                ledger_path=resolve_in_scope(args.ledger),
                sources=[resolve_in_scope(raw) for raw in (args.sources or DEFAULT_SOURCES)],
                cursor_path=resolve_in_scope(args.bridge_cursor),
                max_lag_hours=args.max_lag_hours,
            )
            print(format_report(report))
            stale = not report["ok"]
        except Exception as exc:
            print(f"freshness: FAILED {exc}")
            stale = True

    if bridge_failed or stale:
        reasons = [name for name, failed in
                   (("bridge", bridge_failed), ("freshness", stale)) if failed]
        print(f"rebuild: COMPLETED WITH STALENESS ({', '.join(reasons)}) -- "
              f"knowledge/ was swapped, but do not treat it as current belief")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
