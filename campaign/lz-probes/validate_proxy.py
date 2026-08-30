"""Validate prompt_ms as a proxy for decode rate, using the 64 paired deep-probe samples.

Each deep probe measures prompt_ms (prefill, prompt_n=1) and decode_tok_s (32 tokens)
in the SAME request -- a perfectly paired sample, already recorded, zero added load.
"""
import json, statistics
from datetime import datetime

LEDGER = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
cut = datetime.fromisoformat("2026-08-29T22:00:00-07:00").timestamp()

deeps, pings = [], []
with open(LEDGER, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("probe") != "ARC-KEEPALIVE" or not r.get("ok"):
            continue
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        if dt.timestamp() < cut or r.get("prompt_ms") is None:
            continue
        rec = {"dt": dt, "epoch": dt.timestamp(), "pm": r["prompt_ms"],
               "dec": r.get("decode_tok_s"), "n": r.get("predicted_n") or 0}
        (deeps if rec["dec"] is not None else pings).append(rec)

deeps.sort(key=lambda x: x["epoch"])
pings.sort(key=lambda x: x["epoch"])


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    rk = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            rk[order[k]] = avg
        i = j + 1
    return rk


def pearson(a, b):
    n = len(a)
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((x - mb) ** 2 for x in b) ** 0.5
    return num / (da * db) if da and db else float("nan")


pm = [d["pm"] for d in deeps]
dec = [d["dec"] for d in deeps]
print("=== PAIRED DEEP PROBES: prompt_ms vs decode_tok_s, same request ===")
print("n = %d" % len(deeps))
print("  decode  : min=%.2f p50=%.2f max=%.2f" % (min(dec), statistics.median(dec), max(dec)))
print("  prompt_ms: min=%.2f p50=%.2f max=%.2f" % (min(pm), statistics.median(pm), max(pm)))
print("  Pearson  r = %+.4f" % pearson(pm, dec))
print("  Spearman r = %+.4f" % pearson(rank(pm), rank(dec)))

print("\n--- 2x2 contingency (prefill hot >12.24 ms | decode degraded <90 tok/s) ---")
TH_PM, TH_DEC = 12.24, 90.0
a = sum(1 for d in deeps if d["pm"] > TH_PM and d["dec"] < TH_DEC)
b = sum(1 for d in deeps if d["pm"] > TH_PM and d["dec"] >= TH_DEC)
c = sum(1 for d in deeps if d["pm"] <= TH_PM and d["dec"] < TH_DEC)
e = sum(1 for d in deeps if d["pm"] <= TH_PM and d["dec"] >= TH_DEC)
print("               decode<90   decode>=90")
print("  prefill hot     %3d          %3d      <- precision %.2f" % (a, b, a / (a + b) if a + b else 0))
print("  prefill ok      %3d          %3d" % (c, e))
print("                recall %.2f" % (a / (a + c) if a + c else 0))
print("  false-positive rate among healthy-decode probes: %d/%d = %.3f" % (b, b + e, b / (b + e) if b + e else 0))

print("\n--- every degraded deep probe, and its prefill ---")
for d in deeps:
    if d["dec"] < TH_DEC:
        print("  %s  decode=%7.2f  prefill=%6.2f  %s" % (
            d["dt"].strftime("%H:%M:%S"), d["dec"], d["pm"],
            "PREFILL HOT" if d["pm"] > TH_PM else "prefill NORMAL <-- proxy MISSES this one"))

print("\n--- the hot-prefill deep probes that had HEALTHY decode (false alarms) ---")
for d in deeps:
    if d["pm"] > TH_PM and d["dec"] >= TH_DEC:
        print("  %s  decode=%7.2f  prefill=%6.2f" % (d["dt"].strftime("%H:%M:%S"), d["dec"], d["pm"]))

# Restart / cold-start confound check: any enormous prompt_ms in the ping series?
print("\n--- outlier pings (prompt_ms > 100 ms): restart / cold-start candidates ---")
for p in pings:
    if p["pm"] > 100:
        print("  %s  prompt_ms=%.1f" % (p["dt"].strftime("%m-%d %H:%M:%S"), p["pm"]))

# Does a hot ping predict the NEXT deep probe being degraded?
print("\n--- forward test: hot ping -> is the next deep probe degraded? ---")
hot_then_deg = hot_then_ok = cold_then_deg = cold_then_ok = 0
for p in pings:
    nxt = next((d for d in deeps if d["epoch"] > p["epoch"]), None)
    if nxt is None or nxt["epoch"] - p["epoch"] > 60:
        continue
    hot = p["pm"] > TH_PM
    deg = nxt["dec"] < TH_DEC
    if hot and deg:
        hot_then_deg += 1
    elif hot:
        hot_then_ok += 1
    elif deg:
        cold_then_deg += 1
    else:
        cold_then_ok += 1
print("  ping hot  -> next deep degraded %d / healthy %d" % (hot_then_deg, hot_then_ok))
print("  ping cold -> next deep degraded %d / healthy %d" % (cold_then_deg, cold_then_ok))
