"""ETW2 step 2: PROVE the handle join on the healthy trace. Derive nothing until it holds.

Candidate semantics, from etw2_fields.py cardinality (NOT from field names):
  QueuePacket.SubmitSequence        unique per event  -> identifies one queue submission
  DmaPacket.ulQueueSubmitSequence   260 distinct      -> carries the QueuePacket's SubmitSequence
  DmaPacket/Start.uliSubmissionId   unique per event  - same numbering, same sample values
  DmaPacket/Stop.uliCompletionId    unique per event  - so both identify one DMA packet

REFUTED on the healthy trace: 0 of 864 matched. This box uses HARDWARE GPU SCHEDULING,
so the server's work never touches those SYSTEM-owned scheduler events. See analyse_hws.

Controls carried, per Derek:
  * CONSERVATION/CARDINALITY - every relationship reports matched / unmatched / ambiguous /
    multiply-matched. Nothing is silently dropped because a join failed.
  * TEMPORAL PLAUSIBILITY - joined sequences must be monotonic and inside the request arm.
    A completion-before-submission match is evidence the SEMANTICS ARE WRONG, not an edge case.

Usage: etw2_join.py <report.json>
"""
import collections, io, json, re, sys, xml.etree.ElementTree as ET
from datetime import datetime

ISO = re.compile(r"^(.*?)\.(\d+)([+-]\d\d:\d\d|Z)?$")


def epoch(ts):
    """ETW SystemTime has 9 fractional digits; fromisoformat accepts <= 6.

    REGRESSION FIXTURE (bug 1): the arm epochs recorded by PowerShell were wrong by
    exactly 28800 s because [datetime]'1970-01-01Z' parses as LOCAL. That produced an
    EMPTY join rather than an error. Always derive epochs from the ISO strings.
    """
    if not ts:
        return None
    try:
        m = ISO.match(ts)
        if m:
            ts = "%s.%s%s" % (m.group(1), m.group(2)[:6], m.group(3) or "")
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


def strip(t):
    return t.split("}")[-1]


def events(path):
    """Yield DxgKrnl events.

    REGRESSION FIXTURE (bug 2): DxgKrnl events carry RenderingInfo/<Task>, NOT <EventName>.
    Reading EventName gives '?' for every event and makes 'no packets found' look like a
    real result. Both bugs produced convincing empty output instead of crashing.
    """
    for _, el in ET.iterparse(path, events=("end",)):
        if strip(el.tag) != "Event":
            continue
        prov = pid = ts = opcode = rtask = eid = None
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
                    elif st == "EventID":
                        eid = (s.text or "").strip()
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
            yield (rtask, opcode, eid, pid, epoch(ts), data)


