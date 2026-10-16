"""Labels materializer (Track C; CONTRACT.md §1/§3, plan tender-gathering-fox Lap 2).

One env gate, read per invocation, default off:

    HEARTH_LABELS=off    (default) print {"state": "off"} and exit 0: the derivation code is not imported
                         and no file is opened (test_project_labels proves it)
    HEARTH_LABELS=on     sweep and/or roll up

Steps (any combination, run in this order):

    --sweep    every runs/operator/work_* whose labels.json is missing, older than its inputs (a late
               verdict), or from an older generator gets labels.json (atomic; a fault becomes a labels.json
               with generator_error, never an exception out of the sweep)
    --rollup   every labels.json -> knowledge/labels.json (labels-rollup.v1: coverage per label with
               by_method, status-class counts, self-report-vs-elapsed calibration rows)

Paths: HEARTH_LABELS_RUNS_ROOT (runs/operator), HEARTH_LABELS_KNOWLEDGE_DIR (knowledge), HEARTH_LEDGER_DIR
(the door's ledger, read-only), HEARTH_LABELS_TRANSCRIPTS (~/.claude/projects, metadata only),
HEARTH_LABELS_CONTINUITY_CONFIG (~/.continuity/config.toml, the repos scanned for Local-Work trailers),
HEARTH_LABELS_CORPUS (a precursor-run.v2 runs.jsonl whose group_id gives lineage; absent -> identical
intent_sha256), each overridable by a flag. Relative paths resolve against the repo root this module lives in.

The materializer never records a verdict, never picks a seat and never reads or changes a routing
decision; it runs off the door's request path (a timer, or by hand).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODE_ENV = "HEARTH_LABELS"
RUNS_ROOT_ENV = "HEARTH_LABELS_RUNS_ROOT"
KNOWLEDGE_ENV = "HEARTH_LABELS_KNOWLEDGE_DIR"
LEDGER_ENV = "HEARTH_LEDGER_DIR"
TRANSCRIPTS_ENV = "HEARTH_LABELS_TRANSCRIPTS"
CONTINUITY_ENV = "HEARTH_LABELS_CONTINUITY_CONFIG"
CORPUS_ENV = "HEARTH_LABELS_CORPUS"
MODES = ("off", "on")

DEFAULT_RUNS_ROOT = "runs/operator"
DEFAULT_KNOWLEDGE_DIR = "knowledge"
DEFAULT_LEDGER_DIR = "/home/derek/hearth-production/var/ledger"
DEFAULT_TRANSCRIPTS = "~/.claude/projects"
DEFAULT_CONTINUITY_CONFIG = "~/.continuity/config.toml"
DEFAULT_CORPUS = "/home/derek/work/lab-rnd/research/precursor/corpus/v1/runs.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[2]


def labels_mode() -> str:
    raw = (os.environ.get(MODE_ENV) or "off").strip().lower()
    return raw if raw in MODES else "off"


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--rollup", action="store_true")
    parser.add_argument("--force", action="store_true", help="regenerate labels.json even when fresh")
    parser.add_argument("--runs-root", default=os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT)
    parser.add_argument("--out", default=os.environ.get(KNOWLEDGE_ENV) or DEFAULT_KNOWLEDGE_DIR,
                        help="knowledge directory for labels.json (the rollup)")
    parser.add_argument("--ledger-dir", default=os.environ.get(LEDGER_ENV) or DEFAULT_LEDGER_DIR)
    parser.add_argument("--transcripts", default=os.environ.get(TRANSCRIPTS_ENV) or DEFAULT_TRANSCRIPTS)
    parser.add_argument("--continuity-config", default=os.environ.get(CONTINUITY_ENV) or DEFAULT_CONTINUITY_CONFIG)
    parser.add_argument("--corpus", default=os.environ.get(CORPUS_ENV) or DEFAULT_CORPUS)
    parser.add_argument("--no-transcripts", action="store_true", help="skip the transcript proxy scan")
    parser.add_argument("--no-git", action="store_true", help="skip the Local-Work trailer scan")
    args = parser.parse_args(argv)

    mode = labels_mode()
    if mode == "off":
        # Nothing imported, nothing opened: the gate is the whole contract of off mode.
        print(json.dumps({"state": "off"}))
        return 0

    # Lazy: the derivation code (and its corpus_guard / sqlite3 imports) exists only past the gate.
    from tools.workflow import labels_derive as ld

    runs_root = _resolve(args.runs_root)
    out_dir = _resolve(args.out)
    ctx_kwargs = {
        "ledger_dir": _resolve(args.ledger_dir),
        "transcripts_root": None if args.no_transcripts else _resolve(args.transcripts),
        "continuity_config": None if args.no_git else _resolve(args.continuity_config),
        "corpus_path": _resolve(args.corpus) if args.corpus else None,
    }
    summary: dict = {"state": mode, "runs_root": str(runs_root), "out": str(out_dir),
                     "generator_version": ld.GENERATOR_VERSION}
    if not (args.sweep or args.rollup):
        summary["note"] = "no step requested: pass --sweep and/or --rollup"

    if args.sweep:
        summary["sweep"] = ld.sweep(runs_root, ctx_kwargs, force=args.force)
    if args.rollup:
        try:
            doc = ld.rollup(runs_root, out_dir)
            summary["rollup"] = {
                "runs": doc["runs"], "unreadable": doc["unreadable"], "generator_errors": doc["generator_errors"],
                "evidence_watermark": doc["evidence_watermark"],
                "coverage_n": {name: cov["n"] for name, cov in doc["coverage"].items()},
                "status_classes": doc["status_classes"],
                "calibration_rows": len(doc["calibration"]["self_report_vs_elapsed"]),
                "calibration_summary": doc["calibration"]["summary"],
            }
        except Exception as exc:  # noqa: BLE001 -- a refused knowledge write is reported, never a crash
            summary["rollup"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
