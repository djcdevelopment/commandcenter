"""run_doc_adr_bench -- flat arm comparison on doc-vs-ADR-vs-code consistency tasks.

    python -m hearth.experiments.run_doc_adr_bench --dry-run [...]   # cells + panel, dispatches nothing
    python -m hearth.experiments.run_doc_adr_bench --smoke [--arms X] # 1 arm x 1 task, live proof
    python -m hearth.experiments.run_doc_adr_bench                    # full sweep, BACKENDS x all tasks
    python -m hearth.experiments.run_doc_adr_bench \\
        --arms omen-swap:phi4-vk1 omen-swap:qwen14b-vk1 \\
        --tasks adr-0042-vs-launcher adr-0041-claims-vs-receipts \\
        --max-tokens 1024 --timeout-s 600                             # the M1 two-model pour

Arms are ``backend`` or ``backend:model`` (first colon splits; an Ollama tag
such as ``fx99-ollama:qwen2.5:7b`` survives). ``--judges backend:model ...``
overrides the panel; the default is ``panel.held_out_judges(arms)`` -- seats
from ``panel.JUDGE_POOL`` that share neither a backend nor a model with any
arm. A judge that does is refused (exit 2) before any call, explicit or not.

Every call pins ``backend=`` (never ``task=``), so every row's ledger event
should show ``routed_by: "pinned:<name>"`` -- confirm with:
    tail hearth/var/ledger/events.ndjson

The sweep runs under ``dispatch_identity(DispatchIdentity("experiment-doc-adr-bench",
"local", <node>))`` so its rows are observations, not ad-hoc probes; the
identity is printed with the plan. See ``doc_adr_bench`` for the two pours
to run once OMEN is free and the payload-vs-``context_bytes`` arithmetic
that picks their tasks (``--dry-run`` shows ``fits`` per cell).

Retargeted 2026-08-29: ``BACKENDS`` named ``am4-moe``, whose B70s left AM4 in the
2026-08-20 rebuild -- every one of its rows had been coming back ``ok: false``.
The local arm is now ``omen-arc``, the resident dual-B70 rung that actually
serves. Since M2 (2026-09-03) the panel is derived from the arms, so the old
gcp-gemini self-scoring seat can no longer sit on a sweep that has gcp-gemini
as an arm; the default three-arm sweep leaves only one held-out seat in the
pool and is refused unless you name a panel with ``--judges`` deliberately.

gcp-gemini / gcp-gemini-pro need a valid Google OAuth token on the gateway host
(env ``GOOGLE_OAUTH_ACCESS_TOKEN`` or ``gcloud auth print-access-token``) and
``GOOGLE_CLOUD_PROJECT`` set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from hearth.experiments.doc_adr_bench import (
    DOC_ADR_TASKS, bench_identity, bench_summary, parse_arm, plan_flat_matrix, run_flat_matrix,
)
from hearth.experiments.panel import PanelConflict
from hearth.toolsurface.inference import local_generate

BACKENDS = ["gcp-gemini", "gcp-gemini-pro", "omen-arc"]
OUT_ROOT = "hearth/var/experiments"
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _persist(rows: list[dict], summary: dict, tag: str, out_root: Optional[str] = None,
             plan: Optional[dict] = None) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{tag}"
    root = out_root or os.path.join(_REPO, OUT_ROOT)
    out_dir = os.path.join(root, f"doc-adr-bench-{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "rows.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if plan is not None:
        # The panel (with its exclusions and co-residency note) and the identity
        # the rows were dispatched under are provenance; keep them beside the rows.
        with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
    return out_dir


def _print_plan(plan: dict) -> None:
    ident = plan["identity"]
    print(f"identity: {ident['caller_id']} ({ident['runner_class']} @ {ident['node']})")
    print(f"arms: {', '.join(plan['arms'])}")
    print(f"tasks: {', '.join(plan['tasks'])}")
    print("judges: " + ", ".join(f"{b}/{m}" for b, m in plan["judges"]))
    panel = plan.get("panel") or {}
    for ex in panel.get("excluded") or []:
        print(f"  excluded {ex['backend']}/{ex['model']}: {ex['reason']}")
    if plan.get("panel_note"):
        print(f"  note: {plan['panel_note']}")
    print(f"cells ({len(plan['cells'])}; {plan['planned_calls']} calls incl. judges):")
    for c in plan["cells"]:
        budget = c["context_bytes"]
        fit = ("fits" if c["fits"] else "OVER BUDGET -- the door will refuse this pin") \
            if c["fits"] is not None else "no declared budget"
        missing = f"  MISSING {c['files_missing']}" if c["files_missing"] else ""
        print(f"  {c['arm']}: {c['task_id']}  ~{c['payload_bytes_est']} B"
              f" vs {budget if budget is not None else 'unlimited'}  {fit}{missing}")


def main(argv: list[str] | None = None, *, generate: Optional[Callable[..., dict]] = None,
         out_root: Optional[str] = None, pool: Any = None) -> int:
    ap = argparse.ArgumentParser(
        description="flat doc-vs-ADR-vs-code benchmark across pinned arms")
    ap.add_argument("--smoke", action="store_true",
                    help="one arm (the first --arms, else omen-arc) x one task, live proof "
                         "before the full sweep")
    ap.add_argument("--arms", "--backends", dest="arms", nargs="+", default=None,
                    metavar="BACKEND[:MODEL]",
                    help=f"arms under test, backend or backend:model (default: {BACKENDS})")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help=f"restrict to these task ids (default: all of {list(DOC_ADR_TASKS)})")
    ap.add_argument("--judges", nargs="+", default=None, metavar="BACKEND:MODEL",
                    help="judge seats (default: panel.held_out_judges(arms)); a seat sharing "
                         "a backend or model with an arm is refused")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="output budget for the ARM calls (default: the rung's declared "
                         "max_tokens); judges keep matrix.JUDGE_MAX_TOKENS")
    ap.add_argument("--timeout-s", type=int, default=None,
                    help="timeout for the ARM calls (default: the rung's declared timeout_s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the cells, panel, identity and payload-vs-budget; dispatch nothing")
    args = ap.parse_args(argv)
    if args.max_tokens is not None and args.max_tokens <= 0:
        ap.error("--max-tokens must be a positive integer")
    if args.timeout_s is not None and args.timeout_s <= 0:
        ap.error("--timeout-s must be positive")

    try:
        arms = [parse_arm(a) for a in (args.arms or BACKENDS)]
    except ValueError as exc:
        ap.error(str(exc))
    judges = None
    if args.judges:
        judges = []
        for spec in args.judges:
            try:
                backend, model = parse_arm(spec)
            except ValueError as exc:
                ap.error(str(exc))
            if model is None:
                ap.error(f"--judges seats must be backend:model, got {spec!r}")
            judges.append((backend, model))

    if args.smoke:
        # Smoke on one seat: the path most worth proving is door -> PINNED
        # backend -> held-out judge panel, on the cheapest cell.
        arms = arms[:1] if args.arms else [parse_arm("omen-arc")]
        task_ids = args.tasks[:1] if args.tasks else [next(iter(DOC_ADR_TASKS))]
        tag = "smoke"
    else:
        task_ids = args.tasks
        tag = "sweep"

    identity = bench_identity()
    try:
        plan = plan_flat_matrix(arms, task_ids, judges, pool=pool, identity=identity)
    except PanelConflict as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        ap.error(str(exc))
    _print_plan(plan)
    if args.dry_run:
        print("\ndry-run: nothing dispatched")
        return 0

    def prog(msg: str) -> None:
        print(f"  {msg}", flush=True)

    print(f"\nrunning {len(plan['arms'])} arm(s) x {len(plan['tasks'])} task(s) ({tag})...")
    rows = run_flat_matrix(arms, generate or local_generate, task_ids=plan["tasks"],
                           judges=[tuple(j) for j in plan["judges"]], on_progress=prog,
                           max_tokens=args.max_tokens, timeout_s=args.timeout_s,
                           pool=pool, identity=identity)
    summary = bench_summary(rows)
    out_dir = _persist(rows, summary, tag, out_root, plan=plan)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\ndataset -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
