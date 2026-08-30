"""ETW2 step 1: what EventData fields do the packet events actually carry?

Before any join, enumerate fields and their cardinality for QueuePacket and DmaPacket.
Nothing is inferred from field NAMES; cardinality and lifecycle decide.

Usage: etw2_fields.py <dump.xml> [server_pid]
"""
import collections, io, sys, xml.etree.ElementTree as ET

TARGET = {"QueuePacket", "DmaPacket"}


def strip(t):
    return t.split("}")[-1]


def events(path):
    for _, el in ET.iterparse(path, events=("end",)):
        if strip(el.tag) != "Event":
            continue
        prov = pid = ts = opcode = rtask = None
        data = {}
        for child in el:
            t = strip(child.tag)
            if t == "System":
                for s in child:
                    st = strip(s.tag)
                    if st == "Provider":
                        prov = s.get("Name")
                    elif st == "Execution":
                        pid = s.get("ProcessID")
                    elif st == "TimeCreated":
                        ts = s.get("SystemTime")
            elif t == "EventData":
                for d in child:
                    n = d.get("Name")
                    if n:
                        data[n] = (d.text or "").strip()
            elif t == "RenderingInfo":
                for s in child:
                    st = strip(s.tag)
                    if st == "Opcode":
                        opcode = (s.text or "").strip()
                    elif st == "Task":
                        rtask = (s.text or "").strip()
        el.clear()
        if prov == "Microsoft-Windows-DxgKrnl":
            yield (rtask, opcode, pid, ts, data)


def main():
    path = sys.argv[1]
    srv = sys.argv[2] if len(sys.argv) > 2 else None

    fields = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    counts = collections.Counter()
    pidmix = collections.defaultdict(collections.Counter)

    for rtask, opcode, pid, ts, data in events(path):
        if rtask not in TARGET:
            continue
        key = "%s/%s" % (rtask, opcode)
        counts[key] += 1
        pidmix[key][pid] += 1
        for k, v in data.items():
            fields[key][k][v] += 1

    print("=" * 96)
    print("PACKET EVENT TYPES, their EventData fields, and cardinality")
    print("server_pid = %s" % srv)
    print("=" * 96)
    for key in sorted(counts, key=lambda k: -counts[k]):
        n = counts[key]
        srv_n = pidmix[key].get(srv, 0) if srv else 0
        print("\n%-24s  n=%-6d  server_pid=%-6d  other_pids=%s" % (
            key, n, srv_n, dict(list(pidmix[key].most_common(4)))))
        for fname, vals in sorted(fields[key].items(), key=lambda x: -len(x[1])):
            distinct = len(vals)
            top = vals.most_common(2)
            sample = ", ".join("%s(x%d)" % (v[:26], c) for v, c in top)
            # A join key should be MANY-valued but repeat across event types.
            flag = ""
            if distinct == 1:
                flag = "  [constant]"
            elif distinct == n:
                flag = "  [unique per event]"
            elif 1 < distinct <= 64:
                flag = "  <-- CANDIDATE (low cardinality, repeats)"
            print("    %-32s distinct=%-6d %s%s" % (fname, distinct, sample, flag))


if __name__ == "__main__":
    main()
