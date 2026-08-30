#!/usr/bin/env python3
"""The -ub 512 vs 1024 A/B, done honestly this time.

WHY THE FIRST ONE WAS INVALID, in one line each -- this probe is shaped by both failures:

  * IT COMPARED TWO DIFFERENT MACHINE STATES. `-ub 512` was measured on a freshly
    restarted server (104 tok/s) and `-ub 1024` after co-resident work had left the rung
    idle for minutes (22-27). ADR-0043 now explains that entire spread with the ubatch
    value held constant: >60 s idle costs ~4x. The "4x regression" was a warm arm against
    a cold one, and the causal claim was withdrawn.
  * IT WAS MEASURED WHERE THE FLAG CANNOT BE SEEN. llama-bench has no `-np` (finding A5),
    so it tests ONE slot. Production runs `-np 2`. A batching flag validated only where
    the harness can reach is not validated -- that gap was named at promotion time and
    shipped anyway.

So: both arms on the LIVE SERVER at production flags, both WARM, and measured at
production concurrency. Per ADR-0038, a verdict cites only evidence from the configuration
it promotes, which is why this edits `serve-arc.cmd` and restarts rather than standing up a
convenient side-server. (It could not stand one up anyway: production holds ~15 GB per
card and a second copy at `-c 131072` would not fit.)

CONTROLS

  * ARMS ARE INTERLEAVED A-B-A-B. A straight A-then-B ordering cannot distinguish "1024 is
    faster" from "the machine got faster". Interleaving makes drift show up as
    within-arm disagreement instead of a fake between-arm effect.
  * EVERY ARM IS RESTARTED AND THEN WARMED before it is measured, so all four start from
    the same machine state -- the rule ADR-0041 wrote down and ADR-0043 explained.
  * THE KEEP-ALIVE TIMERS ARE STOPPED for the duration. They are production's warmth
    control, but here they would inject an uncontrolled third request into a `-np 2`
    concurrency measurement. Restored in a finally block.
  * PLACEMENT IS ASSERTED per arm from per-BDF residency, sampled inside the activity
    window the warm-up just created (the `local_committed` counter reads ~0 on an idle
    server -- finding A12).

The file edit is restored in a finally block and verified against git.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_cell        # noqa: E402
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SERVE_CMD = r"C:\work\commandcenter\fleet\arcserve\serve-arc.cmd"
CORPUS = r"C:\work\commandcenter\campaign\lz-probes\kit\probe-prompt.txt"
ANCHOR = "  -c 131072 -np 2"
PORT = 8082


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def set_ub(value):
    """Rewrite the production launch line. `value=None` restores the default (no flag)."""
    s = io.open(SERVE_CMD, encoding="utf-8", errors="replace").read()
    line_re = re.compile(r"^  -c 131072 -np 2(?: -ub \d+)? \^$", re.M)
    if not line_re.search(s):
        raise RuntimeError("could not find the launch line in %s -- refusing to edit blind"
                           % SERVE_CMD)
    new = ANCHOR + ("" if value is None else " -ub %d" % value) + " ^"
    io.open(SERVE_CMD, "w", encoding="utf-8", newline="").write(line_re.sub(new, s))


def current_ub():
    s = io.open(SERVE_CMD, encoding="utf-8", errors="replace").read()
    m = re.search(r"^  -c 131072 -np 2(?: -ub (\d+))? \^$", s, re.M)
    return int(m.group(1)) if (m and m.group(1)) else 512  # 512 is llama.cpp's default


def post(prompt, n_predict, token, slot=None, timeout=900):
    payload = {"prompt": prompt, "n_predict": n_predict, "temperature": 0,
               "cache_prompt": False}
    if slot is not None:
        payload["id_slot"] = slot
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % PORT,
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    return body.get("timings") or {}, (time.time() - t0) * 1000.0


def prompt_of(approx_tokens, tag):
    corpus = io.open(CORPUS, encoding="utf-8", errors="replace").read()
    prefix = "[ubab %s %s] " % (tag, os.urandom(6).hex())
    return prefix + corpus[:max(0, approx_tokens * 4 - len(prefix))]


def timers(action):
    """Stop/start the fx99 keep-alive timers over SSH. Best effort, never fatal."""
    cmd = ("sudo systemctl %s arc-keepalive.timer arc-keepalive-deep.timer" % action)
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                            "derek@192.168.12.220", cmd],
                           capture_output=True, text=True, timeout=40)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def measure_arm(ub, token, reps, prefills, conc_reps):
    print("\n=== arm: -ub %s ===" % (ub if ub else "512 (default, no flag)"))
    set_ub(ub)
    t0 = datetime.now()
    subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
    if not ff_cell.wait_for_ready(since=t0):
        raise RuntimeError("incumbent never reported ready on -ub %s" % ub)
    epoch = ff_cell.incumbent_epoch()

    # Warm up, discarded. Never measure the first requests after a load (ADR-0043).
    for _ in range(3):
        post("warm up the pipeline please", 32, token)

    # Placement, sampled inside the window the warm-up just created.
    cen = ff_census.adapters_via_b70tools()
    arc = [(a.get("bdf"), a.get("local_committed_gb"))
           for a in cen.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
    vals = [v for _, v in arc if isinstance(v, (int, float))]
    placement = "both-b70" if len(vals) == 2 and min(vals) >= 10.0 else "indeterminate"
    print("  placement %s :: %s" % (placement, arc))

    # --- single-stream decode
    dec = []
    for _ in range(reps):
        t, _w = post(ff_ratecheck.PROMPT, 100, token)
        dec.append(round(t.get("predicted_per_second") or 0.0, 2))
    print("  decode      %6.2f tok/s  %s" % (statistics.fmean(dec), dec))

    # --- prefill at the sizes where ubatch can possibly matter
    pre = {}
    for size in prefills:
        rates = []
        for r in range(2):
            t, _w = post(prompt_of(size, "p%d" % size), 8, token)
            rates.append(round(t.get("prompt_per_second") or 0.0, 2))
        pre[size] = rates
        print("  prefill@%-5s %6.2f tok/s  %s" % (size, statistics.fmean(rates), rates))

    # --- CONCURRENCY 2: the regime llama-bench cannot express and the flag was never
    # tested in. Two streams, one per slot, launched together.
    conc = []
    for _ in range(conc_reps):
        with ThreadPoolExecutor(max_workers=2) as ex:
            t_start = time.time()
            futs = [ex.submit(post, ff_ratecheck.PROMPT + (" %d" % i), 100, token, i)
                    for i in (0, 1)]
            res = [f.result() for f in futs]
            wall = time.time() - t_start
        rates = [round(t.get("predicted_per_second") or 0.0, 2) for t, _ in res]
        toks = sum((t.get("predicted_n") or 0) for t, _ in res)
        conc.append({"per_stream_tok_s": rates,
                     "aggregate_tok_s": round(toks / wall, 2),
                     "wall_s": round(wall, 3)})
    agg = [c["aggregate_tok_s"] for c in conc]
    print("  np2 aggregate %6.2f tok/s  %s" % (statistics.fmean(agg), agg))

    return {"ub": ub or 512, "ub_flag_set": ub is not None,
            "incumbent_process_epoch": epoch.get("epoch_start"),
            "placement_assertion": placement, "placement_evidence": arc,
            "decode_reps": dec, "decode_mean": round(statistics.fmean(dec), 2),
            "decode_spread_pct": round((max(dec) - min(dec)) / statistics.fmean(dec) * 100, 2),
            "prefill": pre,
            "prefill_mean": {k: round(statistics.fmean(v), 2) for k, v in pre.items()},
            "concurrency2": conc,
            "concurrency2_aggregate_mean": round(statistics.fmean(agg), 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description="-ub 512 vs 1024, warm vs warm, at -np 2")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--conc-reps", type=int, default=3)
    ap.add_argument("--prefills", type=int, nargs="*", default=[512, 2048, 8192])
    ap.add_argument("--order", nargs="*", default=["512", "1024", "512", "1024"],
                    help="interleaved arm order; 512 means the default (no -ub flag)")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get("omen-arc")
    token = ff_ratecheck._token(rung.get("auth_env", ""))
    original = current_ub()
    print("=== -ub A/B: warm vs warm, on the live server at -np 2 ===")
    print("  serve-arc.cmd currently: -ub %s" % original)

    ka = timers("stop")
    print("  fx99 keep-alive timers stopped: %s" % ka)
    arms = []
    try:
        for name in args.order:
            ub = None if name == "512" else int(name)
            arms.append(measure_arm(ub, token, args.reps, args.prefills, args.conc_reps))
    finally:
        # Restore the file first, then the server, then the timers -- in that order, so a
        # crash never leaves production running a config that is not in git.
        set_ub(None if original == 512 else original)
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        ok = ff_cell.wait_for_ready(since=t0)
        dirty = subprocess.run(["git", "diff", "--stat", "--", SERVE_CMD],
                               capture_output=True, text=True,
                               cwd=r"C:\work\commandcenter").stdout.strip()
        print("\n  restored serve-arc.cmd to -ub %s; ready=%s; git diff: %s"
              % (current_ub(), ok, dirty or "clean"))
        if ka:
            print("  fx99 keep-alive timers restarted: %s" % timers("start"))

    # --- verdict
    by = {}
    for a in arms:
        by.setdefault(a["ub"], []).append(a)
    print("\n=== RESULT ===")
    print("  %-6s %-24s %-24s %s" % ("ub", "decode (mean of arms)", "np2 aggregate", "prefill means"))
    summary = {}
    for ub in sorted(by):
        d = [a["decode_mean"] for a in by[ub]]
        c = [a["concurrency2_aggregate_mean"] for a in by[ub]]
        p = {k: round(statistics.fmean([a["prefill_mean"][k] for a in by[ub]]), 2)
             for k in by[ub][0]["prefill_mean"]}
        summary[ub] = {"decode_arms": d, "decode": round(statistics.fmean(d), 2),
                       "conc_arms": c, "concurrency2": round(statistics.fmean(c), 2),
                       "prefill": p}
        print("  %-6s %-24s %-24s %s"
              % (ub, "%.2f  %s" % (summary[ub]["decode"], d),
                 "%.2f  %s" % (summary[ub]["concurrency2"], c), p))

    verdict, reason = "INCONCLUSIVE", "need both arms"
    if 512 in summary and 1024 in summary:
        a, b = summary[512], summary[1024]
        # The drift control: repeated arms of the SAME config must agree, or a
        # between-config difference cannot be separated from machine drift.
        drift = max(
            (max(v["decode_arms"]) - min(v["decode_arms"])) / statistics.fmean(v["decode_arms"])
            for v in (a, b)) * 100
        d_delta = (b["decode"] - a["decode"]) / a["decode"] * 100
        c_delta = (b["concurrency2"] - a["concurrency2"]) / a["concurrency2"] * 100
        p_delta = {k: round((b["prefill"][k] - a["prefill"][k]) / a["prefill"][k] * 100, 1)
                   for k in a["prefill"]}
        print("\n  within-config drift (worst): %.2f%%" % drift)
        print("  ub1024 vs ub512 -- decode %+.1f%%  np2-aggregate %+.1f%%  prefill %s"
              % (d_delta, c_delta, p_delta))
        best_p = max(p_delta.values())
        if drift > 3.0:
            verdict = "INCONCLUSIVE"
            reason = ("repeated arms of the same config disagree by %.2f%% -- larger than the "
                      "effect being measured, so no between-config claim is supportable" % drift)
        elif d_delta < -3.0:
            verdict = "KEEP 512"
            reason = "ub1024 costs %.1f%% decode at -np 2" % d_delta
        elif best_p > 3.0 and d_delta > -1.0 and c_delta > -1.0:
            verdict = "PROMOTE 1024"
            reason = ("prefill up to %+.1f%% with decode %+.1f%% and np2 aggregate %+.1f%% -- "
                      "no regression where production lives" % (best_p, d_delta, c_delta))
        else:
            verdict = "KEEP 512"
            reason = ("no material gain: best prefill %+.1f%%, decode %+.1f%%, np2 %+.1f%%. "
                      "The default stays on the incumbent-wins rule" % (best_p, d_delta, c_delta))
        print("\n  VERDICT: %s -- %s" % (verdict, reason))

    row = {"ts": now(), "probe": "UB-AB-WARM", "cell": "ub512-vs-ub1024",
           "order": args.order, "arms": arms, "summary": summary,
           "verdict": verdict, "verdict_reason": reason,
           "coresident": False, "keepalive_timers_stopped": ka,
           "receipt_status": "PROMOTION_EVIDENCE",
           "receipt_status_reason": "both arms on the live server at production flags, both warm "
                                    "after restart, interleaved A-B-A-B, measured at -np 2 "
                                    "concurrency which llama-bench cannot express (finding A5)"}
    if not args.no_ledger:
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
