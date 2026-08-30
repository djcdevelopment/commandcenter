"""ETW4: TIME-WEIGHTED in-flight depth occupancy, per hHwQueue.

The event-weighted histogram from ETW3 shows structure but overweights busy periods.
The degraded comparison is about STARVATION DURATION, so the statistic must be the
fraction of WALL TIME each queue spends at depth 0, 1, 2, ...

Edge handling, explicit: the depth timeline is built from the WHOLE TRACE, not the arm
window, so a fence in flight at window start is reconstructed from its preceding submit
instead of being silently initialised to zero. Two residual classes are counted and
reported rather than hidden:
  * complete with no submit in the trace  -> was in flight at TRACE start; raises the
    reconstructed initial depth by one
  * submit with no complete in the trace  -> still in flight at TRACE end

Local ETW rule earned in ETW3: TAG, DON'T FILTER. Filtering a shared hardware queue by
pid destroys the temporal continuity needed to interpret scheduler state, so occupancy is
computed over ALL submissions on a queue and server share is reported alongside.

All three queues stay separate. The 32-event serial queue is a request/token timing
anchor and must NOT be mixed into the compute-depth statistic.

Usage: etw4_depth.py <report.json>
"""
import collections, io, json, sys

sys.path.insert(0, r"C:\work\commandcenter\campaign\lz-probes")
from etw2_join import events, epoch   # noqa: E402  (carries both regression fixtures)

FED = 3   # descriptive only, pending what the healthy arms establish


def build(dump, srv):
    """Whole-trace submit/complete per queue. No window applied yet."""
    q = collections.defaultdict(lambda: {"sub": {}, "comp": {}})
    for rtask, opcode, eid, pid, ts, d in events(dump):
        if rtask != "DmaPacket" or opcode != "Info":
            continue
        fv, hq = d.get("ProgressFenceValue"), d.get("hHwQueue")
        if fv is None or hq is None or ts is None:
            continue
        if eid == "450":
            q[hq]["sub"][fv] = {"ts": ts, "server": pid == srv}
        elif eid == "451":
            q[hq]["comp"][fv] = {"ts": ts}
    return q


def occupancy(rec, lo, hi):
    sub, comp = rec["sub"], rec["comp"]
    orphan_comp = [fv for fv in comp if fv not in sub]     # in flight at trace start
    orphan_sub = [fv for fv in sub if fv not in comp]      # in flight at trace end

    marks = []
    for fv, s in sub.items():
        marks.append((s["ts"], +1))
    for fv, c in comp.items():
        marks.append((c["ts"], -1))
    marks.sort()

    # Reconstructed initial depth: every completion lacking a submit was already in flight.
    depth = len(orphan_comp)
    # Walk to the window start, carrying depth forward.
    i, n = 0, len(marks)
    while i < n and marks[i][0] < lo:
        depth += marks[i][1]
        i += 1
    init_depth = depth

    # Integrate depth over [lo, hi].
    dur = collections.Counter()          # depth -> seconds
    runs = collections.defaultdict(list)  # depth -> list of interval durations
    t = lo
    cur = depth
    run_start = lo
    while i < n and marks[i][0] <= hi:
        ts, delta = marks[i]
        if ts > t:
            dur[cur] += ts - t
            runs[cur].append(ts - run_start)
            t = ts
            run_start = ts
        cur += delta
        i += 1
    if hi > t:
        dur[cur] += hi - t
        runs[cur].append(hi - run_start)

    total = sum(dur.values())
    return {
        "init_depth": init_depth, "orphan_comp": len(orphan_comp), "orphan_sub": len(orphan_sub),
        "total_s": total, "dur": dur, "runs": runs,
        "server_share": (sum(1 for s in sub.values() if s["server"]) / len(sub)) if sub else 0.0,
        "n_sub": len(sub),
    }


