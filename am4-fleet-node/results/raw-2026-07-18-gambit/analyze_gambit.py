#!/usr/bin/env python3
"""Aggregate the gambit JSONL into per-cell summary tables."""
import json
import statistics as st
from collections import defaultdict

RAW = r"C:\work\commandcenter\am4-fleet-node\results\raw-2026-07-18-gambit"


def load(name):
    with open(f"{RAW}\\{name}") as fh:
        return [json.loads(x) for x in fh if x.strip()]


reqs = load("requests.jsonl")
waves = load("waves.jsonl")
samples = load("samples.jsonl")

wave_wall = {w["tag"]: w["wave_wall_s"] for w in waves}


def cell(prefix):
    return [r for r in reqs if r["tag"].startswith(prefix)]


def agg(rows):
    ok = [r for r in rows if r["ok"] and r.get("ttft_s") is not None]
    if not ok:
        return None
    ttfts = [r["ttft_s"] for r in ok]
    decs = [r["decode_tps"] for r in ok if r.get("decode_tps")]
    return {
        "n": len(rows), "ok": len(ok),
        "ttft_mean": round(st.mean(ttfts), 2), "ttft_p95": round(sorted(ttfts)[int(0.95 * (len(ttfts) - 1))], 2),
        "ttft_max": round(max(ttfts), 2),
        "decode_mean": round(st.mean(decs), 2) if decs else None,
        "wall_max": round(max(r["total_s"] for r in ok), 2),
        "chunks": sum(r["chunks"] for r in ok),
    }


print("== A: concurrency ladder (p~800, out 128) ==")
for c in (1, 2, 3, 4, 6, 8, 12, 16):
    rows = cell(f"A.c{c}.")
    a = agg(rows)
    walls = [wave_wall.get(f"A.c{c}.r{r}") for r in (0, 1)]
    walls = [w for w in walls if w]
    goodput = round(a["chunks"] / sum(walls), 1) if walls else None
    print(f"c={c:>2}  {a}  wave_walls={walls}  goodput_tok_s={goodput}")

print("\n== B: context depth (c=4, out 128) ==")
for p in (512, 2048, 4096, 8192, 12288, 15000):
    a = agg(cell(f"B.p{p}"))
    print(f"p={p:>5}  {a}")
print("overlimit:", json.dumps(cell("B.overlimit")[0], default=str)[:400])
for r in cell("B.cache"):
    print(f"{r['tag']}: ttft={r['ttft_s']} total={r['total_s']} ok={r['ok']}")

print("\n== C: decode-heavy ==")
for tag in ("C.out256", "C.out1024", "C.c8.out512"):
    print(tag, agg(cell(tag)), "wall", wave_wall.get(tag))

print("\n== F: burst 32 ==")
f = cell("F.burst32")
a = agg(f)
print(a, "wall", wave_wall.get("F.burst32"))
ttfts = sorted(r["ttft_s"] for r in f if r.get("ttft_s"))
print("ttft quartiles:", [round(ttfts[int(q * (len(ttfts) - 1))], 1) for q in (0, .25, .5, .75, 1)])
print("errors:", [r["error"] for r in f if not r["ok"]] or "none")

print("\n== E: soak drift ==")
e = cell("E.soak")
tags = sorted({r["tag"] for r in e}, key=lambda t: int(t.split(".")[-1]))
first3 = [r for t in tags[:3] for r in e if r["tag"] == t]
last3 = [r for t in tags[-3:] for r in e if r["tag"] == t]
print(f"waves={len(tags)} first3 decode={round(st.mean([r['decode_tps'] for r in first3 if r.get('decode_tps')]), 2)} "
      f"last3 decode={round(st.mean([r['decode_tps'] for r in last3 if r.get('decode_tps')]), 2)}")
print(f"first3 ttft={round(st.mean([r['ttft_s'] for r in first3]), 2)} last3 ttft={round(st.mean([r['ttft_s'] for r in last3]), 2)}")

print("\n== D: pressure phases (2-way load) ==")
d = cell("D.load")
d_by_i = defaultdict(list)
for r in d:
    d_by_i[int(r["tag"].split(".")[-1])].append(r)
for lo, hi, label in ((0, 4, "baseline"), (5, 13, "hog6"), (20, 30, "hog10"),
                      (40, 50, "hog14"), (60, 70, "hog18")):
    rows = [r for i, rs in d_by_i.items() if lo <= i <= hi for r in rs]
    if rows:
        print(label, agg(rows))

print("\n== temps: max during campaign ==")
maxes = defaultdict(int)
for s in samples:
    for k, v in (s.get("temps_c") or {}).items():
        if k.startswith(("xe.", "k10temp.")):
            maxes[k] = max(maxes[k], v)
xe = max((v for k, v in maxes.items() if k.startswith("xe.")), default=None)
cpu = max((v for k, v in maxes.items() if k.startswith("k10temp.")), default=None)
print(f"max GPU die temp: {xe}C  max CPU: {cpu}C")
swaps = [s["mem_mib"].get("SwapFree", 0) for s in samples if s.get("mem_mib")]
print(f"min SwapFree during campaign: {min(swaps)} MiB (of 24575)")
print(f"total requests: {len(reqs)}  failures: {sum(1 for r in reqs if not r['ok'])}")
