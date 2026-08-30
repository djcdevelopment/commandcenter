"""Did the continuous ETW session depress decode? Read-only, ledger only."""
import io, json, statistics
from datetime import datetime

KA = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
SESSION_START = datetime.fromisoformat("2026-08-30T06:00:00-07:00").timestamp()
# exclude the window I contaminated with a 29k-token request
CONTAM = (datetime.fromisoformat("2026-08-30T04:29:00-07:00").timestamp(),
          datetime.fromisoformat("2026-08-30T04:32:00-07:00").timestamp())

deep, ping = [], []
for line in io.open(KA, encoding="utf-8", errors="replace"):
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
        ep = datetime.fromisoformat(r["ts"]).timestamp()
    except Exception:
        continue
    if CONTAM[0] <= ep <= CONTAM[1]:
        continue
    if r.get("decode_tok_s") is not None:
        deep.append((ep, r["decode_tok_s"], r.get("prompt_ms")))
    elif r.get("prompt_ms") is not None:
        ping.append((ep, r["prompt_ms"]))


def summ(name, vals):
    if not vals:
        print("  %-28s n=0" % name)
        return None
    s = sorted(vals)
    m = statistics.median(s)
    print("  %-28s n=%-4d median=%7.2f  mean=%7.2f  min=%7.2f  max=%7.2f"
          % (name, len(s), m, statistics.mean(s), s[0], s[-1]))
    return m


print("=== DEEP PROBE decode_tok_s, before vs during the continuous ETW session ===")
pre = [d for e, d, _ in deep if e < SESSION_START]
post = [d for e, d, _ in deep if e >= SESSION_START]
# last hour before, to avoid pooling the whole night's regimes
pre1h = [d for e, d, _ in deep if SESSION_START - 3600 <= e < SESSION_START]
a = summ("all before 06:00", pre)
b = summ("the hour before 06:00", pre1h)
c = summ("during ETW (06:00 ->)", post)
if b and c:
    print("\n  >>> hour-before median %.2f -> during-ETW median %.2f = %+.1f%%"
          % (b, c, 100.0 * (c - b) / b))
print("\n  during-ETW deep probes, in order:")
for e, d, p in [x for x in deep if x[0] >= SESSION_START]:
    print("     %s  decode=%7.2f  prompt_ms=%s" % (datetime.fromtimestamp(e).strftime("%H:%M:%S"), d, p))

print("\n=== 1-token PING prompt_ms, same split (the sensitive instrument) ===")
pp = [p for e, p in ping if SESSION_START - 3600 <= e < SESSION_START]
pd = [p for e, p in ping if e >= SESSION_START]
x = summ("the hour before 06:00", pp)
y = summ("during ETW (06:00 ->)", pd)
if x and y:
    print("\n  >>> prompt_ms median %.2f -> %.2f = %+.1f%%" % (x, y, 100.0 * (y - x) / x))