def stats(o):
    total = o["total_s"] or 1e-12
    dur = o["dur"]
    mean = sum(d * s for d, s in dur.items()) / total
    # time-weighted median depth
    acc, med = 0.0, None
    for d in sorted(dur):
        acc += dur[d]
        if acc >= total / 2 and med is None:
            med = d
    f0 = dur.get(0, 0.0) / total
    f1 = sum(s for d, s in dur.items() if d <= 1) / total
    ffed = sum(s for d, s in dur.items() if d >= FED) / total
    zr = sorted(o["runs"].get(0, []))
    lowr = sorted([x for d in (0, 1) for x in o["runs"].get(d, [])])

    def p(v, q):
        return (v[min(len(v) - 1, int(len(v) * q))] * 1000.0) if v else float("nan")
    return {
        "mean": mean, "median": med, "f0": f0, "f_le1": f1, "f_ge%d" % FED: ffed,
        "longest_zero_ms": (max(zr) * 1000.0) if zr else 0.0,
        "zero_p50_ms": p(zr, .5), "zero_p90_ms": p(zr, .9), "zero_n": len(zr),
        "low_p50_ms": p(lowr, .5), "low_p90_ms": p(lowr, .9), "low_n": len(lowr),
    }


def main():
    rep = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
    srv = str(rep.get("server_pid"))
    arms = {a["label"]: a for a in rep["arms"]}
    for a in arms.values():
        se, ee = epoch(a["start_utc"]), epoch(a["end_utc"])
        if se and ee:
            a["start_epoch"], a["end_epoch"] = se, ee

    out = {}
    for tr in rep["traces"]:
        dump = tr.get("dump")
        if not dump:
            continue
        lab = "etw-" + dump.split("-r")[-1].split(".")[0]
        arm = arms[lab]
        q = build(dump, srv)
        lo0, hi0 = arm["start_epoch"], arm["end_epoch"]

        # WINDOW-EDGE CORRECTION. The arm window is the PYTHON SUBPROCESS wall time: it
        # includes interpreter start, HTTP connect and teardown, during which the queues are
        # legitimately empty. Counting that as starvation inflates depth-0 by ~25%.
        # Tighten to the GPU-ACTIVE SPAN -- first submit to last completion inside the arm --
        # and report the discarded boundary duration rather than hiding it.
        subs = [s["ts"] for rec in q.values() for s in rec["sub"].values() if lo0 <= s["ts"] <= hi0]
        comps = [c["ts"] for rec in q.values() for c in rec["comp"].values() if lo0 <= c["ts"] <= hi0]
        lo, hi = (min(subs), max(comps)) if subs and comps else (lo0, hi0)
        discarded = (lo - lo0) + (hi0 - hi)
        print("\n" + "=" * 104)
        print("ARM %s   raw window %.4f s   GPU-ACTIVE SPAN %.4f s   DISCARDED BOUNDARY %.4f s (%.1f%%)"
              % (lab, hi0 - lo0, hi - lo, discarded, 100.0 * discarded / (hi0 - lo0)))
        print("  server-side work for comparison: prompt_ms=%s + predicted_ms=%s"
              % (arm.get("prompt_ms"), arm.get("predicted_ms")))
        print("=" * 104)

        rows = []
        for hq, rec in q.items():
            o_raw = occupancy(rec, lo0, hi0)
            o = occupancy(rec, lo, hi)
            if o["n_sub"] == 0:
                continue
            o["f0_raw"] = stats(o_raw)["f0"]
            rows.append((hq, o, stats(o)))
        rows.sort(key=lambda r: -r[1]["n_sub"])

        # UNION over the DEEP COMPUTE queues. Per-queue depth-0 is NOT starvation: queue A
        # can be empty while queue B is executing. The GPU is only unfed when EVERY compute
        # queue is empty. The 32-submit serial queue is a per-token clock, not compute, and
        # is deliberately excluded (heuristic: deep queues carry >100 submits).
        deep = {hq: rec for hq, rec in q.items() if len(rec["sub"]) > 100}
        if deep:
            merged = {"sub": {}, "comp": {}}
            for hq, rec in deep.items():
                for fv, v in rec["sub"].items():
                    merged["sub"][(hq, fv)] = v
                for fv, v in rec["comp"].items():
                    merged["comp"][(hq, fv)] = v
            ou = occupancy(merged, lo, hi)
            su = stats(ou)
            ou["f0_raw"] = stats(occupancy(merged, lo0, hi0))["f0"]
            print("\n  UNION of %d deep compute queues  (GPU unfed only when ALL are empty)" % len(deep))
            print("    TIME-WEIGHTED  mean_depth=%.3f  median_depth=%s" % (su["mean"], su["median"]))
            print("    OCCUPANCY      depth0=%.2f%%   depth<=1=%.2f%%   depth>=%d=%.2f%%"
                  % (100 * su["f0"], 100 * su["f_le1"], FED, 100 * su["f_ge%d" % FED]))
            print("    STARVATION     longest_zero=%.4f ms  zero n=%-4d p50=%.4f p90=%.4f ms"
                  % (su["longest_zero_ms"], su["zero_n"], su["zero_p50_ms"], su["zero_p90_ms"]))
            rows.append(("UNION-deep-compute", ou, su))
        out[lab] = rows
        for hq, o, s in rows:
            print("\n  queue %s   submits(trace)=%d  server_share=%.0f%%" % (hq, o["n_sub"], 100 * o["server_share"]))
            print("    EDGE  reconstructed_initial_depth=%-3d  orphan_completes=%-3d (in flight at trace start)"
                  "  orphan_submits=%-3d (in flight at trace end)"
                  % (o["init_depth"], o["orphan_comp"], o["orphan_sub"]))
            print("    TIME-WEIGHTED  mean_depth=%.3f  median_depth=%s  integrated=%.4f s"
                  % (s["mean"], s["median"], o["total_s"]))
            print("    OCCUPANCY      depth0=%.2f%%   depth<=1=%.2f%%   depth>=%d=%.2f%%"
                  "     (raw-window depth0 was %.2f%% -- edge inflation)"
                  % (100 * s["f0"], 100 * s["f_le1"], FED, 100 * s["f_ge%d" % FED], 100 * o["f0_raw"]))
            print("    STARVATION     longest_zero=%.4f ms   zero n=%-4d p50=%.4f p90=%.4f ms"
                  % (s["longest_zero_ms"], s["zero_n"], s["zero_p50_ms"], s["zero_p90_ms"]))
            print("    LOW (<=1)      n=%-4d p50=%.4f p90=%.4f ms" % (s["low_n"], s["low_p50_ms"], s["low_p90_ms"]))
            top = sorted(o["dur"].items(), key=lambda x: -x[1])[:6]
            print("    dwell by depth (ms): %s" % ", ".join("d%d=%.2f" % (d, v * 1000) for d, v in top))

    # ---- licensing: do the two healthy arms agree? ----
    print("\n" + "=" * 104)
    print("LICENSING: do the two identical healthy arms agree closely enough to use this on degraded traces?")
    print("=" * 104)
    labs = list(out)
    if len(labs) != 2:
        print("  need exactly 2 arms")
        return
    a = {hq: (o, s) for hq, o, s in out[labs[0]]}
    b = {hq: (o, s) for hq, o, s in out[labs[1]]}
    ok = True
    print("  %-20s %14s %14s %10s" % ("queue / metric", labs[0], labs[1], "delta"))
    for hq in a:
        if hq not in b:
            continue
        for key, fmt, tol in (("mean", "%.3f", 0.10), ("f0", "%.4f", 0.05), ("f_le1", "%.4f", 0.05),
                              ("f_ge%d" % FED, "%.4f", 0.05)):
            va, vb = a[hq][1][key], b[hq][1][key]
            d = abs(va - vb)
            rel = d / max(abs(va), 1e-9)
            flag = ""
            # Relative tolerance is degenerate near zero: the serial clock queue has
            # mean_depth 0.006 in both arms, and a 0.0006 absolute difference tripped a
            # 10% relative gate as "DISAGREE". Require BOTH relative and a small absolute
            # floor. (This gate fired incorrectly on first run; fixed here, not hidden.)
            if key == "mean":
                bad = rel > tol and d > 0.05
            else:
                bad = d > tol
            if bad:
                flag = "  <-- DISAGREE"
                ok = False
            print("  %-20s %14s %14s %10.4f%s" % (hq[-10:] + " " + key, fmt % va, fmt % vb, d, flag))
    print("\n  >>> %s" % ("LICENSED: healthy arms agree; the statistic may carry the degraded comparison."
                          if ok else
                          "NOT LICENSED: healthy arms disagree. Quantify why BEFORE collecting more traces."))


if __name__ == "__main__":
    main()
