"""Phenotype-first: do the prefill-only and prefill+decode states share a hardware signature?

Instrument: gpu.activity.global_counter (IGCL, DirectlyObserved, NANOSECONDS of GPU busy
time) plus gpu.energy_j_counter, from the passive 1 Hz b70tools capture. Read-only.

The busy counter has a ~0.008%% idle floor on the B70s, so integrating a sub-second event
over a multi-second window loses almost nothing -- unlike energy, whose 26.5 W/card floor
swamps a 300 ms probe.
"""
import json, statistics
from datetime import datetime

CAP = r"E:\work\battlemage\ff-probes\statewatch-20260830\events.jsonl"
KA = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"

# Fixed anchor (taken 2026-08-30 03:38:47 local, while the capture was live).
T0_NS, T0_EPOCH = 155147035906200, 1788086327.324
B70 = ["adapter_00016f14", "adapter_00017310"]
IGPU = "adapter_00016b87"


def wall(t_ns):
    return (t_ns - T0_NS) / 1e9 + T0_EPOCH


series = {m: {a: [] for a in B70 + [IGPU]} for m in
          ["gpu.activity.global_counter", "gpu.activity.render_compute_counter",
           "gpu.energy_j_counter"]}
with open(CAP, "r", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        nm, a, t = r.get("n"), r.get("a"), r.get("t")
        if nm in series and a in series[nm] and t:
            series[nm][a].append((wall(t), r["v"]))
for m in series:
    for a in series[m]:
        series[m][a].sort()

ka = []
with open(KA, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("probe") != "ARC-KEEPALIVE" or not r.get("ok") or r.get("prompt_ms") is None:
            continue
        dt = datetime.fromisoformat(r["ts"])
        ka.append({"ep": dt.timestamp(), "dt": dt, "pm": r["prompt_ms"],
                   "dec": r.get("decode_tok_s"), "n": r.get("predicted_n") or 0,
                   "pred_ms": r.get("predicted_ms") or 0.0})
ka.sort(key=lambda x: x["ep"])

lo = min(v[0][0] for a in B70 for v in [series["gpu.activity.global_counter"][a]])
hi = max(v[-1][0] for a in B70 for v in [series["gpu.activity.global_counter"][a]])
inwin = [k for k in ka if lo + 5 < k["ep"] < hi - 5]
print("capture covers %s .. %s local" % (
    datetime.fromtimestamp(lo).strftime("%H:%M:%S"), datetime.fromtimestamp(hi).strftime("%H:%M:%S")))
print("keepalive events inside capture: %d (deep=%d, ping=%d)" % (
    len(inwin), sum(1 for k in inwin if k["dec"] is not None),
    sum(1 for k in inwin if k["dec"] is None)))


def integrate(a, metric, t_lo, t_hi):
    """Value increase of a cumulative counter across [t_lo, t_hi]."""
    v = series[metric][a]
    before = [x for x in v if x[0] <= t_lo]
    after = [x for x in v if x[0] >= t_hi]
    if not before or not after:
        return None, None
    return after[0][1] - before[-1][1], after[0][0] - before[-1][0]


def quiet_rate(a, metric, centre, exclude_lo, exclude_hi, half=45.0):
    """Local baseline rate from ticks near `centre` but outside the event window."""
    v = series[metric][a]
    pts = [x for x in v if centre - half <= x[0] <= centre + half
           and not (exclude_lo - 1.5 <= x[0] <= exclude_hi + 1.5)]
    rates = []
    for i in range(1, len(pts)):
        dt = pts[i][0] - pts[i - 1][0]
        dv = pts[i][1] - pts[i - 1][1]
        if 0.5 < dt < 2.0 and dv >= 0:
            rates.append(dv / dt)
    return statistics.median(rates) if rates else None


def measure(k, pad=2.5):
    """Busy-ns and energy-J attributable to one keepalive request, both B70s summed."""
    t_hi = k["ep"] + pad
    t_lo = k["ep"] - (k.get("wall_s", 0.5)) - pad
    out = {"busy_ns": 0.0, "energy_j": 0.0, "ok": True, "per_card": []}
    for a in B70:
        dv, dt = integrate(a, "gpu.activity.global_counter", t_lo, t_hi)
        de, _ = integrate(a, "gpu.energy_j_counter", t_lo, t_hi)
        br = quiet_rate(a, "gpu.activity.global_counter", k["ep"], t_lo, t_hi)
        er = quiet_rate(a, "gpu.energy_j_counter", k["ep"], t_lo, t_hi)
        if dv is None or de is None or br is None or er is None:
            out["ok"] = False
            continue
        busy = dv - br * dt
        ener = de - er * dt
        out["busy_ns"] += busy
        out["energy_j"] += ener
        out["per_card"].append((busy, ener, br, er))
    return out


for k in inwin:
    k["wall_s"] = (k["pred_ms"] + k["pm"]) / 1000.0

# --- anchor validation: do busy spikes land where the keepalive says they do? ---
print("\n=== ANCHOR VALIDATION: busy-ns in aligned vs offset windows ===")
for off in [0, 3, 7, 11, 15]:
    vals = []
    for k in inwin:
        kk = dict(k); kk["ep"] = k["ep"] + off
        m = measure(kk, pad=1.5)
        if m["ok"]:
            vals.append(m["busy_ns"])
    if vals:
        print("  offset %+3ds : n=%3d  median busy_ns = %12.0f  mean = %12.0f" % (
            off, len(vals), statistics.median(vals), statistics.mean(vals)))

deeps = [k for k in inwin if k["dec"] is not None]
pings = [k for k in inwin if k["dec"] is None]

print("\n=== DEEP PROBES (32 tokens) inside the capture ===")
print("  %-10s %8s %7s %10s %12s %10s %9s %9s" % (
    "time", "decode", "pm_ms", "pred_ms", "busy_ns", "busy_ms", "J", "J/tok"))
rows = []
for k in deeps:
    m = measure(k)
    if not m["ok"]:
        print("  %-10s  <window incomplete>" % k["dt"].strftime("%H:%M:%S"))
        continue
    bms = m["busy_ns"] / 1e6
    rows.append((k, m, bms))
    print("  %-10s %8.2f %7.2f %10.1f %12.0f %10.2f %9.2f %9.3f" % (
        k["dt"].strftime("%H:%M:%S"), k["dec"], k["pm"], k["pred_ms"],
        m["busy_ns"], bms, m["energy_j"], m["energy_j"] / 32.0))

print("\n=== the same deep probes, grouped by PHENOTYPE ===")


def phen(k):
    return "B: prefill+decode" if k["dec"] < 90 else "healthy/A"


for label, sel in [("prefill+decode degraded (decode<90)", lambda k: k["dec"] < 90),
                   ("decode healthy (>=100)", lambda k: k["dec"] >= 100)]:
    sub = [(k, m, b) for (k, m, b) in rows if sel(k)]
    if not sub:
        continue
    print("\n  --- %s  (n=%d) ---" % (label, len(sub)))
    for k, m, b in sub:
        print("      %s decode=%7.2f  busy=%8.2f ms  busy/token=%7.3f ms  J/token=%6.3f  wall/token=%6.3f ms" % (
            k["dt"].strftime("%H:%M:%S"), k["dec"], b, b / 32.0,
            m["energy_j"] / 32.0, k["pred_ms"] / 32.0))
    bpt = [b / 32.0 for _, _, b in sub]
    jpt = [m["energy_j"] / 32.0 for _, m, _ in sub]
    wpt = [k["pred_ms"] / 32.0 for k, _, _ in sub]
    print("      median busy/token = %.3f ms | J/token = %.3f | wall/token = %.3f ms | busy fraction = %.1f%%" % (
        statistics.median(bpt), statistics.median(jpt), statistics.median(wpt),
        100.0 * statistics.median(bpt) / statistics.median(wpt)))

print("\n=== PINGS (1 token = one forward pass): is a hot prompt_ms real GPU work? ===")
print("  %-10s %7s %12s %10s %10s" % ("time", "pm_ms", "busy_ns", "busy_ms", "busy/pm"))
hot, cold = [], []
for k in sorted(pings, key=lambda x: x["ep"]):
    m = measure(k, pad=2.0)
    if not m["ok"]:
        continue
    bms = m["busy_ns"] / 1e6
    (hot if k["pm"] > 12.24 else cold).append((k, bms))
    print("  %-10s %7.2f %12.0f %10.3f %10.2f" % (
        k["dt"].strftime("%H:%M:%S"), k["pm"], m["busy_ns"], bms, bms / k["pm"] if k["pm"] else 0))

for lbl, grp in [("prompt_ms NORMAL (<=12.24)", cold), ("prompt_ms HOT (>12.24)", hot)]:
    if not grp:
        continue
    pm = [k["pm"] for k, _ in grp]
    bm = [b for _, b in grp]
    print("\n  %s  n=%d" % (lbl, len(grp)))
    print("    prompt_ms  median %.2f  (min %.2f max %.2f)" % (statistics.median(pm), min(pm), max(pm)))
    print("    busy_ms    median %.3f  (min %.3f max %.3f)" % (statistics.median(bm), min(bm), max(bm)))
