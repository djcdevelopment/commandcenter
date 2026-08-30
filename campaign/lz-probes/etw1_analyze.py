"""Analyze an ETW1 DxgKrnl dump against the recorded arm windows.

Answers Q1 (are QueuePacket/DmaPacket emitted), Q2 (cadence + stability), and the A1
join (are packets attributable to the probe window and the server PID).

Usage: etw1_analyze.py <report.json>
"""
import collections, io, json, sys, xml.etree.ElementTree as ET
from datetime import datetime

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
TR = "{http://schemas.microsoft.com/win/2004/08/events/trace}"


def strip(tag):
    return tag.split("}")[-1]


def _epoch(ts):
    """ETW SystemTime carries 9 fractional digits; fromisoformat wants <=6."""
    if not ts:
        return None
    try:
        m = ISO.match(ts)
        if m:
            ts = "%s.%s%s" % (m.group(1), m.group(2)[:6], m.group(3))
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


ISO = __import__("re").compile(r"^(.*?)\.(\d+)([+-]\d\d:\d\d|Z)?$")


def parse(path, provider_only="Microsoft-Windows-DxgKrnl"):
    """Stream the dump, yielding (name, opcode, pid, epoch_seconds).

    DxgKrnl events carry no <EventName>; their human name is RenderingInfo/Task
    (e.g. 'ExternalEvent'). Fall back to the numeric System/Task when unresolved.
    """
    for _, el in ET.iterparse(path, events=("end",)):
        if strip(el.tag) != "Event":
            continue
        name = opcode = rtask = None
        pid = ts = prov = None
        tasknum = None
        for child in el:
            t = strip(child.tag)
            if t == "System":
                for s in child:
                    st = strip(s.tag)
                    if st == "TimeCreated":
                        ts = s.get("SystemTime")
                    elif st == "Execution":
                        pid = s.get("ProcessID")
                    elif st == "Provider":
                        prov = s.get("Name") or s.get("Guid")
                    elif st == "Task":
                        tasknum = (s.text or "").strip()
            elif t == "RenderingInfo":
                for s in child:
                    st = strip(s.tag)
                    if st == "EventName":
                        name = (s.text or "").strip()
                    elif st == "Task":
                        rtask = (s.text or "").strip()
                    elif st == "Opcode":
                        opcode = (s.text or "").strip()
        el.clear()
        if provider_only and prov != provider_only:
            continue
        label = name or rtask or ("task#" + (tasknum or "?"))
        yield (label, opcode or "?", pid, _epoch(ts))


def main():
    rep = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
    srv = str(rep.get("server_pid"))
    arms = {a["label"]: a for a in rep["arms"]}
    print("server_pid = %s" % srv)
    # The recorded *_epoch fields are wrong by exactly 28800 s: PowerShell's
    # [datetime]'1970-01-01Z' parses as LOCAL, not UTC. The ISO start_utc/end_utc
    # strings are unambiguous, so re-derive from those and ignore the precomputed field.
    for a in arms.values():
        se, ee = _epoch(a["start_utc"]), _epoch(a["end_utc"])
        if se and ee:
            a["start_epoch"], a["end_epoch"] = se, ee

    for tr in rep["traces"]:
        dump = tr.get("dump")
        if not dump:
            continue
        label = "etw-" + dump.split("-r")[-1].split(".")[0]
        arm = arms.get(label)
        print("\n" + "=" * 78)
        print("TRACE %s   arm=%s" % (dump.split("\\")[-1], label))
        if arm:
            print("  arm window: %.3f .. %.3f  (%.3f s)  decode=%s  predicted_n=%s" % (
                arm["start_epoch"], arm["end_epoch"],
                arm["end_epoch"] - arm["start_epoch"], arm["decode_tps"], arm["predicted_n"]))

        by_name = collections.Counter()
        by_name_pid = collections.Counter()
        pids = collections.Counter()
        times = collections.defaultdict(list)
        total = 0
        for name, opcode, pid, ep in parse(dump):
            total += 1
            key = "%s/%s" % (name, opcode)
            by_name[key] += 1
            pids[pid] += 1
            if pid == srv:
                by_name_pid[key] += 1
                if ep:
                    times[key].append(ep)

        print("  total events in dump: %d" % total)
        print("\n  --- top event types, ALL processes ---")
        for k, c in by_name.most_common(15):
            print("    %-52s %7d" % (k, c))

        print("\n  --- top PIDs ---")
        for p, c in pids.most_common(6):
            mark = "  <-- llama-server" if p == srv else ""
            print("    pid %-8s %7d%s" % (p, c, mark))

        print("\n  --- event types attributable to the SERVER pid (A1) ---")
        if not by_name_pid:
            print("    NONE. No DxgKrnl events carry the server PID in this trace.")
        for k, c in by_name_pid.most_common(15):
            print("    %-52s %7d" % (k, c))

        # Q1/Q2: packet events specifically
        print("\n  --- Q1: packet-like event types (any process) ---")
        pk = {k: c for k, c in by_name.items() if "packet" in k.lower()}
        if not pk:
            print("    NO event type containing 'Packet' appears at all.")
        for k, c in sorted(pk.items(), key=lambda x: -x[1]):
            print("    %-52s %7d" % (k, c))

        # Q2 cadence, for server-attributed packet events inside the arm window
        if arm:
            lo, hi = arm["start_epoch"], arm["end_epoch"]
            n_pred = arm.get("predicted_n") or 0
            print("\n  --- Q2: cadence inside the arm window, server pid ---")
            any_row = False
            for k, ts in sorted(times.items(), key=lambda x: -len(x[1])):
                inside = sorted(t for t in ts if lo <= t <= hi)
                if len(inside) < 2:
                    continue
                any_row = True
                gaps = [(inside[i] - inside[i - 1]) * 1000.0 for i in range(1, len(inside))]
                gaps_s = sorted(gaps)
                med = gaps_s[len(gaps_s) // 2]
                ratio = (len(inside) / n_pred) if n_pred else 0
                print("    %-46s n=%5d  per_token=%6.2f  gap_ms p50=%7.3f p10=%7.3f p90=%7.3f" % (
                    k[:46], len(inside), ratio, med,
                    gaps_s[int(len(gaps_s) * 0.1)], gaps_s[int(len(gaps_s) * 0.9)]))
            if not any_row:
                print("    no server-attributed event type has >=2 events inside the arm window")


if __name__ == "__main__":
    main()