def analyse(dump, srv, arm):
    lo, hi = arm["start_epoch"], arm["end_epoch"]
    qp_start, qp_stop = {}, {}
    dma_start, dma_stop = {}, {}
    dup = collections.Counter()
    eid_mix = collections.defaultdict(collections.Counter)

    for rtask, opcode, eid, pid, ep, d in events(dump):
        if rtask == "QueuePacket" and opcode == "Start":
            k = d.get("SubmitSequence")
            if k is not None:
                if k in qp_start:
                    dup["qp_start"] += 1
                qp_start[k] = {"ts": ep, "pid": pid, "ctx": d.get("hContext"),
                               "ptype": d.get("PacketType"), "eid": eid}
        elif rtask == "QueuePacket" and opcode == "Stop":
            k = d.get("SubmitSequence")
            if k is not None:
                if k in qp_stop:
                    dup["qp_stop"] += 1
                qp_stop[k] = {"ts": ep, "ptype": d.get("PacketType")}
        elif rtask == "DmaPacket" and opcode == "Start":
            k = d.get("uliSubmissionId")
            eid_mix["DmaPacket/Start"][eid] += 1
            if k is not None:
                if k in dma_start:
                    dup["dma_start"] += 1
                dma_start[k] = {"ts": ep, "qseq": d.get("ulQueueSubmitSequence"),
                                "ctx": d.get("hContext"), "ptype": d.get("PacketType")}
        elif rtask == "DmaPacket" and opcode == "Stop":
            k = d.get("uliCompletionId")
            eid_mix["DmaPacket/Stop"][eid] += 1
            if k is not None:
                if k in dma_stop:
                    dup["dma_stop"] += 1
                dma_stop[k] = {"ts": ep, "qseq": d.get("ulQueueSubmitSequence"),
                               "preempted": d.get("bPreempted")}
        elif rtask == "DmaPacket" and opcode == "Info":
            eid_mix["DmaPacket/Info"][eid] += 1

    print("  duplicate keys (should be 0): %s" % (dict(dup) or "none"))
    print("  DmaPacket/Info EventID mix (one task name, several event ids): %s"
          % dict(eid_mix["DmaPacket/Info"].most_common(6)))

    # --- server-attributed QueuePackets, stratified by PacketType ---
    srv_qp = {k: v for k, v in qp_start.items() if v["pid"] == srv}
    in_arm = {k: v for k, v in srv_qp.items() if v["ts"] and lo <= v["ts"] <= hi}
    ptypes = collections.Counter(v["ptype"] for v in in_arm.values())
    print("\n  server QueuePacket/Start in arm: %d   PacketType mix: %s"
          % (len(in_arm), dict(ptypes)))

    # --- JOIN: QueuePacket.SubmitSequence -> DmaPacket.ulQueueSubmitSequence ---
    dma_by_qseq = collections.defaultdict(list)
    for sid, v in dma_start.items():
        if v["qseq"]:
            dma_by_qseq[v["qseq"]].append(sid)

    matched, unmatched, multi = [], [], []
    for seq, v in in_arm.items():
        hits = dma_by_qseq.get(seq, [])
        if len(hits) == 0:
            unmatched.append((seq, v))
        elif len(hits) == 1:
            matched.append((seq, v, hits[0]))
        else:
            multi.append((seq, v, hits))

    print("\n  === CONSERVATION: QueuePacket -> DmaPacket/Start ===")
    print("    matched            %d" % len(matched))
    print("    unmatched          %d   (no DmaPacket carries this SubmitSequence)" % len(unmatched))
    print("    multiply-matched   %d" % len(multi))
    if unmatched:
        um = collections.Counter(v["ptype"] for _, v in unmatched)
        print("    unmatched by PacketType: %s" % dict(um))
    if matched:
        mm = collections.Counter(v["ptype"] for _, v, _ in matched)
        print("    matched   by PacketType: %s" % dict(mm))

    # --- chain to completion, and check temporal plausibility ---
    print("\n  === CONSERVATION: DmaPacket/Start -> DmaPacket/Stop ===")
    chain, no_stop, bad_order = [], 0, []
    for seq, v, sid in matched:
        st = dma_start[sid]
        sp = dma_stop.get(sid)
        if not sp:
            no_stop += 1
            continue
        if None in (v["ts"], st["ts"], sp["ts"]):
            continue
        if not (v["ts"] <= st["ts"] <= sp["ts"]):
            bad_order.append((seq, v["ts"], st["ts"], sp["ts"]))
            continue
        chain.append({"qp": v["ts"], "dstart": st["ts"], "dstop": sp["ts"], "ptype": v["ptype"]})
    print("    complete chains    %d" % len(chain))
    print("    start without stop %d" % no_stop)
    print("    NON-MONOTONIC      %d   <-- any nonzero means the SEMANTICS ARE WRONG" % len(bad_order))
    for b in bad_order[:3]:
        print("      seq=%s qp=%.6f dstart=%.6f dstop=%.6f" % b)
    return chain, len(in_arm), len(matched), len(unmatched), len(multi), len(bad_order)


def analyse_hws(dump, srv, arm):
    """The HARDWARE-SCHEDULING path, which is what this box actually uses.

    Refuted first: QueuePacket.SubmitSequence -> DmaPacket.ulQueueSubmitSequence matched
    0 of 864. The server's work never reaches the SYSTEM-owned DmaPacket/Start-Stop
    scheduler events, because those are the legacy path.

    Cardinality says the real pair is:
      eid450 DmaPacket/Info  ProgressFenceValue + hHwQueue, emitted on the APP pid  -> submit
      eid451 DmaPacket/Info  ProgressFenceValue + hHwQueue, emitted on pid 0 (DPC)  -> complete
    640 distinct fence values on each side, same hHwQueue set. Proven, not assumed.
    """
    lo, hi = arm["start_epoch"], arm["end_epoch"]
    sub, comp = {}, {}
    dup = collections.Counter()
    for rtask, opcode, eid, pid, ep, d in events(dump):
        if rtask != "DmaPacket" or opcode != "Info":
            continue
        fv, hq = d.get("ProgressFenceValue"), d.get("hHwQueue")
        if fv is None or hq is None:
            continue
        key = (hq, fv)
        if eid == "450":
            if key in sub:
                dup["submit"] += 1
            sub[key] = {"ts": ep, "pid": pid}
        elif eid == "451":
            if key in comp:
                dup["complete"] += 1
            comp[key] = {"ts": ep, "pid": pid}

    srv_sub = {k: v for k, v in sub.items() if v["pid"] == srv and v["ts"] and lo <= v["ts"] <= hi}
    matched, unmatched, bad = [], 0, []
    for k, v in srv_sub.items():
        c = comp.get(k)
        if not c or not c["ts"]:
            unmatched += 1
            continue
        if c["ts"] < v["ts"]:
            bad.append((k, v["ts"], c["ts"]))
            continue
        matched.append({"sub": v["ts"], "comp": c["ts"], "hq": k[0]})

    print("\n  === HWS PATH: eid450 submit -> eid451 complete, keyed (hHwQueue, ProgressFenceValue) ===")
    print("    duplicate keys      %s" % (dict(dup) or "none"))
    print("    server submits in arm %d" % len(srv_sub))
    print("    matched             %d" % len(matched))
    print("    unmatched           %d" % unmatched)
    print("    NON-MONOTONIC       %d   <-- nonzero means the semantics are wrong" % len(bad))
    for b in bad[:3]:
        print("      %s sub=%.6f comp=%.6f" % b)
    return matched, len(srv_sub), unmatched, len(bad)


