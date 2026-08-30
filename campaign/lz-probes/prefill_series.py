"""LZ-STATEWATCH lap: read the 30 s keep-alive ping's prompt_ms as a rate instrument.

Zero added load. Nothing is fired at the server; this only reads the ledger the
keep-alive already writes.
"""
import json, statistics, sys
from datetime import datetime

LEDGER = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"

rows = []
with open(LEDGER, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("probe") != "ARC-KEEPALIVE":
            continue
        ts = r.get("ts")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        rows.append({
            "dt": dt,
            "epoch": dt.timestamp(),
            "ok": r.get("ok"),
            "prompt_ms": r.get("prompt_ms"),
            "predicted_n": r.get("predicted_n"),
            "decode": r.get("decode_tok_s"),
            "degraded_flag": r.get("decode_degraded"),
        })

rows.sort(key=lambda x: x["epoch"])
print("total keepalive rows: %d" % len(rows))
if not rows:
    sys.exit(0)
print("span: %s -> %s" % (rows[0]["dt"].strftime("%m-%d %H:%M:%S"),
                          rows[-1]["dt"].strftime("%m-%d %H:%M:%S")))

# Restrict to tonight's epoch: from the start of the run that produced the incident.
# Use everything from 2026-08-29 22:00 local onward.
cut = datetime.fromisoformat("2026-08-29T22:00:00-07:00").timestamp()
night = [r for r in rows if r["epoch"] >= cut and r["ok"] and r["prompt_ms"] is not None]
print("tonight rows (ok, prompt_ms present): %d" % len(night))

pings = [r for r in night if (r["predicted_n"] or 0) <= 1]
deeps = [r for r in night if r["decode"] is not None]
print("  1-token pings: %d   deep probes: %d" % (len(pings), len(deeps)))

pm = [r["prompt_ms"] for r in pings]
pm_sorted = sorted(pm)


def pct(v, p):
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


print("\n--- prompt_ms distribution over the 1-token pings ---")
print("  n=%d  min=%.2f  p05=%.2f  p50=%.2f  p95=%.2f  p99=%.2f  max=%.2f" % (
    len(pm_sorted), pm_sorted[0], pct(pm_sorted, 5), pct(pm_sorted, 50),
    pct(pm_sorted, 95), pct(pm_sorted, 99), pm_sorted[-1]))
med = pct(pm_sorted, 50)
print("  median=%.2f  mean=%.3f  stdev=%.3f" % (med, statistics.mean(pm), statistics.pstdev(pm)))

# Histogram, 0.5 ms bins up to 25, then overflow.
print("\n--- histogram (0.5 ms bins) ---")
bins = {}
for v in pm:
    b = min(int(v * 2), 50)
    bins[b] = bins.get(b, 0) + 1
for b in sorted(bins):
    lo = b / 2.0
    label = ">=25.0" if b == 50 else "%4.1f-%4.1f" % (lo, lo + 0.5)
    print("  %s  %5d  %s" % (label, bins[b], "#" * min(80, bins[b])))

# Excursion threshold: anything above median * 1.20
thresh = med * 1.20
hot = [r for r in pings if r["prompt_ms"] > thresh]
print("\n--- excursions above median*1.20 = %.2f ms ---" % thresh)
print("  n=%d of %d pings (%.1f%%)" % (len(hot), len(pings), 100.0 * len(hot) / len(pings)))

# Group excursions into episodes: consecutive hot pings within 90 s of each other.
episodes = []
cur = None
for r in hot:
    if cur is None or r["epoch"] - cur[-1]["epoch"] > 90:
        cur = [r]
        episodes.append(cur)
    else:
        cur.append(r)

print("\n--- prefill episodes (hot pings grouped, gap>90s splits) ---")
print("  %-19s %-19s %6s %5s %8s %8s" % ("start", "end", "dwell_s", "n", "peak_ms", "med_ms"))
for ep in episodes:
    dwell = ep[-1]["epoch"] - ep[0]["epoch"]
    vals = [r["prompt_ms"] for r in ep]
    print("  %-19s %-19s %6.0f %5d %8.1f %8.1f" % (
        ep[0]["dt"].strftime("%m-%d %H:%M:%S"),
        ep[-1]["dt"].strftime("%H:%M:%S"),
        dwell, len(ep), max(vals), statistics.median(vals)))

print("\n--- deep probes with decode (the 5-min grid, for comparison) ---")
dgr = [r for r in deeps if r["decode"] is not None and r["decode"] < 90]
print("  deep probes: %d   of which decode<90: %d" % (len(deeps), len(dgr)))
for r in dgr:
    print("    %s  decode=%7.2f  prefill=%5.2f  flag=%s" % (
        r["dt"].strftime("%m-%d %H:%M:%S"), r["decode"], r["prompt_ms"], r["degraded_flag"]))

# Coverage question: how many prefill episodes contain a degraded deep probe?
print("\n--- do the two instruments agree? ---")
deep_by_epoch = [(r["epoch"], r) for r in deeps]
caught = 0
for ep in episodes:
    lo, hi = ep[0]["epoch"] - 30, ep[-1]["epoch"] + 30
    inside = [r for e, r in deep_by_epoch if lo <= e <= hi]
    d = [r for r in inside if r["decode"] is not None and r["decode"] < 90]
    if d:
        caught += 1
    print("  %s dwell=%4.0fs  deep probes inside: %d  degraded among them: %d %s" % (
        ep[0]["dt"].strftime("%H:%M:%S"), ep[-1]["epoch"] - ep[0]["epoch"],
        len(inside), len(d), "" if d or inside else "<-- INVISIBLE to the 5-min grid"))
print("  episodes containing a degraded deep probe: %d of %d" % (caught, len(episodes)))
