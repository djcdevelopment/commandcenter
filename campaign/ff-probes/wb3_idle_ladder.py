#!/usr/bin/env python3
"""W-B3 -- how long does the rung have to sit idle before it serves degraded?

W-B2 removed co-residency from the picture. A restarted incumbent that never shared the
cards with anything read, from its own log:

    epoch 0:11-0:15   88.5 | 105.97  106.36  105.90  105.89     <- flat, 100% of baseline
    epoch 5:23-5:33   68.6 |  74.37   57.89   31.17   26.60
    epoch 10:23-10:33 68.8 |  74.09   59.17   32.12   27.96

Two things follow immediately. The loss does NOT need a co-tenant, and it does not
accumulate with epoch age -- t+5 and t+10 are the same curve, so ~4.5 minutes of idle
between them RESET it rather than deepening it. What differs between the flat burst and
the collapsing ones is simply that the first followed a model load with no idle gap.

So the variable is idle time, and the questions this probe answers are the operational
ones:

  1. THRESHOLD -- how much idle is needed? If it is tens of seconds, then a door that
     fields an agent's question every few minutes is serving degraded ESSENTIALLY ALWAYS,
     and every benchmark number this lab holds was taken in a regime real traffic never
     sees.
  2. MITIGATION -- does a trivial keep-alive prevent it? One cheap request every N
     seconds costs almost nothing. If that holds the rate, the fix is a timer.

Method: restart, confirm the fresh rate, then for each idle duration sit still for
exactly that long and take an identical burst. The keep-alive arm sits for the LONGEST
duration but pings with a 1-token request on an interval, so it is the same wall-clock
gap with the idleness removed -- the only difference that matters.

*** THE ARMS SHARE ONE SERVER, SO THEY ARE ONLY INDEPENDENT UNTIL THE FIRST COLLAPSE. ***
Once an arm degrades the incumbent, every later arm starts from a degraded state and
measures recovery rather than onset. In the first full run this invalidated the
keep-alive arm outright: it ran after the 300 s idle arm and so tested "does pinging a
collapsed server revive it" (no) instead of "does pinging prevent collapse". Run the
keep-alive arm on its OWN invocation -- `--idles 0` -- so it starts from a fresh server.
The ladder arms themselves are trustworthy only up to and including the first one that
falls; read nothing into the ones after it.

Per-rep series are recorded, never just the mean: the within-burst slope IS the signal.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
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


def ping(rung, token):
    """One-token request: the cheapest thing that counts as 'not idle'."""
    body = json.dumps({"prompt": "hi", "n_predict": 1, "temperature": 0,
                       "cache_prompt": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % rung["port"],
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    with urllib.request.urlopen(req, timeout=120) as r:
        json.load(r)


def burst(rung, reps):
    """A measurement burst WITHOUT ff_ratecheck's warm-up.

    ff_ratecheck discards a warm-up request before measuring, which is right for a health
    gate and wrong here: after an idle gap the first request is part of the phenomenon.
    """
    url = "http://127.0.0.1:%d/completion" % rung["port"]
    token = ff_ratecheck._token(rung.get("auth_env", ""))
    payload = {"prompt": ff_ratecheck.PROMPT, "n_predict": rung.get("n_predict", 100),
               "temperature": 0, "cache_prompt": False}
    out = []
    for _ in range(reps):
        r = ff_ratecheck._post(url, payload, token, timeout=900)
        t = r.get("timings") or {}
        out.append(round(t.get("predicted_per_second") or 0.0, 2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="W-B3: idle-duration ladder + keep-alive arm")
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--idles", type=int, nargs="*", default=[0, 30, 60, 120, 300])
    ap.add_argument("--keepalive-idle", type=int, default=300)
    ap.add_argument("--keepalive-every", type=int, default=20)
    ap.add_argument("--no-restart", dest="restart", action="store_false", default=True)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get(args.rung)
    if rung is None:
        print("no rung %r" % args.rung)
        return 2
    base = rung.get("baseline_decode_tok_s")
    token = ff_ratecheck._token(rung.get("auth_env", ""))

    print("=== W-B3: idle-duration ladder (no co-tenant at any point) ===")
    if args.restart:
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        if not ff_cell.wait_for_ready(since=t0):
            print("  FAIL -- incumbent never reported ready. Aborting.")
            return 1
    epoch = ff_cell.incumbent_epoch()
    print("  epoch: %s\n" % epoch.get("epoch_start"))
    print("  %-18s %-8s %s" % ("arm", "mean", "per-rep series"))

    arms = []
    for idle in args.idles:
        if idle:
            time.sleep(idle)
        reps = burst(rung, args.reps)
        mean = round(sum(reps) / len(reps), 2)
        arms.append({"arm": "idle_%ds" % idle, "idle_s": idle, "keepalive": False,
                     "reps": reps, "mean_decode_tok_s": mean,
                     "first_rep": reps[0], "last_rep": reps[-1],
                     "within_burst_slope": round(reps[-1] / reps[0], 3) if reps[0] else None,
                     "fraction_of_baseline": round(mean / base, 3) if base else None})
        print("  %-18s %-8s %s" % ("idle %ds" % idle, mean, reps))

    # Keep-alive arm: same wall-clock gap, idleness removed.
    end = time.time() + args.keepalive_idle
    pings = 0
    while time.time() < end:
        try:
            ping(rung, token)
            pings += 1
        except Exception:  # noqa: BLE001
            break
        time.sleep(args.keepalive_every)
    reps = burst(rung, args.reps)
    mean = round(sum(reps) / len(reps), 2)
    arms.append({"arm": "keepalive_%ds_every_%ds" % (args.keepalive_idle, args.keepalive_every),
                 "idle_s": args.keepalive_idle, "keepalive": True, "pings": pings,
                 "reps": reps, "mean_decode_tok_s": mean,
                 "first_rep": reps[0], "last_rep": reps[-1],
                 "within_burst_slope": round(reps[-1] / reps[0], 3) if reps[0] else None,
                 "fraction_of_baseline": round(mean / base, 3) if base else None})
    print("  %-18s %-8s %s  (%d pings)"
          % ("keepalive %ds" % args.keepalive_idle, mean, reps, pings))

    fresh = arms[0]["mean_decode_tok_s"]
    threshold = None
    for a in arms:
        if not a["keepalive"] and a["mean_decode_tok_s"] < 0.90 * fresh:
            threshold = a["idle_s"]
            break
    ka = arms[-1]
    ka_holds = ka["mean_decode_tok_s"] >= 0.90 * fresh
    print("\n  fresh (idle 0) = %.2f tok/s" % fresh)
    print("  first idle duration below 90%% of fresh: %s"
          % ("%ds" % threshold if threshold is not None else "none in this ladder"))
    print("  keep-alive holds the rate: %s (%.2f tok/s over the same %ds gap)"
          % (ka_holds, ka["mean_decode_tok_s"], args.keepalive_idle))

    row = {"ts": now(), "probe": "W-B3-IDLE-LADDER", "cell": "idle-ladder",
           "incumbent_process_epoch": epoch.get("epoch_start"),
           "coresident": False, "cotenant_class": None,
           "baseline_decode_tok_s": base, "fresh_mean_decode_tok_s": fresh,
           "arms": arms,
           "degradation_threshold_idle_s": threshold,
           "keepalive_holds_rate": ka_holds,
           "receipt_status": "MECHANISM_DISCRIMINATION",
           "receipt_status_reason": "no co-tenant at any point; idle duration is the only variable, "
                                    "and the keep-alive arm holds wall-clock constant while removing idleness"}
    if not args.no_ledger:
        append(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
