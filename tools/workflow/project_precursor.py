"""Precursor materializer (Track D1; plan tender-gathering-fox §5, ADR-0070 proposed).

One env gate, read per invocation, default off:

    HEARTH_PRECURSOR=off      (default) print {"state": "off"} and exit 0: the index code is not
                              imported and no file is opened (test_project_precursor proves it)
    HEARTH_PRECURSOR=shadow   build the index, advise, join; nothing is displayed anywhere
    HEARTH_PRECURSOR=display  identical to shadow here; display is the AMUX side's concern

Steps (any combination, run in this order):

    --build-index   corpus export + live work dirs -> knowledge/precursor_index.json (atomic)
    --advise        every runs/operator/work_* with an envelope and a dispatch (or receive)
                    event but no precursor/advice.json gets advice for arms table and bm25
    --join          advice rows x first outcome.final -> knowledge/precursor_accuracy.json

Paths: HEARTH_PRECURSOR_CORPUS (runs.jsonl), HEARTH_PRECURSOR_RUNS_ROOT (runs/operator),
HEARTH_PRECURSOR_KNOWLEDGE_DIR (knowledge), HEARTH_PRECURSOR_RESEARCH_DIR (frozen config lookup),
each overridable by a flag. Relative paths resolve against the repo root this module lives in.

The materializer never records a verdict, never picks a seat and never reads or changes a routing
decision; it runs off the door's request path (a timer, or by hand).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODE_ENV = "HEARTH_PRECURSOR"
CORPUS_ENV = "HEARTH_PRECURSOR_CORPUS"
RUNS_ROOT_ENV = "HEARTH_PRECURSOR_RUNS_ROOT"
KNOWLEDGE_ENV = "HEARTH_PRECURSOR_KNOWLEDGE_DIR"
RESEARCH_ENV = "HEARTH_PRECURSOR_RESEARCH_DIR"
MODES = ("off", "shadow", "display")

DEFAULT_CORPUS = "/home/derek/work/lab-rnd/research/precursor/corpus/v1/runs.jsonl"
DEFAULT_RESEARCH_DIR = "/home/derek/work/lab-rnd/research/precursor"
DEFAULT_RUNS_ROOT = "runs/operator"
DEFAULT_KNOWLEDGE_DIR = "knowledge"
REPO_ROOT = Path(__file__).resolve().parents[2]


def precursor_mode() -> str:
    raw = (os.environ.get(MODE_ENV) or "off").strip().lower()
    return raw if raw in MODES else "off"


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-index", action="store_true")
    parser.add_argument("--advise", action="store_true")
    parser.add_argument("--join", action="store_true")
    parser.add_argument("--corpus", default=os.environ.get(CORPUS_ENV) or DEFAULT_CORPUS)
    parser.add_argument("--runs-root", default=os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT)
    parser.add_argument("--out", default=os.environ.get(KNOWLEDGE_ENV) or DEFAULT_KNOWLEDGE_DIR,
                        help="knowledge directory for precursor_index.json and precursor_accuracy.json")
    parser.add_argument("--research-dir", default=os.environ.get(RESEARCH_ENV) or DEFAULT_RESEARCH_DIR,
                        help="where a frozen replay config (results/*/config.json) is looked for")
    args = parser.parse_args(argv)

    mode = precursor_mode()
    if mode == "off":
        # Nothing imported, nothing opened: the gate is the whole contract of off mode.
        print(json.dumps({"state": "off"}))
        return 0

    # Lazy: the index code (and its corpus_guard import) exists only past the gate.
    from tools.workflow import precursor_index as pi

    corpus = _resolve(args.corpus)
    runs_root = _resolve(args.runs_root)
    out_dir = _resolve(args.out)
    research_dir = _resolve(args.research_dir)
    summary: dict = {"state": mode, "runs_root": str(runs_root), "out": str(out_dir)}

    if not (args.build_index or args.advise or args.join):
        summary["note"] = "no step requested: pass --build-index, --advise and/or --join"

    if args.build_index:
        index = pi.build_index(corpus, runs_root, out_dir, research_dir=research_dir)
        summary["index"] = {k: index[k] for k in ("corpus_version", "built_at", "row_count", "corpus_rows",
                                                  "live_rows", "skipped", "config_id", "sha256", "build_ms")}
        if index["build_ms"] > 60_000:
            summary["index"]["bound_exceeded"] = "build > 60 s (plan §5.4)"
    if args.advise:
        summary["advise"] = pi.advise(runs_root, out_dir, mode="shadow")
    if args.join:
        accuracy = pi.join(runs_root, out_dir, corpus)
        summary["join"] = {k: accuracy[k] for k in ("advice_files", "advice_count", "joined_count", "late_excluded",
                                                    "awaiting_verdict", "nonbinary_excluded", "unreadable")}
        summary["join"]["arms"] = {arm: periods.get("all") for arm, periods in accuracy["arms"].items()}

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
