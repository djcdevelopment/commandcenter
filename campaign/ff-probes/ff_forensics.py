#!/usr/bin/env python3
"""FF-FORENSICS (P-A) -- reconstruct the Aug-28 incumbent topology timeline.

ADR-0042 established that production had been running all 49 layers on ONE B70.
ADR-0041 established that co-resident work poisons the incumbent until restart.
Neither invalidates the venue-matrix conclusions, but both invalidate the
PROVENANCE of the measurements behind them. This tool repairs the record.

WHAT SURVIVES, AND WHAT DOES NOT (verified 2026-08-29)
-------------------------------------------------------
Three of the four evidence sources P-A would ideally use are gone:

  * per-epoch `using device` / `model buffer size` -- DOES NOT EXIST.
    serve-arc.cmd redirects with `>` (truncate), so only the live epoch's log
    survives; and at the default `verbosity = 3` llama-server emits ZERO
    placement lines. They appear only at `-lv 5`. Same for every LZ probe log.
  * epoch boundaries from the OS -- UNAVAILABLE.
    Microsoft-Windows-TaskScheduler/Operational is enabled=False, 0 records.
  * an incumbent rate timeline -- TOO SPARSE.
    hearth/var/ledger/index.sqlite holds 4 omen-arc calls on Aug 28, and carries
    duration_ms but not tokens_out, so it is not a rate.

What DOES survive is per-PCI-BDF residency from b70tools adapter probes under
`qwen38-bench-2026-08/results/telemetry/adapter-probe-*`.

THE ACTIVITY-WINDOW CAVEAT IS LOAD-BEARING HERE
------------------------------------------------
`gpu.adapter.vram.local.bytes_committed` decays to ~0 on an idle server and
recovers on use (measured three times on one unchanging server, 2026-08-29). So
a probe reading 0.00/0.00 is INDETERMINATE, not "empty cards". Most probes are
indeterminate for exactly this reason -- which is why this tool reports
UNKNOWN far more often than INVALID.

WE DO NOT LABEL ABSENCE OF EVIDENCE AS EVIDENCE OF INVALIDITY. Three findings
were retracted on 2026-08-29 for precisely that error.

Read-only. Appends classification rows; never rewrites an original observation.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
from datetime import datetime, timedelta, timezone

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
PROBE_GLOB = (r"E:\work\battlemage\qwen38-bench-2026-08\results\telemetry"
              r"\adapter-probe-*")
RECEIPT_SOURCES = [
    r"E:\work\battlemage\lz-probes\lz-receipts.jsonl",
    r"E:\work\battlemage\rotation-phase1\r2-receipts.jsonl",
    r"E:\work\battlemage\rotation-phase1\w1-receipts.jsonl",
    r"E:\work\battlemage\rotation-phase1\w2-receipts.jsonl",
    r"E:\work\battlemage\rotation-phase1\w3-receipts.jsonl",
]

# A single card holding the whole model reads ~29.7 GB; a dual split reads ~15/15.
SINGLE_HI_GB, SINGLE_LO_GB = 20.0, 2.0
DUAL_LO_GB, DUAL_HI_GB = 8.0, 25.0

# How close a receipt must sit to an anchor to inherit its topology. The filter's
# outcome is nondeterministic PER PROCESS LAUNCH, so an anchor only speaks for its
# own epoch -- and we cannot see epoch boundaries. 15 min is deliberately tight.
BIND_WINDOW_MIN = 15

# The placement fix landed 2026-08-29; before it, no rate gate existed anywhere,
# so incumbent health is unestablished for every co-resident receipt prior.
HEALTH_GATE_EPOCH = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _parse_ts(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[:32] if "%z" in fmt else s[:26] if "." in fmt else s[:19], fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_anchors():
    """Classify every adapter probe by per-BDF residency."""
    out = []
    for d in sorted(glob.glob(PROBE_GLOB)):
        p = os.path.join(d, "events.jsonl")
        if not os.path.exists(p):
            continue
        ident, last = {}, {}
        for line in io.open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("k") == "ai":
                ident[e.get("a")] = e
            elif e.get("k") == "ms":
                last[(e.get("a"), e.get("n"))] = e.get("v")
        cards = {}
        for a, i in ident.items():
            if "Arc" not in (i.get("desc") or ""):
                continue
            v = last.get((a, "gpu.adapter.vram.local.bytes_committed"))
            cards[i.get("bdf")] = (v / 1024 ** 3) if isinstance(v, (int, float)) else None
        vals = [v for v in cards.values() if v is not None]
        if len(vals) < 2:
            verdict, why = "indeterminate", "fewer than two Arc adapters reported"
        elif max(vals) >= SINGLE_HI_GB and min(vals) <= SINGLE_LO_GB:
            verdict, why = "confirmed-single", "one card >=%.0f GB, other <=%.0f GB" % (SINGLE_HI_GB, SINGLE_LO_GB)
        elif all(DUAL_LO_GB <= v <= DUAL_HI_GB for v in vals):
            verdict, why = "confirmed-dual", "both cards in the %.0f-%.0f GB split band" % (DUAL_LO_GB, DUAL_HI_GB)
        else:
            verdict, why = "indeterminate", "activity-window artifact: counter reads ~0 on an idle server"
        stamp = os.path.basename(d).replace("adapter-probe-", "")
        try:
            ts = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            ts = None
        out.append({"probe": os.path.basename(d), "ts": ts, "cards": cards,
                    "verdict": verdict, "why": why})
    return [a for a in out if a["ts"]]


def read_receipts():
    rows = []
    for src in RECEIPT_SOURCES:
        if not os.path.exists(src):
            continue
        for n, line in enumerate(io.open(src, encoding="utf-8", errors="replace")):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(d.get("ts"))
            rows.append({"source": os.path.basename(src), "line": n + 1, "ts": ts,
                         "probe": d.get("probe"), "cell": d.get("cell"),
                         "coresident": d.get("coresident"),
                         "has_time_only": len(str(d.get("ts") or "")) <= 10})
    return rows


def classify(receipt, anchors):
    """Bind a receipt to the nearest preceding anchor, if one is close enough."""
    statuses, reasons = [], []
    ts = receipt["ts"]

    if ts is None:
        statuses.append("PLACEMENT_CONTEXT_UNKNOWN")
        reasons.append("receipt carries no parseable timestamp")
    elif receipt["has_time_only"]:
        statuses.append("PLACEMENT_CONTEXT_UNKNOWN")
        reasons.append("receipt ts is date-only; cannot bind to an anchor")
    else:
        usable = [a for a in anchors if a["verdict"] != "indeterminate" and a["ts"] <= ts]
        near = [a for a in usable if ts - a["ts"] <= timedelta(minutes=BIND_WINDOW_MIN)]
        if near:
            a = max(near, key=lambda x: x["ts"])
            gap = (ts - a["ts"]).total_seconds()
            if a["verdict"] == "confirmed-single":
                statuses.append("PLACEMENT_CONTEXT_INVALID")
                reasons.append("anchor %s (%.0fs earlier) shows SINGLE-card: %s"
                               % (a["probe"], gap, a["why"]))
            else:
                reasons.append("anchor %s (%.0fs earlier) shows dual-card as assumed" % (a["probe"], gap))
        else:
            statuses.append("PLACEMENT_CONTEXT_UNKNOWN")
            nearest = max(usable, key=lambda x: x["ts"]) if usable else None
            if nearest:
                hrs = (ts - nearest["ts"]).total_seconds() / 3600.0
                reasons.append("nearest usable anchor %s is %.1f h earlier; the filter's outcome is "
                               "nondeterministic per process launch, so it cannot be extrapolated"
                               % (nearest["probe"], hrs))
            else:
                reasons.append("no usable topology anchor precedes this receipt")

    if ts is None or ts < HEALTH_GATE_EPOCH:
        if receipt.get("coresident"):
            statuses.append("INCUMBENT_HEALTH_UNKNOWN")
            reasons.append("co-resident receipt predating any rate gate; healthy-vs-poisoned "
                           "incumbent state was never measured (ADR-0041)")
    return statuses, reasons


def main() -> int:
    ap = argparse.ArgumentParser(description="P-A: reconstruct the Aug-28 topology timeline")
    ap.add_argument("--emit", action="store_true", help="append classification rows to the ledger")
    args = ap.parse_args()

    anchors = read_anchors()
    receipts = read_receipts()

    print("=== TOPOLOGY ANCHORS (b70tools adapter probes, per PCI BDF) ===")
    for a in anchors:
        cards = "  ".join("%s=%s" % (b, ("%.2f GB" % v) if v is not None else "null")
                          for b, v in sorted(a["cards"].items()))
        flag = "  <<<" if a["verdict"] != "indeterminate" else ""
        print("  %s  %-16s  %s%s" % (a["ts"].strftime("%Y-%m-%d %H:%M:%S"), a["verdict"], cards, flag))
    usable = [a for a in anchors if a["verdict"] != "indeterminate"]
    print("  -> %d probes, %d usable, %d indeterminate (activity-window artifact)"
          % (len(anchors), len(usable), len(anchors) - len(usable)))

    print("\n=== RECEIPT CLASSIFICATION ===")
    tally, rows = {}, []
    for r in receipts:
        st, why = classify(r, anchors)
        key = "+".join(st) if st else "OK (anchored dual / post-gate)"
        tally[key] = tally.get(key, 0) + 1
        rows.append((r, st, why))
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-58s %d" % (k, v))

    print("\n=== the one binding that produced a verdict ===")
    for r, st, why in rows:
        if "PLACEMENT_CONTEXT_INVALID" in st:
            print("  %s %-22s %-14s %s" % (r["ts"].strftime("%H:%M:%S"), r["source"], r["probe"], why[0]))

    if args.emit:
        ts_now = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "ts": ts_now, "probe": "FF-FORENSICS", "result": "classification",
                "detail": ("P-A retroactive provenance classification. Anchors from b70tools "
                           "adapter probes; per-epoch placement evidence does not survive "
                           "(truncating log + default verbosity emits none), OS task history is "
                           "disabled, and the ledger has 4 omen-arc calls on Aug 28. Absence of "
                           "evidence is recorded as UNKNOWN, never as INVALID."),
                "anchors": [{"probe": a["probe"], "ts": a["ts"].isoformat(),
                             "verdict": a["verdict"], "cards_gb": a["cards"]} for a in anchors],
                "tally": tally, "bind_window_min": BIND_WINDOW_MIN,
                "receipts_classified": len(rows),
            }, ensure_ascii=False) + "\n")
            for r, st, why in rows:
                if not st:
                    continue
                f.write(json.dumps({
                    "ts": ts_now, "probe": "FF-FORENSICS", "result": "receipt_status",
                    "target": {"source": r["source"], "line": r["line"],
                               "probe": r["probe"], "cell": r["cell"],
                               "ts": r["ts"].isoformat() if r["ts"] else None},
                    "receipt_status": st, "receipt_status_reason": why,
                }, ensure_ascii=False) + "\n")
        print("\n  -> classification rows appended to %s" % LEDGER)
        print("     (originals untouched; this is an added annotation layer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