def main():
    rep = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
    srv = str(rep.get("server_pid"))
    arms = {a["label"]: a for a in rep["arms"]}
    for a in arms.values():
        se, ee = epoch(a["start_utc"]), epoch(a["end_utc"])
        if se and ee:
            a["start_epoch"], a["end_epoch"] = se, ee

    results, hws = {}, {}
    for tr in rep["traces"]:
        dump = tr.get("dump")
        if not dump:
            continue
        label = "etw-" + dump.split("-r")[-1].split(".")[0]
        print("\n" + "=" * 90)
        print("ARM %s   %s" % (label, dump.split("\\")[-1]))
        print("=" * 90)
        results[label] = analyse(dump, srv, arms[label])
        hws[label] = analyse_hws(dump, srv, arms[label])

    print("\n" + "=" * 90)
    print("REPRODUCIBILITY ACROSS THE TWO IDENTICAL HEALTHY ARMS")
    print("=" * 90)
    print("  %-8s %8s %8s %10s %6s %8s" % ("arm", "qp_arm", "matched", "unmatched", "multi", "nonmono"))
    for lab, (chain, n_arm, m, u, mu, bo) in results.items():
        print("  %-8s %8d %8d %10d %6d %8d" % (lab, n_arm, m, u, mu, bo))

    if all(r[5] == 0 for r in results.values()) and all(r[2] > 0 for r in results.values()):
        print("\n  >>> JOIN PROVEN: monotonic, no ambiguity. Deriving the decomposition.")
        for lab, (chain, *_ ) in results.items():
            if not chain:
                continue
            chain.sort(key=lambda c: c["qp"])
            wait = sorted((c["dstart"] - c["qp"]) * 1000 for c in chain)
            exe = sorted((c["dstop"] - c["dstart"]) * 1000 for c in chain)
            gap = sorted((chain[i]["qp"] - chain[i - 1]["dstop"]) * 1000 for i in range(1, len(chain)))

            def pr(name, v):
                if not v:
                    return
                print("    %-22s n=%4d  p10=%8.4f  p50=%8.4f  p90=%8.4f  sum=%9.3f ms"
                      % (name, len(v), v[int(len(v) * .1)], v[len(v) // 2], v[int(len(v) * .9)], sum(v)))
            print("\n  --- %s ---" % lab)
            pr("queue/WDDM wait", wait)
            pr("execution", exe)
            pr("submission gap", gap)
    else:
        print("\n  >>> LEGACY JOIN NOT PROVEN. No decomposition derived from it, by design.")

    print("\n" + "=" * 90)
    print("HWS PATH REPRODUCIBILITY")
    print("=" * 90)
    print("  %-8s %10s %8s %10s %8s" % ("arm", "submits", "matched", "unmatched", "nonmono"))
    for lab, (m, n, u, b) in hws.items():
        print("  %-8s %10d %8d %10d %8d" % (lab, n, len(m), u, b))

    ok = hws and all(b == 0 and u == 0 and len(m) > 0 for m, n, u, b in hws.values())
    if not ok:
        print("\n  >>> HWS JOIN NOT PROVEN. Nothing derived.")
        return
    print("\n  >>> HWS JOIN PROVEN: complete, monotonic, no ambiguity. Deriving.")
    for lab, (m, n, u, b) in hws.items():
        m.sort(key=lambda c: c["sub"])
        exe = sorted((c["comp"] - c["sub"]) * 1000 for c in m)
        gap = sorted((m[i]["sub"] - m[i - 1]["comp"]) * 1000 for i in range(1, len(m)))

        def pr(name, v):
            if not v:
                return
            print("    %-26s n=%4d p10=%8.4f p50=%8.4f p90=%8.4f sum=%9.3f ms"
                  % (name, len(v), v[int(len(v) * .1)], v[len(v) // 2], v[int(len(v) * .9)], sum(v)))
        print("\n  --- %s ---" % lab)
        pr("submit -> complete", exe)
        pr("complete -> next submit", gap)
        neg = sum(1 for g in gap if g < 0)
        print("    overlapping (negative gap): %d of %d  -- concurrent hw queues, expected" % (neg, len(gap)))


if __name__ == "__main__":
    main()
