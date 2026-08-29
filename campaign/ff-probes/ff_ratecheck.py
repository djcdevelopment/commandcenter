#!/usr/bin/env python3
"""FF-RATECHECK -- assert a rung is serving at its known-good rate.

Standing control. Run after EVERY config change, restart, or driver update.

Why this exists: on 2026-08-29 production was quietly wrong three separate times
in one day, and every health check passed each time.

  1. all 49 layers on ONE B70 (a stale index filter) -- correct output, half the
     hardware, ~1.5 GB of headroom instead of ~16
  2. ~10 GB spilled into host memory under co-resident benchmarking -- serve-arc
     prices spill at ~22%
  3. -ub 1024 promoted on llama-bench evidence -> 4x decode regression at
     -np 2, which llama-bench structurally cannot test (it has no -np)

Every one of those served CORRECT OUTPUT the whole time. Serviceability probes,
/health, and door proofs all passed. `correct-but-degraded` is this lab's
characteristic failure mode and nothing detected it.

FF-CENSUS answers "where are the weights". This answers "is it actually fast".

Design notes, each earned the hard way today:
  - timings come from the SERVER's own `timings` block, never wall clock. The
    door adds seconds of pipeline overhead that has nothing to do with inference.
  - readiness is gated on a REAL COMPLETION. /health returns 200 while the model
    is still loading (measured: a 503 and a 43 s call with no timings at all).
  - rep 1 is discarded. First eval pays graph/pipeline compile -- that artifact
    is what made the Flash "prefill cliff" look real until LZ1 refuted it.
  - the noise floor is the BETWEEN-REP spread, not a stddev over consecutive
    samples, which understates real noise by ~50x on this box.
  - per-process CPU counters are NOT used: llama-server runs under an S4U
    scheduled task and its counters read 0 while it is working.

Read-only against the rung (one small completion). Appends one FF-RATECHECK row.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
BASELINES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rate-baselines.json")
TOKEN_FRAGMENT = r"C:\work\commandcenter\hearth\var\gateway.cmd"

# A rung below FAIL_FRAC of its baseline is degraded, not noisy. 0.80 is chosen
# to catch the ~22% loss that serve-arc.cmd attributes to a partial VRAM spill --
# the quietest real degradation this box is known to produce.
# A baseline may not be drawn from data noisier than the effect it must detect.
MAX_BASELINE_SPREAD_PCT = 10.0

FAIL_FRAC = 0.80
WARN_FRAC = 0.90

PROMPT = "Explain consensus algorithms in depth."


def _token(env_name: str) -> str | None:
    """Read a rung's bearer from the gitignored fragment. Never printed."""
    if not env_name or not os.path.exists(TOKEN_FRAGMENT):
        return os.environ.get(env_name or "")
    pat = re.compile(r"^\s*set\s+%s=(.*)$" % re.escape(env_name), re.I)
    for line in io.open(TOKEN_FRAGMENT, encoding="utf-8", errors="replace"):
        m = pat.match(line)
        if m:
            return m.group(1).strip()
    return os.environ.get(env_name or "")


def _post(url: str, payload: dict, token: str | None, timeout: int = 600) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def measure(rung: dict, reps: int) -> dict:
    """Warm once, then measure `reps` times. Returns server-reported rates."""
    url = "http://127.0.0.1:%d/completion" % rung["port"]
    token = _token(rung.get("auth_env", ""))
    payload = {"prompt": PROMPT, "n_predict": rung.get("n_predict", 100),
               "temperature": 0, "cache_prompt": False}
    try:
        _post(url, {**payload, "n_predict": 4}, token, timeout=900)  # warm-up, discarded
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "warmup HTTP %s (503 => still loading; "
                                      "port-open != model-ready)" % e.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "warmup failed: %s" % exc}

    dec, pre = [], []
    for _ in range(reps):
        try:
            r = _post(url, payload, token, timeout=900)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "measure failed: %s" % exc}
        t = r.get("timings") or {}
        if not t:
            return {"ok": False, "error": "no timings block -- served during load?"}
        dec.append(t.get("predicted_per_second") or 0.0)
        pre.append(t.get("prompt_per_second") or 0.0)
    spread = (max(dec) - min(dec)) / statistics.fmean(dec) * 100 if len(dec) > 1 else None
    return {"ok": True, "decode_tok_s": round(statistics.fmean(dec), 2),
            "decode_reps": [round(x, 2) for x in dec],
            "prefill_tok_s": round(statistics.fmean(pre), 2),
            "repeat_spread_pct": round(spread, 2) if spread is not None else None}


