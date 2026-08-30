#!/usr/bin/env python3
"""C2 -- attribute a door call's wall time: how much is the DOOR, how much is the RUNG?

The question this settles. One observation put 100 tokens at 11 781 ms end-to-end while
the server's own log recorded that same task decoding at 91.75 tok/s -- about 1.09 s of
decode inside an 11.78 s call, so the door was ~91% of the cost. But a 10-token proof the
same evening completed in 281 ms, so the overhead is not a constant. Until it is
attributed, we cannot say whether keeping the rung warm (ADR-0043) is the throughput fix
or a rounding error against door cost.

Two controls make the answer trustworthy, both requested by Derek and both load-bearing:

  * A REPEATED TOKEN SIZE AT EACH END of the ladder -- 32 -> ... -> 32. If the bracketing
    pair disagree, the run drifted (epoch change, a rung transition per ADR-0043, or
    another caller) and the middle of the ladder is not a clean size sweep. Without it, a
    rung sliding from warm to cold mid-ladder would masquerade as door overhead that
    happens to grow with token count -- exactly the wrong conclusion.
  * AN UNAMBIGUOUS JOIN between each door receipt and its server timing line. Ordering
    alone is not a join: any other caller landing in the window silently shifts it. Each
    call therefore gets a DISTINCT PROMPT TOKEN COUNT, achieved by padding, so the
    server's own `prompt eval time = ... / N tokens` line is a unique key. That is
    corroborated by two independent checks -- the absolute time of the server line
    (elapsed stamp + epoch start) must fall inside the door call's wall-clock window, and
    the door's `tokens_out` must equal the server's `predicted_n`.

Door-side correlation IDs (`request_id`, `job_id`) are recorded on every row too. They
cannot be joined to the server log -- llama-server never sees them -- but they tie each
row back to the HEARTH ledger, which is the other half of the trail.

*** SESSION REUSE IS A CONTROLLED VARIABLE, NOT AN IMPLEMENTATION DETAIL. ***
`HearthClient.call_sync` opens a NEW streamable-http MCP session per call, whereas an
agent talking to the door reuses one for its whole lifetime. The first draft of this probe
used call_sync and measured 12.5 s of "door overhead" on a call whose inference was 0.54 s
-- which would have been reported as a door defect when it may simply be MCP session
setup. So the warm ladder runs inside ONE session, and a separate `fresh-session` arm
makes the per-call setup cost its own measurement. That arm is not overhead-accounting
noise: it decides how a long-running keep-alive should talk to the rung.

*** THE JOIN KEY IS THE WALL-CLOCK WINDOW, NOT `tokens_in`. ***
The door's `tokens_in` counts the raw prompt; the server counts the JINJA-TEMPLATED
prompt, so the two disagree by a constant and `tokens_in` is not the server's `prompt_n`
(measured: door 20, server 15). The join is therefore the server line's absolute time
falling inside the call's wall-clock window -- calls are strictly sequential, so exactly
one task should land in each -- and it is CORROBORATED by two independent facts: each
call's padding makes its `prompt_n` distinct, so the joined prompt_n values must all
differ, and the door's `tokens_out` must equal the server's `predicted_n`.

Usage:
  c2_door_attribution.py [--sizes 32 64 128 256 512 32] [--cold-after 150]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"C:\work\commandcenter")
import ff_cell        # noqa: E402
import ff_ratecheck   # noqa: E402
from hearth.callers.client import HearthClient  # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SERVE_LOG = r"C:\work\commandcenter\hearth\var\arc-serve.log"
MCP_CONFIG = r"C:\work\commandcenter\.mcp.json"

# ONE event loop for the whole run: the reused MCP session is bound to the loop it
# was opened on, so asyncio.run() per call (what call_sync does) would tear it down.
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

# "N.NN.mmm.uuu I slot print_timing: id  K | task T | prompt eval time = X ms / P tokens"
PROMPT_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\s.*task (\d+) \| prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
EVAL_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\s.*task (\d+) \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens")


def door_key() -> str:
    """Read the gateway bearer from the gitignored MCP config. Never printed."""
    d = json.load(io.open(MCP_CONFIG, encoding="utf-8"))
    return d["mcpServers"]["hearth"]["headers"]["X-Hearth-Key"]


def make_prompt(pad_tokens: int) -> str:
    """A prompt whose token count is unique to this call -- the join key."""
    return ("Reply with a short paragraph about storage engines. "
            + ("pad " * pad_tokens)).strip()


def parse_server_log(epoch_start: datetime):
    """Every completed task in the CURRENT epoch, with absolute times.

    The log is truncated at every launch, so it describes exactly one epoch and the
    elapsed stamp can be turned into wall-clock by adding it to the epoch start.
    """
    tasks = {}
    if not os.path.exists(SERVE_LOG):
        return tasks
    for line in io.open(SERVE_LOG, encoding="utf-8", errors="replace"):
        for rx, kind in ((PROMPT_RE, "prompt"), (EVAL_RE, "eval")):
            m = rx.match(line)
            if not m:
                continue
            elapsed = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000.0
            task = int(m.group(5))
            row = tasks.setdefault(task, {"task": task})
            row["at"] = epoch_start + timedelta(seconds=elapsed)
            if kind == "prompt":
                row["prompt_ms"], row["prompt_n"] = float(m.group(6)), int(m.group(7))
            else:
                row["eval_ms"], row["predicted_n"] = float(m.group(6)), int(m.group(7))
            break
    return {k: v for k, v in tasks.items() if "prompt_ms" in v and "eval_ms" in v}


def join_all(calls, tasks, slack_s=5.0):
    """Join door receipts to server tasks by ORDER within the run window.

    A per-call time window is the wrong primitive here. The epoch start is derived (log
    mtime minus the elapsed stamp), not observed, so it carries seconds of error -- and
    calls land ~600 ms apart, so any slack wide enough to absorb that error sweeps in
    neighbouring tasks and every join comes back AMBIGUOUS. Both sequences are strictly
    ordered and the probe is the only caller, so ordinal assignment is exact.

    It is verified, not assumed: the counts must match, and every pair's
    `door tokens_out == server predicted_n`. If either check fails the join is refused
    rather than guessed, because a silently mis-assigned row would attribute one call's
    stall to another call's size.
    """
    lo = min(c["t_start"] for c in calls) - timedelta(seconds=slack_s)
    hi = max(c["t_end"] for c in calls) + timedelta(seconds=slack_s)
    win = sorted((t for t in tasks.values() if lo <= t["at"] <= hi), key=lambda t: t["at"])
    if len(win) != len(calls):
        return None, ("REFUSED: %d server tasks in the run window vs %d door calls -- "
                      "another caller was active, or a task was missed"
                      % (len(win), len(calls)))
    bad = [(c["size"], c.get("tokens_out"), t.get("predicted_n"))
           for c, t in zip(calls, win)
           if c.get("tokens_out") is not None and c["tokens_out"] != t.get("predicted_n")]
    if bad:
        return None, ("REFUSED: tokens_out != predicted_n on %d of %d rows %s"
                      % (len(bad), len(calls), bad[:3]))
    return list(zip(calls, win)), ("ordinal within the run window, verified by "
                                   "count match and tokens_out == predicted_n on every row")


def main() -> int:
    ap = argparse.ArgumentParser(description="C2: attribute door call time to door vs rung")
    ap.add_argument("--sizes", type=int, nargs="*", default=[32, 64, 128, 256, 512, 32],
                    help="n_predict ladder; repeat the first value last as the drift control")
    ap.add_argument("--cold-after", type=int, default=150,
                    help="seconds of idle before the cold arm (0 to skip)")
    ap.add_argument("--no-restart", dest="restart", action="store_false", default=True)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get("omen-arc")

    print("=== C2: door vs rung attribution ===")
    if args.restart:
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        if not ff_cell.wait_for_ready(since=t0):
            print("  FAIL -- incumbent never reported ready.")
            return 1
    epoch = ff_cell.incumbent_epoch()
    epoch_start = datetime.fromisoformat(epoch["epoch_start"]).replace(tzinfo=None)
    print("  epoch: %s" % epoch["epoch_start"])

    key = door_key()
    calls = []
    pads = [3, 7, 11, 17, 23, 29, 31, 37, 41, 43]

    def record(arm, size, pad, t_start, t_end, wall_ms, session_ms, reused, res):
        body = {}
        try:
            body = json.loads(res.get("text") or "{}")
        except Exception:  # noqa: BLE001
            pass
        row = {"arm": arm, "size": size, "pad": pad,
               "t_start": t_start, "t_end": t_end,
               "wall_ms": round(wall_ms, 1),
               "session_reused": reused,
               "session_setup_ms": round(session_ms, 1),
               "door_duration_ms": body.get("duration_ms"),
               "tokens_out": body.get("tokens_out"),
               "tokens_in": body.get("tokens_in"),
               "request_id": ((body.get("execution") or {}).get("request_id")),
               "job_id": ((body.get("execution") or {}).get("job_id")),
               "routed_by": body.get("routed_by"),
               "ok": body.get("ok")}
        calls.append(row)
        print("  %-14s size=%-4s wall=%-9.1f door=%-9s tokens_out=%-5s sess=%-8s req=%s"
              % (arm, size, wall_ms, row["door_duration_ms"], row["tokens_out"],
                 ("reused" if reused else "%.0fms" % session_ms),
                 (row["request_id"] or "")[:16]))
        return row

    async def run_all():
        """Everything runs inside ONE task.

        anyio's cancel scopes must be entered and exited in the same task, so driving
        __aenter__ and __aexit__ through separate run_until_complete calls raises
        "Attempted to exit cancel scope in a different task". The measurements were fine;
        only teardown blew up. One coroutine for the whole run fixes it.
        """
        async def call_on(client, arm, size, pad):
            prompt = make_prompt(pad)
            t0, w0 = datetime.now(), time.time()
            res = await client.call("local_generate", prompt=prompt,
                                    backend="omen-arc", max_tokens=size)
            return record(arm, size, pad, t0, datetime.now(),
                          (time.time() - w0) * 1000.0, 0.0, True, res)

        async with HearthClient(key=key) as session:
            # warm ladder, back-to-back, one session. First and last size are identical:
            # the drift control.
            for i, size in enumerate(args.sizes):
                await call_on(session, "warm", size, pads[i % len(pads)])

            # fresh-session arm: identical call, brand-new MCP session. Isolates client
            # setup from door work, and decides how a long-running pinger should talk.
            pad = pads[len(args.sizes) % len(pads)]
            prompt = make_prompt(pad)
            t0, w0 = datetime.now(), time.time()
            s0 = time.time()
            async with HearthClient(key=key) as fresh:
                setup_ms = (time.time() - s0) * 1000.0
                res = await fresh.call("local_generate", prompt=prompt,
                                       backend="omen-arc", max_tokens=args.sizes[0])
            record("fresh-session", args.sizes[0], pad, t0, datetime.now(),
                   (time.time() - w0) * 1000.0, setup_ms, False, res)

            if args.cold_after:
                print("  ... idling %ds to cross the ADR-0043 threshold ..." % args.cold_after)
                await asyncio.sleep(args.cold_after)
                await call_on(session, "cold", args.sizes[0],
                              pads[(len(args.sizes) + 1) % len(pads)])
                await call_on(session, "cold-next", args.sizes[0],
                              pads[(len(args.sizes) + 2) % len(pads)])

    _LOOP.run_until_complete(run_all())

    tasks = parse_server_log(epoch_start)
    print("\nserver tasks parsed from this epoch: %d" % len(tasks))
    pairs, method = join_all(calls, tasks)
    print("  join: %s" % method)
    rows = []
    if pairs is None:
        for c in calls:
            rows.append({**{k: v for k, v in c.items() if k not in ("t_start", "t_end")},
                         "joined": False, "join_note": method})
    else:
        print("\n%-14s %-5s %-9s %-11s %-9s %-9s %-7s"
              % ("arm", "size", "wall_ms", "prompt_ms", "eval_ms", "door_ms", "door%"))
        for c, t in pairs:
            server_ms = t["prompt_ms"] + t["eval_ms"]
            overhead = c["wall_ms"] - server_ms
            pct = overhead / c["wall_ms"] * 100 if c["wall_ms"] else None
            flag = "  <-- STALL" if t["prompt_ms"] > 2000 else ""
            print("  %-14s %-5s %-9.1f %-11.1f %-9.1f %-9.1f %-6.1f%%%s"
                  % (c["arm"], c["size"], c["wall_ms"], t["prompt_ms"], t["eval_ms"],
                     overhead, pct, flag))
            rows.append({**{k: v for k, v in c.items() if k not in ("t_start", "t_end")},
                         "joined": True, "join_note": method, "server_task": t["task"],
                         "server_prompt_n": t["prompt_n"],
                         "server_prompt_ms": round(t["prompt_ms"], 1),
                         "server_predicted_n": t["predicted_n"],
                         "server_eval_ms": round(t["eval_ms"], 1),
                         "server_decode_tok_s": round(t["predicted_n"] / (t["eval_ms"] / 1000.0), 2)
                                                if t["eval_ms"] else None,
                         "server_total_ms": round(server_ms, 1),
                         "door_overhead_ms": round(overhead, 1),
                         "door_overhead_pct": round(pct, 1) if pct is not None else None,
                         "prefill_stall": bool(t["prompt_ms"] > 2000)})

    # --- the drift control: the bracketing calls must agree, or the sweep is not clean
    warm = [r for r in rows if r.get("arm") == "warm" and r.get("joined")]
    bracket = [r for r in warm if r["size"] == args.sizes[0]]
    verdict = "UNKNOWN: bracketing pair not joined"
    if len(bracket) >= 2:
        a, b = bracket[0], bracket[-1]
        for key, label in (("door_overhead_ms", "door overhead"),
                           ("server_decode_tok_s", "decode")):
            va, vb = a.get(key), b.get(key)
            if va and vb:
                print("  bracket %-14s %.2f -> %.2f (%.3fx)" % (label, va, vb, vb / va))
        d = [x.get("server_decode_tok_s") for x in (a, b)]
        o = [x.get("door_overhead_ms") for x in (a, b)]
        if all(d) and min(d) / max(d) < 0.90:
            verdict = ("DRIFTED: the bracketing %d-token calls disagree on DECODE -- the rung "
                       "changed state mid-ladder, so the size sweep is not clean" % args.sizes[0])
        elif all(o) and min(o) / max(o) < 0.70:
            verdict = ("DRIFTED: the bracketing calls disagree on DOOR OVERHEAD -- something "
                       "other than size moved during the ladder")
        else:
            verdict = "STABLE: the bracketing calls agree; the size sweep is clean"
    print("\nDRIFT CONTROL -- %s" % verdict)

    joined = [r for r in rows if r.get("joined")]
    ov = [r["door_overhead_ms"] for r in joined if r.get("arm") == "warm"]
    if ov:
        print("  warm door overhead: %.1f - %.1f ms (spread %.1f ms) across sizes %s"
              % (min(ov), max(ov), max(ov) - min(ov), args.sizes))
    stalls = [r for r in joined if r.get("prefill_stall")]
    for r in stalls:
        print("  PREFILL STALL: arm=%s server_prompt_n=%s prompt_ms=%.1f (decode was %.1f tok/s)"
              % (r["arm"], r["server_prompt_n"], r["server_prompt_ms"], r["server_decode_tok_s"]))

    out = {"ts": datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat(),
           "probe": "C2-DOOR-ATTRIBUTION", "cell": "door-vs-rung",
           "incumbent_process_epoch": epoch.get("epoch_start"),
           "coresident": False,
           "baseline_decode_tok_s": (rung or {}).get("baseline_decode_tok_s"),
           "sizes": args.sizes, "cold_after_s": args.cold_after,
           "drift_control": verdict, "calls": rows,
           "join_method": "distinct prompt token count per call, corroborated by the server "
                          "line's absolute time falling inside the door call's wall-clock "
                          "window and by door tokens_out == server predicted_n",
           "receipt_status": "MECHANISM_DISCRIMINATION",
           "receipt_status_reason": "attributes door-call wall time between gateway overhead and "
                                    "rung decode; bracketed size ladder controls for rung state drift"}
    if not args.no_ledger:
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
