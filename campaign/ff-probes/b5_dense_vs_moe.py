#!/usr/bin/env python3
"""B5 -- dense vs MoE per seat, with topology and warmth held constant.

THE CLAIM UNDER TEST (T1b): "Dense seats decode ~21-24 tok/s against the 30B MoE's ~121.
A planner/builder/reviewer split built from dense models runs ~6x slower per seat than the
incumbent."

It is last in the queue deliberately, because until B3 and B4 landed there was no way to
stop topology or warmth from masquerading as an architectural effect. The original figure
carries FOUR confounds, every one of them now identifiable:

  1. TOPOLOGY. The dense seats each sat on ONE card; the ~121 came from a llama-bench
     `tg128` run whose receipt reads `tensor_split "1.00"` -- also single-card, and
     mislabeled as dual-split. B3 has since measured a 5-6% decode difference between the
     topologies, so this is not negligible.
  2. MUTUAL CO-RESIDENCY. The two dense seats were measured SIMULTANEOUSLY, with each
     other and with Flash. B4 has since measured what a co-tenant costs an incumbent:
     15-28%. The dense numbers therefore include contention the MoE number does not.
  3. CONTEXT. Qwen3.8-27B ran at 32k, coder-32B at 16k. Different KV depth, and decode
     falls with KV depth.
  4. WARMTH. No rate gate on anything, before ADR-0043 existed.

So this measures ONE MODEL AT A TIME, at a FIXED topology, at the SAME context, warm, with
models INTERLEAVED so cross-model drift shows up as within-model disagreement. Both
topologies are swept, because "is the architecture 6x?" and "is the deployable seat 6x?"
are different questions and the first is only answerable at matched topology.

⚠ WHAT THIS CANNOT SETTLE. A 3B-active MoE doing less work per token than a 27B dense
model is not a defect, it is what MoE is for. B5 does not ask whether the gap is
"justified" -- only whether the ~6x RATIO survives once the confounds are removed. Per R1,
finding that the ratio moves does not license a mechanism story about why.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_cell        # noqa: E402
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402
import b3_topology_crossover as b3   # noqa: E402  -- the proven launch/assert machinery

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SENTINEL = r"C:\work\commandcenter\hearth\var\arc-maintenance.stop"
LOGDIR = r"E:\work\battlemage\ff-probes\b5-dense-moe"

MODELS = {
    "moe-30b-a3b": {
        "path": r"E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
        "kind": "MoE", "params": "30B total / 3B active", "quant": "Q4_K_M",
        "file_gb": 18.56, "kv_kib_per_tok": 96,
    },
    "dense-qwen38-27b": {
        "path": r"E:\work\battlemage\models\qwen38\Qwen3.8-27B-Q4_K_M.gguf",
        "kind": "dense", "params": "27B", "quant": "Q4_K_M",
        "file_gb": None, "kv_kib_per_tok": 64,
        "note": "the seat that produced the historical 23.54 tok/s figure",
    },
    "dense-qwen25-32b": {
        "path": r"E:\work\battlemage\models\qwen2.5-32b-instruct-q4_K_M.gguf",
        "kind": "dense", "params": "32B", "quant": "Q4_K_M",
        "file_gb": 19.85, "kv_kib_per_tok": None,
        "note": "size- and quant-matched control: 19.85 GB against the MoE's 18.56 GB",
    },
}


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def timers(action):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                            "derek@192.168.12.220",
                            "sudo systemctl %s arc-keepalive.timer arc-keepalive-deep.timer" % action],
                           capture_output=True, text=True, timeout=40)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def measure(model_key, topo, rep, ctx, nparallel, lengths, dec_reps):
    m = MODELS[model_key]
    print("\n=== %s / %s (rep %d) ===" % (model_key, topo, rep))
    proc, place = b3.start_probe(topo, ctx, nparallel, rep, model=m["path"],
                                 tag=model_key, logdir=LOGDIR)
    try:
        print("  placement OK :: %s" % ", ".join(place["gpu_buffers"]))
        for _ in range(3):                       # warm-up, discarded (R6)
            b3.post("warm the pipeline", 32)

        dec = []
        for _ in range(dec_reps):
            t, _w = b3.post(ff_ratecheck.PROMPT, 100)
            dec.append(round(t.get("predicted_per_second") or 0.0, 2))
        print("  decode      %8.2f tok/s  %s" % (statistics.fmean(dec), dec))

        pre = {}
        for size in lengths:
            rates = []
            for _ in range(2):
                t, _w = b3.post(b3.prompt_of(size, "%s%d" % (model_key, size)), 8)
                rates.append(round(t.get("prompt_per_second") or 0.0, 2))
            pre[size] = rates
            print("  prefill@%-5s %8.2f tok/s  %s" % (size, statistics.fmean(rates), rates))

        cen = ff_census.adapters_via_b70tools()
        arc = [(a.get("bdf"), a.get("local_committed_gb"), a.get("non_local_committed_gb"))
               for a in cen.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
        non_local = sum(x[2] or 0 for x in arc)
        print("  residency %s  non_local %.3f GB" % (arc, non_local))
    finally:
        b3.stop_probe(proc)

    return {"model": model_key, "kind": m["kind"], "params": m["params"], "quant": m["quant"],
            "topology": topo, "rep": rep, "context": ctx, "n_parallel": nparallel,
            "placement_assertion": topo, "placement_evidence": place["gpu_buffers"],
            "config_assertion": "load_report",
            "decode_reps": dec, "decode_mean": round(statistics.fmean(dec), 2),
            "prefill": pre,
            "prefill_mean": {k: round(statistics.fmean(v), 2) for k, v in pre.items()},
            "residency_per_bdf": arc, "non_local_total_gb": round(non_local, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description="B5: dense vs MoE at matched topology and warmth")
    ap.add_argument("--models", nargs="*", default=["moe-30b-a3b", "dense-qwen38-27b"])
    ap.add_argument("--topologies", nargs="*", default=["dual", "single"])
    ap.add_argument("--reps", type=int, default=2, help="interleaved repeats per model")
    ap.add_argument("--dec-reps", type=int, default=5)
    ap.add_argument("--lengths", type=int, nargs="*", default=[512, 2048])
    ap.add_argument("--context", type=int, default=16384)
    ap.add_argument("--np", dest="nparallel", type=int, default=2)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    for k in args.models:
        if not os.path.exists(MODELS[k]["path"]):
            print("  REFUSING: model file missing for %s: %s" % (k, MODELS[k]["path"]))
            return 2

    # R7 preflight: -np N SPLITS the context. Validate before anything goes offline.
    per_slot = args.context // args.nparallel
    need = max(args.lengths) + 64
    print("=== B5: dense vs MoE, topology and warmth held constant ===")
    print("  context %d (-np %d -> %d per slot), largest prompt needs ~%d"
          % (args.context, args.nparallel, per_slot, need))
    if need > per_slot:
        print("  REFUSING: prompt does not fit a slot. Raise -c to at least %d." % (need * args.nparallel))
        return 2

    ka = timers("stop")
    print("  fx99 keep-alive timers stopped: %s" % ka)
    io.open(SENTINEL, "w", encoding="utf-8").write(
        "B5 dense-vs-MoE. Production DOWN: single-card arms do not fit beside it.\n")
    subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
    time.sleep(12)
    print("  production stopped for the window")

    arms = []
    try:
        for topo in args.topologies:
            # Models INTERLEAVED within a topology, so drift shows up as within-model
            # disagreement rather than a fake between-model effect (R3).
            for rep in range(1, args.reps + 1):
                for mk in args.models:
                    arms.append(measure(mk, topo, rep, args.context, args.nparallel,
                                        args.lengths, args.dec_reps))
    finally:
        try:
            os.remove(SENTINEL)
        except OSError:
            pass
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        print("\n  production restored: ready=%s" % ff_cell.wait_for_ready(since=t0))
        if ka:
            print("  fx99 keep-alive timers restarted: %s" % timers("start"))

    print("\n=== RESULT ===")
    summary, verdicts = {}, {}
    for topo in args.topologies:
        summary[topo] = {}
        for mk in args.models:
            rows = [a for a in arms if a["model"] == mk and a["topology"] == topo]
            if not rows:
                continue
            d = [r["decode_mean"] for r in rows]
            drift = (max(d) - min(d)) / statistics.fmean(d) * 100 if len(d) > 1 else None
            p = {k: round(statistics.fmean([r["prefill_mean"][k] for r in rows]), 2)
                 for k in rows[0]["prefill_mean"]}
            summary[topo][mk] = {"decode": round(statistics.fmean(d), 2), "decode_arms": d,
                                 "within_model_drift_pct": round(drift, 2) if drift else None,
                                 "prefill": p, "kind": MODELS[mk]["kind"]}
            print("  %-7s %-18s decode %7.2f %s  drift %s%%  prefill %s"
                  % (topo, mk, summary[topo][mk]["decode"], d,
                     summary[topo][mk]["within_model_drift_pct"], p))

        moe = [k for k in summary[topo] if MODELS[k]["kind"] == "MoE"]
        dense = [k for k in summary[topo] if MODELS[k]["kind"] == "dense"]
        if moe and dense:
            mval = summary[topo][moe[0]]["decode"]
            for dk in dense:
                dval = summary[topo][dk]["decode"]
                ratio = mval / dval if dval else None
                drifts = [summary[topo][x]["within_model_drift_pct"] or 0.0 for x in (moe[0], dk)]
                worst = max(drifts)
                verdicts["%s/%s" % (topo, dk)] = {
                    "moe_decode": mval, "dense_decode": dval,
                    "ratio": round(ratio, 2) if ratio else None,
                    "worst_within_model_drift_pct": round(worst, 2),
                    # The historical claim is a RATIO (~6x). Judge the ratio, not the
                    # absolute numbers, since context and topology both moved.
                    "historical_ratio": 6.0,
                    "verdict": ("INCONCLUSIVE" if worst > 5.0 else
                                "SUPPORTED" if 5.0 <= ratio <= 7.0 else
                                "SMALLER THAN CLAIMED" if ratio < 5.0 else
                                "LARGER THAN CLAIMED"),
                }
                print("    -> %s vs %s at %s: MoE %.2f / dense %.2f = **%.2fx**  (claim ~6x, "
                      "worst within-model drift %.2f%%) -> %s"
                      % (moe[0], dk, topo, mval, dval, ratio, worst,
                         verdicts["%s/%s" % (topo, dk)]["verdict"]))

    row = {"ts": now(), "probe": "B5-DENSE-VS-MOE", "cell": "dense-vs-moe",
           "models": args.models, "topologies": args.topologies,
           "context": args.context, "n_parallel": args.nparallel,
           "arms": arms, "summary": summary, "verdicts": verdicts,
           "coresident": False, "keepalive_timers_stopped": ka,
           "historical_claim": "dense seats ~21-24 tok/s vs the 30B MoE's ~121, i.e. ~6x per seat",
           "confounds_removed": ["topology matched", "one model at a time (no mutual co-residency)",
                                 "same context", "warm-only with placement asserted"],
           "scope_limit": ("measured at -c %d, one model at a time. A 3B-active MoE doing less work "
                           "per token than a dense model is what MoE IS -- this tests whether the "
                           "~6x RATIO survives the confounds, not whether the gap is justified."
                           % args.context),
           "receipt_status": "CONTROLLED_REMEASUREMENT",
           "receipt_status_reason": "models interleaved within each topology; within-model drift is "
                                    "the floor; placement asserted from each load report"}
    if not args.no_ledger:
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