def main() -> int:
    ap = argparse.ArgumentParser(description="Assert a rung's known-good serving rate")
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--set-baseline", action="store_true",
                    help="record the measurement AS the new baseline (use only on a rung "
                         "you have just verified by other means)")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(BASELINES, encoding="utf-8")) if os.path.exists(BASELINES) else {}
    rung = (baselines.get("rungs") or {}).get(args.rung)
    if rung is None:
        print("no rung %r in %s" % (args.rung, BASELINES))
        return 2

    m = measure(rung, args.reps)
    ts = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
    print("=== FF-RATECHECK: %s ===" % args.rung)
    if not m["ok"]:
        print("  FAIL -- %s" % m["error"])
        verdict = "FAIL"
    else:
        base = rung.get("baseline_decode_tok_s")
        print("  decode  %.2f tok/s   reps=%s   repeat-spread %.2f%%"
              % (m["decode_tok_s"], m["decode_reps"], m["repeat_spread_pct"] or 0.0))
        print("  prefill %.2f tok/s" % m["prefill_tok_s"])
        spread = m.get("repeat_spread_pct")
        if args.set_baseline and spread is not None and spread > MAX_BASELINE_SPREAD_PCT:
            print("  REFUSED to set a baseline: repeat spread %.2f%% exceeds %.0f%%."
                  % (spread, MAX_BASELINE_SPREAD_PCT))
            print("      The spread IS the noise floor. A baseline drawn from data noisier")
            print("      than the effect it must detect is worse than no baseline at all.")
            print("      (This guard exists because a 55.44 baseline was once set from a")
            print("       74.87%% spread on this very rung.)")
            verdict = "BASELINE-REFUSED"
        elif args.set_baseline:
            rung["baseline_decode_tok_s"] = m["decode_tok_s"]
            rung["baseline_set"] = ts
            rung["baseline_note"] = args.note or rung.get("baseline_note", "")
            json.dump(baselines, io.open(BASELINES, "w", encoding="utf-8"), indent=2)
            print("  baseline SET to %.2f tok/s" % m["decode_tok_s"])
            verdict = "BASELINE-SET"
        elif not base:
            print("  no baseline recorded -- run with --set-baseline on a verified rung")
            verdict = "NO-BASELINE"
        else:
            frac = m["decode_tok_s"] / base
            print("  baseline %.2f tok/s  =>  %.0f%% of known-good" % (base, frac * 100))
            if frac < FAIL_FRAC:
                print("  *** FAIL: %.0f%% of baseline. This is DEGRADATION, not noise." % (frac * 100))
                print("      Check, in order: device placement (ff_census.py), VRAM spill,")
                print("      and any batching flag changed since the baseline (-ub/-np/-c).")
                verdict = "FAIL"
            elif frac < WARN_FRAC:
                print("  ** WARN: %.0f%% of baseline." % (frac * 100))
                verdict = "WARN"
            else:
                print("  PASS")
                verdict = "PASS"

    if not args.no_ledger:
        row = {"ts": ts, "probe": "FF-RATECHECK", "rung": args.rung,
               "verdict": verdict, "measurement": m,
               "baseline_decode_tok_s": rung.get("baseline_decode_tok_s"),
               "note": args.note or None}
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("  -> appended to %s" % LEDGER)
    return 0 if verdict in ("PASS", "BASELINE-SET") else 1


if __name__ == "__main__":
    sys.exit(main())
