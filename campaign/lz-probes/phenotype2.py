"""Clean pass: exclude contaminated windows, quote defensible numbers."""
import json, statistics, runpy, sys, io, contextlib

# Re-use the measurement machinery by importing the module's globals.
src = r"C:\Users\derek\AppData\Local\Temp\claude\C--work-commandcenter--claude-worktrees-quizzical-taussig-c91322\417af4f7-b34b-497c-b45b-9e75a19af720\scratchpad\phenotype.py"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    G = runpy.run_path(src)

measure, inwin = G["measure"], G["inwin"]
deeps = [k for k in inwin if k["dec"] is not None]
pings = [k for k in inwin if k["dec"] is None]

print("=== CHANNEL B: the 32-token deep probe, per-token budget ===")
print("  %-9s %8s %11s %11s %11s %9s %9s" % (
    "time", "decode", "busy/tok", "wall/tok", "gap/tok", "busy_frac", "J/tok"))
grp = {"degraded": [], "healthy": []}
for k in deeps:
    m = measure(k)
    if not m["ok"]:
        continue
    b = m["busy_ns"] / 1e6 / 32.0
    w = k["pred_ms"] / 32.0
    row = (k, b, w, m["energy_j"] / 32.0)
    grp["degraded" if k["dec"] < 90 else "healthy"].append(row)
    print("  %-9s %8.2f %11.3f %11.3f %11.3f %8.1f%% %9.3f" % (
        k["dt"].strftime("%H:%M:%S"), k["dec"], b, w, w - b, 100.0 * b / w, row[3]))

for lbl in ["healthy", "degraded"]:
    rows = grp[lbl]
    if not rows:
        continue
    b = [r[1] for r in rows]; w = [r[2] for r in rows]; j = [r[3] for r in rows]
    print("\n  %s (n=%d): busy/tok %.3f ms | wall/tok %.3f ms | GAP %.3f ms | busy_frac %.1f%% | J/tok %.3f" % (
        lbl.upper(), len(rows), statistics.median(b), statistics.median(w),
        statistics.median(w) - statistics.median(b),
        100.0 * statistics.median(b) / statistics.median(w), statistics.median(j)))

hb, hw = statistics.median([r[1] for r in grp["healthy"]]), statistics.median([r[2] for r in grp["healthy"]])
db, dw = statistics.median([r[1] for r in grp["degraded"]]), statistics.median([r[2] for r in grp["degraded"]])
print("\n  >>> busy/token   %.3f -> %.3f ms  = %+.1f%%" % (hb, db, 100.0 * (db - hb) / hb))
print("  >>> wall/token   %.3f -> %.3f ms  = %+.1f%%" % (hw, dw, 100.0 * (dw - hw) / hw))
print("  >>> host GAP/tok %.3f -> %.3f ms  = %+.0f%%   <-- where the time goes" % (
    hw - hb, dw - db, 100.0 * ((dw - db) - (hw - hb)) / (hw - hb)))

print("\n=== CHANNEL A: the 1-token ping, one forward pass ===")
clean, dropped = [], []
for k in pings:
    m = measure(k, pad=2.0)
    if not m["ok"]:
        continue
    b = m["busy_ns"] / 1e6
    (dropped if b > 50 else clean).append((k, b))
print("  dropped %d contaminated windows (busy>50 ms: overlapped a deep probe or a ratecheck arm):" % len(dropped))
for k, b in dropped:
    print("      %s  pm=%5.2f  busy=%9.2f ms" % (k["dt"].strftime("%H:%M:%S"), k["pm"], b))

cold = [(k, b) for k, b in clean if k["pm"] <= 12.24]
hot = [(k, b) for k, b in clean if k["pm"] > 12.24]
for lbl, g in [("prompt_ms NORMAL", cold), ("prompt_ms HOT", hot)]:
    pm = [k["pm"] for k, _ in g]; bm = [b for _, b in g]
    print("\n  %s  n=%d" % (lbl, len(g)))
    print("    prompt_ms median %6.2f   busy_ms median %6.3f   host overhead %6.3f ms" % (
        statistics.median(pm), statistics.median(bm), statistics.median(pm) - statistics.median(bm)))

cpm, cbm = statistics.median([k["pm"] for k, _ in cold]), statistics.median([b for _, b in cold])
hpm, hbm = statistics.median([k["pm"] for k, _ in hot]), statistics.median([b for _, b in hot])
print("\n  >>> prompt_ms  %.2f -> %.2f ms  (+%.2f ms)" % (cpm, hpm, hpm - cpm))
print("  >>> GPU busy   %.3f -> %.3f ms  (+%.3f ms)" % (cbm, hbm, hbm - cbm))
print("  >>> of the +%.2f ms of extra prefill latency, %.2f ms (%.0f%%) is GPU busy;"
      " %.2f ms (%.0f%%) is HOST-SIDE" % (
          hpm - cpm, hbm - cbm, 100.0 * (hbm - cbm) / (hpm - cpm),
          (hpm - cpm) - (hbm - cbm), 100.0 * ((hpm - cpm) - (hbm - cbm)) / (hpm - cpm)))

xs = [k["pm"] for k, _ in clean]; ys = [b for _, b in clean]
mx, my = statistics.mean(xs), statistics.mean(ys)
num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
den = sum((x - mx) ** 2 for x in xs)
print("  >>> regression busy_ms on prompt_ms: slope = %.3f ms GPU per ms reported (n=%d)" % (
    num / den if den else float("nan"), len(xs)))

print("\n=== CROSS-CHECK: is one ping really one forward pass? ===")
print("  ping busy (1 token, n=%d)          = %.3f ms" % (len(cold), cbm))
print("  deep-probe busy/token (32 tokens)  = %.3f ms" % hb)
print("  agreement: %.2f%%  -- two independent request shapes, same per-pass GPU cost" % (
    100.0 * abs(cbm - hb) / hb))
