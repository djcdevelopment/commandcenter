"""run_doc_adr_bench -- Track 1 of the GCP trial-credit benchmark: flat
gcp-gemini / gcp-gemini-pro / am4-moe comparison on doc-vs-ADR-vs-code
consistency tasks (see the implementation plan's Track 1 for the full design).

    python -m hearth.experiments.run_doc_adr_bench --smoke   # 1 backend x 1 task, live proof
    python -m hearth.experiments.run_doc_adr_bench           # full sweep, all 3 backends x 5 tasks

Every call pins ``backend=`` (never ``task=``), so every row's ledger event
should show ``routed_by: "pinned:<name>"`` -- confirm with:
    tail hearth/var/ledger/events.ndjson

am4-moe must be awake (see hearth.toolsurface.summon.wake_am4 / the AM4
B70-card runbook) or its rows will come back ``ok: false``; gcp-gemini /
gcp-gemini-pro need a valid Google OAuth token on the gateway host (env
``GOOGLE_OAUTH_ACCESS_TOKEN`` or ``gcloud auth print-access-token``) and
``GOOGLE_CLOUD_PROJECT`` set.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from hearth.experiments.doc_adr_bench import DOC_ADR_TASKS, bench_summary, run_flat_matrix
from hearth.toolsurface.inference import local_generate

BACKENDS = ["gcp-gemini", "gcp-gemini-pro", "am4-moe"]
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
                    help="one backend (gcp-gemini) x one task, live proof before the full sweep")
    ap.add_argument("--backends", nargs="+", default=None,
                    help=f"restrict to these backends (default: {BACKENDS})")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help=f"restrict to these task ids (default: all of {list(DOC_ADR_TASKS)})")
    args = ap.parse_args(argv)

    def prog(msg: str) -> None:
        print(f"  {msg}", flush=True)

    if args.smoke:
        backends = ["gcp-gemini"]
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
