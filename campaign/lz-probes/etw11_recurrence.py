"""How often did INC-A actually fire overnight? Grounds the observation-window question."""
import io, json, statistics
from datetime import datetime

KA = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
CONTAM = (datetime.fromisoformat("2026-08-30T04:29:00-07:00").timestamp(),
          datetime.fromisoformat("2026-08-30T04:32:00-07:00").timestamp())
EXPOSURE_START = datetime.fromisoformat("2026-08-30T06:01:23-07:00").timestamp()

deep = []
for line in io.open(KA, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("probe") != "ARC-KEEPALIVE" or r.get("decode_tok_s") is None:
        continue
    ep = datetime.fromisoformat(r["ts"]).timestamp()
    if CONTAM[0] <= ep <= CONTAM[1]:
        continue
    deep.append((ep, r["decode_tok_s"]))
deep.sort()

print("total deep probes on the ledger: %d" % len(deep))
print("span: %s -> %s" % (datetime.fromtimestamp(deep[0][0]).strftime("%m-%d %H:%M"),
                          datetime.fromtimestamp(deep[-1][0]).strftime("%m-%d %H:%M")))

# Episodes = runs of consecutive sub-90 probes (the watcher's own trigger rule).
eps, cur = [], None
for ep, d in deep:
    if d < 90:
        if cur is None:
            cur = [ep, ep]
        else:
            cur[1] = ep
    else:
        if cur:
            eps.append(cur)
            cur = None
if cur:
    eps.append(cur)

print("\n=== episodes crossing the <90 trigger ===")
for a, b in eps:
    print("  %s -> %s   dwell<=%.0f min" % (datetime.fromtimestamp(a).strftime("%H:%M:%S"),
                                            datetime.fromtimestamp(b).strftime("%H:%M:%S"),
                                            (b - a) / 60 + 5))
print("  count = %d" % len(eps))

if len(eps) >= 2:
    gaps = [(eps[i][0] - eps[i - 1][1]) / 60.0 for i in range(1, len(eps))]
    print("\n  inter-episode gaps (min): %s" % ", ".join("%.0f" % g for g in gaps))
    print("  median gap %.0f min   mean %.0f min   max %.0f min"
          % (statistics.median(gaps), statistics.mean(gaps), max(gaps)))

pre = [x for x in deep if x[0] < EXPOSURE_START]
post = [x for x in deep if x[0] >= EXPOSURE_START]
pre_eps = [e for e in eps if e[0] < EXPOSURE_START]
if pre:
    hours = (pre[-1][0] - pre[0][0]) / 3600.0
    print("\n=== BEFORE the current exposure window ===")
    print("  %.2f h, %d deep probes, %d episodes = %.2f episodes/hour"
          % (hours, len(pre), len(pre_eps), len(pre_eps) / hours if hours else 0))
    if hours and len(pre_eps):
        rate = len(pre_eps) / hours
        print("  at that rate, expected wait for the next episode ~ %.0f min" % (60.0 / rate))
        # Poisson: P(zero episodes in t hours) = exp(-rate*t)
        for t in (2.5, 4, 6, 8, 12):
            import math
            print("     P(zero episodes in %4.1f h) = %.3f" % (t, math.exp(-rate * t)))

if post:
    hours = (post[-1][0] - EXPOSURE_START) / 3600.0
    post_eps = [e for e in eps if e[0] >= EXPOSURE_START]
    print("\n=== CURRENT exposure window ===")
    print("  %.2f h, %d deep probes, %d episodes crossing the trigger" % (hours, len(post), len(post_eps)))
    d = [x[1] for x in post]
    print("  decode min %.2f  median %.2f  max %.2f" % (min(d), statistics.median(d), max(d)))
