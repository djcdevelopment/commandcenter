"""run_pilot — execute the planning-quality matrix pilot and persist the dataset.

AM4 is now native Ubuntu (the Windows vllama.exe lifecycle is gone; the alias
contract survives in ~/.config/am4-fleet/alias-backends.json). Only the PLANNER
slot has Linux backing today: oxen-planner -> :8080 Qwen3-30B-A3B (dual-B70).
The critic slot (oxen-critic -> :8081 qwen2.5-14b) has no gguf/launcher yet, so
the interim pilot is AM4 planner <-> OMEN critic (both orderings) -- the split
run_refine's per-role routing was built for.

Wake the planner on AM4 (check the B70s are free of imagegen first):
    ssh derek@am4.tail8e749c.ts.net 'nohup ~/baseline/relaunch-qwen3-baseline.sh >> ~/baseline/qwen3.log 2>&1 &'

Then, from OMEN:
    python -m hearth.experiments.run_pilot            # interim 12-cell cross-machine pilot
    python -m hearth.experiments.run_pilot --smoke    # OMEN-only 2-cell live proof (no AM4)
    python -m hearth.experiments.run_pilot --check    # preflight only: are both boxes ready?
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

from hearth.experiments.matrix import (
    Cell, Role, build_pilot_cells, build_planner_critic_cells, run_matrix, dataset_summary,
)
from hearth.toolsurface.inference import local_generate

# Pilot config. Only the planner slot (:8080 Qwen3-30B) has Linux backing today;
# the :8081 critic slot is unbacked, so the interim grid is 1 AM4 model x OMEN
# (both orderings) = 12 cells. Add oxen-critic here once :8081 has a gguf+launcher.
AM4_MODELS = [("am4-oxen", "oxen-planner")]     # :8080 Qwen3-30B-A3B (dual-B70)
OMEN_MOE = (None, "qwen3-coder:30b")            # OMEN's resident MoE (A3B); fast
JUDGES = [(None, "qwen3-coder:30b")]            # held-out judge on OMEN

OUT_ROOT = "hearth/var/experiments"
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _omen_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _am4_ready() -> bool:
    """Probe a real generation through the oxen-planner facade alias (serve-truth)."""
    token = os.environ.get("AM4_OXEN_TOKEN", "")
    body = json.dumps({"model": "oxen-planner", "max_tokens": 4,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request(
        "http://100.116.82.60:8090/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def _persist(rows: list[dict], summary: dict, tag: str) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{tag}"
    out_dir = os.path.join(_REPO, OUT_ROOT, f"matrix-{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dataset.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "summary": summary, "rows": rows}, f, indent=2)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return out_dir


def _smoke_cells() -> list[Cell]:
    # OMEN-only live proof: planner=qwen2.5:14b, critic=qwen3-coder:30b, both orderings, 1 lap.
    p = Role("omen", None, "qwen2.5:14b")
    q = Role("omen", None, "qwen3-coder:30b")
    return [
        Cell("smoke_plan-skeleton_L1_a", "plan-skeleton", p, q, 1, "omen->omen"),
        Cell("smoke_plan-skeleton_L1_b", "plan-skeleton", q, p, 1, "omen->omen"),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="OMEN-only 2-cell live proof")
    ap.add_argument("--check", action="store_true", help="preflight only")
    ap.add_argument("--laps", type=int, nargs="+", default=[1, 3])
    ap.add_argument("--repeats", type=int, default=1,
                    help="run each cell N times (confirmation sweep; averages the score gradient)")
    ap.add_argument("--planner-critic", action="store_true",
                    help="dedicated AM4 planner(30B,:8080) <-> critic(14b,:8081) loop, OMEN judge")
    args = ap.parse_args(argv)

    omen = _omen_ready()
    am4 = _am4_ready()
    print(f"preflight: OMEN ollama={'UP' if omen else 'DOWN'}  "
          f"AM4 oxen slots={'READY' if am4 else 'COLD'}")
    if args.check:
        if not am4:
            print("\nAM4 planner (:8080) cold. Check the B70s are free, then wake it:")
            print("  ssh derek@am4.tail8e749c.ts.net 'nohup ~/baseline/relaunch-qwen3-baseline.sh "
                  ">> ~/baseline/qwen3.log 2>&1 &'")
        return 0 if (omen and am4) else 1

    if not omen:
        print("OMEN ollama is down — cannot run.")
        return 1

    def prog(msg: str) -> None:
        print(f"  {msg}", flush=True)

    if args.planner_critic:
        if not am4:
            print("\nAM4 planner (:8080) COLD — bring up the planner+critic first.")
            return 1
        cells = build_planner_critic_cells(laps=tuple(args.laps), repeats=args.repeats)
        tag = f"pc-sweep-r{args.repeats}"
    elif args.smoke:
        cells, tag = _smoke_cells(), "smoke"
    else:
        if not am4:
            print("\nAM4 planner (:8080) COLD — the cross-machine pilot needs it hot first.")
            print("Wake it (see module docstring), or run --smoke for the OMEN-only proof.")
            return 1
        cells = build_pilot_cells(AM4_MODELS, OMEN_MOE, laps=tuple(args.laps),
                                  repeats=args.repeats)
        tag = "pilot" if args.repeats <= 1 else f"sweep-r{args.repeats}"

    print(f"running {len(cells)} cells ({tag})...")
    rows = run_matrix(cells, generate=local_generate, judges=JUDGES, on_progress=prog)
    summary = dataset_summary(rows)
    out_dir = _persist(rows, summary, tag)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\ndataset -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
