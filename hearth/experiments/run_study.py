"""run_study — autonomous prompting study.

Question: does the CRITIC/AUTHOR prompt shape the refinement-laps curve? Round 4
found an inverted-U (2 laps best, over-refinement hurts at L3-L4). Hypothesis: the
over-refinement collapse is driven by the critic pushing complexity — a minimalist
critic should flatten it, a thorough critic steepen it, a concise author raise the
floor. This sweeps 4 prompt-variant arms x 6 planning prompts x laps{1..4} x repeats.

Memory-safe cross-machine by construction: planner on AM4 (oxen-planner, one model
on the box), critic + judge on OMEN. Stop b70-critic first so AM4 holds only the
planner. Writes rows.jsonl incrementally (partial results always survive a crash or
an early wake) and rewrites summary.json every few cells.

    AM4_OXEN_TOKEN=... python -m hearth.experiments.run_study
    ... --repeats 2 --laps 1 2 3 4          # defaults
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone

from hearth.experiments.matrix import (
    STUDY_CONFIGS, configs_by_name, build_variant_cells, run_cell, dataset_summary,
)
from hearth.experiments.run_pilot import _omen_ready, _am4_ready, OUT_ROOT, _REPO
from hearth.toolsurface.inference import local_generate

JUDGES = [(None, "qwen3-coder:30b")]           # held-out OMEN judge
PLANNER = ("am4-oxen", "oxen-planner")         # AM4 30B (author) — only model on AM4
CRITIC = (None, "qwen3-coder:30b")             # OMEN critic (persona varied per arm)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--laps", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--prompts", nargs="+", default=None)
    ap.add_argument("--configs", nargs="+", default=None,
                    help="select STUDY_CONFIGS arms by name (default: all)")
    args = ap.parse_args(argv)

    omen, am4 = _omen_ready(), _am4_ready()
    print(f"preflight: OMEN={'UP' if omen else 'DOWN'} AM4-planner={'READY' if am4 else 'COLD'}",
          flush=True)
    if not (omen and am4):
        print("preflight failed — need OMEN ollama + AM4 oxen-planner (:8080).")
        return 1

    configs = configs_by_name(args.configs) if args.configs else STUDY_CONFIGS
    cells = build_variant_cells(configs, prompt_ids=args.prompts,
                                laps=tuple(args.laps), repeats=args.repeats,
                                planner=PLANNER, critic=CRITIC)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(_REPO, OUT_ROOT, f"study-{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    jsonl_path = os.path.join(out_dir, "rows.jsonl")
    summ_path = os.path.join(out_dir, "summary.json")
    print(f"study {run_id}: {len(cells)} cells "
          f"({[c['name'] for c in STUDY_CONFIGS]}) -> {out_dir}", flush=True)

    rows: list[dict] = []
    with open(jsonl_path, "a", encoding="utf-8") as jf:
        for i, cell in enumerate(cells, 1):
            try:
                row = run_cell(cell, generate=local_generate, judges=JUDGES)
            except Exception as exc:                # never let one cell kill the study
                row = {"cell_id": cell.cell_id, "variant": cell.variant,
                       "prompt_id": cell.prompt_id, "laps": cell.laps, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}", "score": None}
                traceback.print_exc()
            rows.append(row)
            jf.write(json.dumps(row) + "\n")
            jf.flush()
            sc = (row.get("score") or {}).get("mean")
            print(f"[{i}/{len(cells)}] {cell.variant:18s} {cell.prompt_id:20s} "
                  f"L{cell.laps} -> score={sc} ok={row.get('ok')}", flush=True)
            if i % 6 == 0 or i == len(cells):        # rewrite summary periodically
                with open(summ_path, "w", encoding="utf-8") as sf:
                    json.dump({"run_id": run_id, "cells_done": i, "cells_total": len(cells),
                               "summary": dataset_summary(rows)}, sf, indent=2)

    summary = dataset_summary(rows)
    print("\n=== BY VARIANT x LAPS (the study result) ===")
    for k, v in sorted(summary["mean_score_by_variant_laps"].items()):
        print(f"  {k:26s} mean={v['mean']} n={v['n']}")
    print(f"\ndataset -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
