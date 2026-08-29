"""run_doc_adr_bench -- flat gcp-gemini / gcp-gemini-pro / omen-arc comparison
on doc-vs-ADR-vs-code consistency tasks.

    python -m hearth.experiments.run_doc_adr_bench --smoke   # 1 backend x 1 task, live proof
    python -m hearth.experiments.run_doc_adr_bench           # full sweep, all 3 backends x 6 tasks

Every call pins ``backend=`` (never ``task=``), so every row's ledger event
should show ``routed_by: "pinned:<name>"`` -- confirm with:
    tail hearth/var/ledger/events.ndjson

Retargeted 2026-08-29: ``BACKENDS`` named ``am4-moe``, whose B70s left AM4 in the
2026-08-20 rebuild -- every one of its rows had been coming back ``ok: false``.
The local arm is now ``omen-arc``, the resident dual-B70 rung that actually
serves. The judge panel moved too (see ``matrix.DEFAULT_JUDGES``); note that
gcp-gemini is both an arm here and a judge seat, so read the per-judge scores,
not only the mean.

gcp-gemini / gcp-gemini-pro need a valid Google OAuth token on the gateway host
(env ``GOOGLE_OAUTH_ACCESS_TOKEN`` or ``gcloud auth print-access-token``) and
``GOOGLE_CLOUD_PROJECT`` set.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from hearth.experiments.doc_adr_bench import DOC_ADR_TASKS, bench_summary, run_flat_matrix
from hearth.toolsurface.inference import local_generate

BACKENDS = ["gcp-gemini", "gcp-gemini-pro", "omen-arc"]
OUT_ROOT = "hearth/var/experiments"
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _persist(rows: list[dict], summary: dict, tag: str) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{tag}"
    out_dir = os.path.join(_REPO, OUT_ROOT, f"doc-adr-bench-{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "rows.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="one backend (omen-arc) x one task, live proof before the full sweep")
    ap.add_argument("--backends", nargs="+", default=None,
                    help=f"restrict to these backends (default: {BACKENDS})")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help=f"restrict to these task ids (default: all of {list(DOC_ADR_TASKS)})")
    args = ap.parse_args(argv)

    def prog(msg: str) -> None:
        print(f"  {msg}", flush=True)

    if args.smoke:
        # Smoke on the sunk-cost local rung: the path most worth proving is
        # door -> PINNED local backend -> judge panel, and it costs no credits.
        backends = ["omen-arc"]
        task_ids = [next(iter(DOC_ADR_TASKS))]
        tag = "smoke"
    else:
        backends = args.backends or BACKENDS
        task_ids = args.tasks
        tag = "sweep"

    print(f"running {len(backends)} backend(s) x "
          f"{len(task_ids) if task_ids else len(DOC_ADR_TASKS)} task(s) ({tag})...")
    rows = run_flat_matrix(backends, local_generate, task_ids=task_ids, on_progress=prog)
    summary = bench_summary(rows)
    out_dir = _persist(rows, summary, tag)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\ndataset -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
