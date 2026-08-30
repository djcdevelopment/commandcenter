#!/usr/bin/env python3
"""FF-CELL -- run ONE experiment cell under the ADR-0041/0042 invariant.

This is the P-B per-cell harness. It exists because the invariant has an ORDERING
that is easy to get wrong by hand, and getting it wrong produces a plausible number
rather than an error:

  1. (optional) restart the incumbent, and wait for the REAL ready marker
  2. ff_ratecheck  -> this both DRIVES TRAFFIC and yields the pre-rate
  3. immediately sample b70tools -> placement by PCI BDF, *inside the activity
     window step 2 just created*
  4. gate: pre-rate must be >= FAIL_FRAC of baseline, or refuse to run the cell
  5. run exactly one experimental cell
  6. ff_ratecheck again -> post-rate
  7. emit one row carrying all nine provenance fields

Step 3 MUST follow step 2. `local_committed` is an activity-window counter that reads
~0.00 on an idle server even while the model is demonstrably resident and serving
(finding A12, measured three times on one unchanging server). Sampling before driving
traffic reads zeros and proves nothing -- which is why 22 of the 25 archived
adapter-probe captures are `indeterminate` rather than informative. Doing this in one
process is the only reliable way to land the sample inside the window.

A sub-gate POST-rate is a POISONING EVENT, not a co-residency datum. It is recorded as
such and the co-resident interpretation of the cell is invalidated; the fix is a
restart and a re-baseline, not a correction factor (ADR-0041).

Serviceability is treated as LIVENESS ONLY and never as performance-health evidence:
the door proof returned ok:true at 105 tok/s and at 22 tok/s alike.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SERVE_LOG = r"C:\work\commandcenter\hearth\var\arc-serve.log"
READY_MARKER = "model loaded"

# llama-server stamps each line with elapsed MM.SS.mmm.uuu since process start.
ELAPSED_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\s")


def incumbent_epoch():
    """Identify the incumbent's process epoch from its own log.

    Authoritative, and it has to be derived rather than read: llama-server runs under
    an S4U scheduled task, so Get-Process StartTime is inaccessible and its CPU
    counters read 0 while it works. The log's elapsed stamp plus the file mtime pins
    the start instead.
    """
    if not os.path.exists(SERVE_LOG):
        return {"ok": False, "reason": "no server log at %s" % SERVE_LOG}
    last = None
    ready = False
    for line in io.open(SERVE_LOG, encoding="utf-8", errors="replace"):
        m = ELAPSED_RE.match(line)
        if m:
            last = m
        if READY_MARKER in line:
            ready = True
    if last is None:
        return {"ok": False, "reason": "no elapsed-stamped lines in the server log"}
    elapsed_s = int(last.group(1)) * 60 + int(last.group(2)) + int(last.group(3)) / 1000.0
    mtime = datetime.fromtimestamp(os.path.getmtime(SERVE_LOG)).astimezone()
    start = (mtime - timedelta(seconds=elapsed_s)).replace(microsecond=0)
    return {"ok": True, "epoch_start": start.isoformat(),
            "log_elapsed_s": round(elapsed_s, 1),
            "ready_marker_seen": ready,
            "method": "arc-serve.log elapsed stamp subtracted from file mtime; the log is "
                      "truncated on every launch, so it describes exactly one epoch"}


def wait_for_ready(timeout_s=420, since=None):
    """Gate on the REAL ready marker, never on /health.

    /health returns 200 while the model is still loading -- measured: a 503 and a 43 s
    call with no timings at all. Port-open is not model-ready. This rule was written
    down and then broken the same day, which is why it is enforced in code here.

    `since` closes a STALE-MARKER race that the marker check alone does not. serve-arc.cmd
    redirects with `>`, so the log is truncated at launch -- but restart-arc.cmd kills the
    old server, waits 3 s, and only then starts the new one. For those seconds the file
    still holds the PREVIOUS epoch's "model loaded" line, so a caller that restarts and
    immediately waits gets True from a log describing a server that no longer exists.
    Passing the instant the restart was issued makes the check require a log written
    AFTER it -- i.e. this epoch's marker, not the last one's.
    """
    deadline = datetime.now() + timedelta(seconds=timeout_s)
    while datetime.now() < deadline:
        try:
            if since is not None:
                mtime = datetime.fromtimestamp(os.path.getmtime(SERVE_LOG))
                if mtime < since:
                    raise ValueError("log not rewritten yet")
            if READY_MARKER in io.open(SERVE_LOG, encoding="utf-8", errors="replace").read():
                return True
        except (OSError, ValueError):
            pass
        subprocess.run(["powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 3"],
                       capture_output=True)
    return False


def rate(rung, reps):
    m = ff_ratecheck.measure(rung, reps)
    if not m.get("ok"):
        return m, None
    base = rung.get("baseline_decode_tok_s")
    frac = (m["decode_tok_s"] / base) if base else None
    return m, frac


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one FF cell under the ADR-0041/0042 invariant")
    ap.add_argument("--cell", required=True, help="cell label, e.g. LZ1-A")
    ap.add_argument("--expect", default="both-b70",
                    choices=["both-b70", "one-b70", "igpu-plus-b70", "any"],
                    help="placement this cell requires of the INCUMBENT")
    ap.add_argument("--command", default="",
                    help="the experimental cell to run (shell). Omitted => gate-only dry run.")
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--restart", action="store_true",
                    help="restart the incumbent first and wait for the real ready marker")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get(args.rung)
    if rung is None:
        print("no rung %r in %s" % (args.rung, ff_ratecheck.BASELINES))
        return 2

    print("=== FF-CELL: %s ===" % args.cell)
    epoch = incumbent_epoch()
    print("  incumbent epoch: %s" % (epoch.get("epoch_start") or epoch.get("reason")))

    if args.restart:
        print("  restarting incumbent (ArcServeRestart) ...")
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        if not wait_for_ready(since=t0):
            print("  FAIL -- ready marker %r never appeared. Refusing to measure." % READY_MARKER)
            return 1
        epoch = incumbent_epoch()
        print("  new epoch: %s" % epoch.get("epoch_start"))

    # --- step 2: pre-rate. This ALSO opens the activity window step 3 needs.
    pre, pre_frac = rate(rung, args.reps)
    if not pre.get("ok"):
        print("  FAIL -- pre-rate: %s" % pre.get("error"))
        return 1
    print("  pre-rate  %.2f tok/s  reps=%s  spread %.2f%%"
          % (pre["decode_tok_s"], pre["decode_reps"], pre.get("repeat_spread_pct") or 0.0))

    # --- step 3: placement, sampled INSIDE the window the pre-rate just created
    census = ff_census.adapters_via_b70tools()
    arc = [a for a in census.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
    committed = [(a.get("bdf"), a.get("local_committed_gb")) for a in arc]
    vals = [v for _, v in committed if isinstance(v, (int, float))]
    if census.get("local_committed_reads_idle"):
        assertion = "indeterminate"
        evidence = ("local_committed read ~0 on both Arc cards despite the pre-rate having "
                    "just driven traffic -- the activity window closed before the sample "
                    "landed. Re-run; do NOT read this as 'weights unloaded' (A12).")
    elif len(vals) == 2 and min(vals) >= 10.0:
        assertion = "both-b70"
        evidence = "per-BDF committed %s; arc temp spread %s C (both_warm=%s)" % (
            committed, census.get("temp_spread_c"), census.get("both_arc_cards_warm"))
    elif len(vals) == 2 and max(vals) >= 25.0 and min(vals) < 1.0:
        assertion = "one-b70"
        evidence = "per-BDF committed %s -- the whole model on one card" % (committed,)
    else:
        assertion = "indeterminate"
        evidence = "per-BDF committed %s does not match a known placement shape" % (committed,)
    print("  placement %s :: %s" % (assertion, evidence))

    # --- step 4: the health gate
    gate_pass = pre_frac is not None and pre_frac >= ff_ratecheck.FAIL_FRAC
    if pre_frac is None:
        status = "NO_BASELINE"
        reason = ("no baseline recorded for rung %r, so the health gate cannot be evaluated. "
                  "Restart production, wait for the real ready marker, then run "
                  "ff_ratecheck.py --set-baseline (ADR-0041: baseline from a FRESH server)."
                  % args.rung)
    elif not gate_pass:
        status = "HEALTH_GATE_FAILED"
        reason = ("pre-rate %.2f tok/s is %.0f%% of baseline %.2f, below the %.0f%% floor. "
                  "This is degradation, not noise -- per ADR-0041 the likely cause is "
                  "co-residency poisoning from earlier work. Restart, re-baseline, retry."
                  % (pre["decode_tok_s"], pre_frac * 100, rung["baseline_decode_tok_s"],
                     ff_ratecheck.FAIL_FRAC * 100))
    elif args.expect != "any" and assertion != args.expect:
        status = "PLACEMENT_MISMATCH"
        gate_pass = False
        reason = ("cell requires placement %r but the incumbent asserts %r. %s"
                  % (args.expect, assertion, evidence))
    else:
        status = "READY"
        reason = "pre-rate %.0f%% of baseline and placement %s as required" % (
            pre_frac * 100, assertion)

    print("  gate: %s -- %s" % (status, reason))

    row = {
        "ts": datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat(),
        "probe": "FF-CELL", "cell": args.cell, "coresident": True,
        # --- the nine provenance fields (ADR-0041/0042) ---
        "incumbent_process_epoch": epoch.get("epoch_start"),
        "incumbent_restarted_since_cotenancy": bool(args.restart),
        "incumbent_rate_fraction_pre": round(pre_frac, 4) if pre_frac is not None else None,
        "incumbent_rate_fraction_post": None,
        "placement_assertion": assertion,
        "placement_evidence": evidence,
        "health_gate_passed": gate_pass,
        "receipt_status": status,
        "receipt_status_reason": reason,
        # --- context ---
        "epoch_detail": epoch,
        "pre_rate": pre,
        "expect": args.expect,
        "command": args.command or None,
        "note": ("incumbent_process_epoch and the measured rate fractions are AUTHORITATIVE; "
                 "incumbent_restarted_since_cotenancy is convenience metadata and must never "
                 "be relied on alone."),
    }

    if not gate_pass:
        row["result"] = "REFUSED - cell not run"
        print("  *** REFUSING to run the cell. A measurement taken now would be unattributable.")
        _append(row, args.no_ledger)
        return 1

    if not args.command:
        row["result"] = "gate-only dry run (no --command given)"
        print("  dry run: gate passed, no cell to execute")
        _append(row, args.no_ledger)
        return 0

    # --- step 5: exactly one experimental cell
    print("  running cell: %s" % args.command)
    proc = subprocess.run(args.command, shell=True, capture_output=True, text=True,
                          errors="replace")
    row["cell_returncode"] = proc.returncode
    row["cell_stdout_tail"] = (proc.stdout or "")[-2000:]
    row["cell_stderr_tail"] = (proc.stderr or "")[-2000:]

    # --- step 6: post-rate
    post, post_frac = rate(rung, args.reps)
    row["post_rate"] = post
    row["incumbent_rate_fraction_post"] = round(post_frac, 4) if post_frac is not None else None
    if post.get("ok"):
        print("  post-rate %.2f tok/s (%.0f%% of baseline)"
              % (post["decode_tok_s"], (post_frac or 0) * 100))

    if post_frac is not None and post_frac < ff_ratecheck.FAIL_FRAC:
        row["result"] = "POISONING EVENT - co-resident interpretation invalidated"
        row["receipt_status"] = "POISONED_DURING_CELL"
        row["receipt_status_reason"] = (
            "post-rate fell to %.0f%% of baseline while the cell ran. Per ADR-0041 this is a "
            "POISONING EVENT, not a co-residency datum: the incumbent is degraded until "
            "restarted, and any co-residency cost read from this cell would be measuring "
            "machine state. Restart, re-baseline, re-run the cell." % (post_frac * 100))
        print("  *** POISONING EVENT -- %s" % row["receipt_status_reason"])
    else:
        row["result"] = "cell completed with the incumbent healthy before and after"

    _append(row, args.no_ledger)
    return 0 if row["receipt_status"] not in ("POISONED_DURING_CELL",) else 1


def _append(row, skip):
    if skip:
        print("  (--no-ledger: row not appended)")
        return
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("  -> appended to %s" % LEDGER)


if __name__ == "__main__":
    sys.exit(main())
