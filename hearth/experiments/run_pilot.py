"""run_pilot — execute the planning-quality matrix pilot and persist the dataset.

Full pilot (cross-machine) needs the AM4 vllama slots hot (oxen-planner +
oxen-critic). Because the B70 stack is Windows-native on D:\\work and this host
reaches AM4 only over a WSL SSH with no Windows interop, hotting the slots is a
Windows-side action:

    # on AM4 (PowerShell), once:
    & 'D:\\work\\vllama\\src\\Vllama\\bin\\Release\\net9.0\\vllama.exe' up --model qwen3-30b-a3b-128k
    & '...\\vllama.exe' up --model qwen2.5-14b-q4
    # (or the persistent supervisor: D:\\work\\b70tools\\scripts\\tooling\\Start-AlwaysHotMoE.ps1)

Then, from OMEN:
    python -m hearth.experiments.run_pilot            # full 24-cell cross-machine pilot
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
    Cell, Role, build_pilot_cells, run_matrix, dataset_summary,
)
from hearth.toolsurface.inference import local_generate

# Pilot config (grounded in the am4-catalog + backends.toml).
AM4_MODELS = [("am4-oxen", "oxen-planner"), ("am4-oxen", "oxen-critic")]
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
    args = ap.parse_args(argv)

    omen = _omen_ready()
    am4 = _am4_ready()
    print(f"preflight: OMEN ollama={'UP' if omen else 'DOWN'}  "
          f"AM4 oxen slots={'READY' if am4 else 'COLD'}")
    if args.check:
        if not am4:
            print("\nAM4 slots cold. Hot them on AM4 (Windows), then re-run:")
            print("  & 'D:\\work\\vllama\\...\\vllama.exe' up --model qwen3-30b-a3b-128k")
            print("  & '...\\vllama.exe' up --model qwen2.5-14b-q4")
        return 0 if (omen and am4) else 1

    if not omen:
        print("OMEN ollama is down — cannot run.")
        return 1

    def prog(msg: str) -> None:
        print(f"  {msg}", flush=True)

    if args.smoke:
        cells, tag = _smoke_cells(), "smoke"
    else:
        if not am4:
            print("\nAM4 slots COLD — the cross-machine pilot needs them hot first.")
            print("Hot them on AM4 (see module docstring), or run --smoke for the OMEN-only proof.")
            return 1
        cells, tag = build_pilot_cells(AM4_MODELS, OMEN_MOE, laps=tuple(args.laps)), "pilot"

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
