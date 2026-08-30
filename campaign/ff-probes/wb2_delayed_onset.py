#!/usr/bin/env python3
"""W-B2 -- is the degradation DELAYED, and does it need a co-tenant at all?

W-B measured the incumbent 6 seconds after the co-tenant exited and found every class
clean. Minutes later, with nothing else on the cards and a 4-minute-old epoch, the same
server read 40% of baseline. So W-B's after-window was simply too short: whatever this
is, it does not arrive at co-tenant exit.

Two further structures showed up in that reading and both are measurable:

  * WITHIN a burst the rate decays monotonically -- 62.95, 47.46, 30.29, 29.31.
  * ACROSS a ~1 minute gap it partially recovers, then decays again from a similar
    starting point -- the next burst opened at 69.67 and fell to 27.59.

That is the shape of the "sustained-rate decay that recovers after idle" account which
was RETRACTED earlier the same day. The retraction was correct about the case it tested
-- 40 back-to-back requests on a FRESH server held 102-107 flat -- so the two
observations are not in conflict. They are two different machine states, and nobody had
measured the second one deliberately.

This probe separates them. It restarts the incumbent, then samples the SAME rate check
on a schedule with NO co-tenant at any point. The control it provides is the one W-B
lacked:

  * if a co-tenant-free server degrades on the same clock, co-residency is not necessary
    and ADR-0041's trigger should be re-stated in terms of epoch age or cumulative load;
  * if it stays flat, the class-3 co-tenancy is implicated with a DELAYED onset, and
    ADR-0041 survives with its after-window corrected from seconds to minutes.

Each sample records the per-rep series, not just the mean: the within-burst slope is the
signal, and a mean hides it.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_cell        # noqa: E402
import ff_ratecheck   # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def append(row):
    with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def slope(reps):
    """Fraction of the first rep that the last rep retains. <1 means within-burst decay."""
    if not reps or len(reps) < 2 or not reps[0]:
        return None
    return round(reps[-1] / reps[0], 3)


def main() -> int:
    ap = argparse.ArgumentParser(description="W-B2: delayed onset, with no co-tenant")
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--marks", type=int, nargs="*", default=[0, 300, 600, 900],
                    help="seconds after the ready marker at which to sample")
    ap.add_argument("--restart", action="store_true", default=True)
    ap.add_argument("--no-restart", dest="restart", action="store_false")
    ap.add_argument("--label", default="wb2-no-cotenant")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get(args.rung)
    if rung is None:
        print("no rung %r" % args.rung)
        return 2
    base = rung.get("baseline_decode_tok_s")

    print("=== W-B2: %s ===" % args.label)
    if args.restart:
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        if not ff_cell.wait_for_ready(since=t0):
            print("  FAIL -- incumbent never reported ready. Aborting.")
            return 1
    epoch = ff_cell.incumbent_epoch()
    print("  epoch: %s" % epoch.get("epoch_start"))
    ready_at = time.time()

    samples = []
    for mark in args.marks:
        wait = ready_at + mark - time.time()
        while wait > 0:
            time.sleep(min(wait, 30))
            wait = ready_at + mark - time.time()
        m = ff_ratecheck.measure(rung, args.reps)
        age_s = round(time.time() - ready_at)
        if not m.get("ok"):
            print("  t+%-5s FAILED: %s" % (mark, m.get("error")))
            samples.append({"mark_s": mark, "epoch_age_s": age_s, "ok": False,
                            "error": m.get("error")})
            continue
        frac = m["decode_tok_s"] / base if base else None
        sl = slope(m["decode_reps"])
        print("  t+%-5s epoch_age=%-5ss  %6.2f tok/s (%3.0f%%)  reps=%s  within-burst %s"
              % (mark, age_s, m["decode_tok_s"], (frac or 0) * 100, m["decode_reps"], sl))
        samples.append({"mark_s": mark, "epoch_age_s": age_s, "ok": True,
                        "decode_tok_s": m["decode_tok_s"], "reps": m["decode_reps"],
                        "fraction_of_baseline": round(frac, 3) if frac else None,
                        "within_burst_slope": sl,
                        "repeat_spread_pct": m.get("repeat_spread_pct")})

    good = [s for s in samples if s.get("ok")]
    verdict, reason = "INCONCLUSIVE", "fewer than two usable samples"
    if len(good) >= 2:
        first, worst = good[0], min(good, key=lambda s: s["decode_tok_s"])
        rel = worst["decode_tok_s"] / first["decode_tok_s"]
        if rel < 0.97:
            verdict = "DEGRADES-WITHOUT-COTENANT"
            reason = ("fell to %.2fx of its own t+0 rate by t+%ss with nothing else on the "
                      "cards -- co-residency is NOT necessary"
                      % (rel, worst["mark_s"]))
        else:
            verdict = "STABLE-WITHOUT-COTENANT"
            reason = ("held %.2fx of its t+0 rate across the whole schedule -- a co-tenant-free "
                      "server does not degrade on this clock, so the class-3 co-tenancy is "
                      "implicated with a DELAYED onset" % rel)
    print("\n  VERDICT %s -- %s" % (verdict, reason))

    row = {"ts": now(), "probe": "W-B2-DELAYED-ONSET", "cell": args.label,
           "restarted": args.restart, "incumbent_process_epoch": epoch.get("epoch_start"),
           "cotenant_class": None, "coresident": False,
           "baseline_decode_tok_s": base, "samples": samples,
           "verdict": verdict, "verdict_reason": reason,
           "receipt_status": "MECHANISM_DISCRIMINATION",
           "receipt_status_reason": "control for W-B: identical rate schedule with no co-tenant at any point"}
    if not args.no_ledger:
        append(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
