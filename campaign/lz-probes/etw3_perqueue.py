"""ETW3: per-hHwQueue scoping. Gating question, and nothing else.

    Within each hHwQueue, does the fence lifecycle become serial and stable enough that
    completion -> next submission is a valid measure of the PRE-SUBMISSION gap?

Why this is mandatory: ETW2 proved a 628/628 join on a GLOBAL timeline that is
structurally invalid. 540 of 627 global complete->next-submit gaps were NEGATIVE and
submit->complete summed to ~2.9x the arm wall, because three hardware queues overlap.
A perfect join on an invalid timeline manufactures nonsense confidently.

Rules, mechanical:
  * partition STRICTLY by hHwQueue
  * within a queue order by ProgressFenceValue AND by timestamp, and REPORT disagreements
  * conservation reported INDEPENDENTLY PER QUEUE
  * completion -> next submit derived ONLY within the same queue
  * submit -> complete is the after-submission interval
  * negative / zero / duplicate / missing are REPORTED, never normalised away
  * queue identity is preserved on every derived row
  * (hHwQueue, ProgressFenceValue) is the EXECUTION IDENTITY; pid is ATTRIBUTION METADATA.
    A shared queue is compatible with the join - 12 of 640 eid450 events are pid 4 - so
    server-attributed submissions are TAGGED, and both all-pid and server-only views print.

If per-queue gaps are predominantly non-negative and stable across the two arms, the
two-way decomposition is licensed. If a queue carries multiple in-flight fences, then even
per-queue completion->next-submit is NOT the host gap, and the next step is in-flight
depth, not forcing seriality. In-flight depth is therefore measured here, as the
discriminator between those two branches.

Usage: etw3_perqueue.py <report.json>
"""
import collections, io, json, statistics, sys

sys.path.insert(0, r"C:\work\commandcenter\campaign\lz-probes")
from etw2_join import events, epoch   # noqa: E402  (carries both regression fixtures)


def pct(v, p):
    if not v:
        return float("nan")
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * p))]


def collect(dump, srv, lo, hi):
    """Per-queue submit/complete records. Returns {hq: {"sub": {...}, "comp": {...}}}."""
    q = collections.defaultdict(lambda: {"sub": {}, "comp": {}, "dup": collections.Counter()})
    for rtask, opcode, eid, pid, ts, d in events(dump):
        if rtask != "DmaPacket" or opcode != "Info":
            continue
        fv, hq = d.get("ProgressFenceValue"), d.get("hHwQueue")
        if fv is None or hq is None or ts is None:
            continue
        side = "sub" if eid == "450" else ("comp" if eid == "451" else None)
        if side is None:
            continue
        bucket = q[hq][side]
        if fv in bucket:
            q[hq]["dup"][side] += 1
        bucket[fv] = {"ts": ts, "pid": pid, "server": pid == srv}
    return q


def analyse_queue(hq, rec, lo, hi):
    sub, comp = rec["sub"], rec["comp"]
    # Restrict to submissions whose SUBMIT falls in the arm window; completion may trail.
    in_arm = {fv: v for fv, v in sub.items() if lo <= v["ts"] <= hi}
    matched, missing = {}, 0
    for fv, v in in_arm.items():
        c = comp.get(fv)
        if c is None:
            missing += 1
            continue
        matched[fv] = (v, c)

    # Ordering: does fence order agree with timestamp order?
    byfence = sorted(matched.items(), key=lambda kv: int(kv[0]))
    inversions = sum(1 for i in range(1, len(byfence))
                     if byfence[i][1][0]["ts"] < byfence[i - 1][1][0]["ts"])

    # In-flight depth over the queue timeline (ALL pids - the queue is shared).
    marks = []
    for fv, (s, c) in matched.items():
        marks.append((s["ts"], +1))
        marks.append((c["ts"], -1))
    marks.sort()
    depth, maxdepth, depth_hist = 0, 0, collections.Counter()
    for _, delta in marks:
        depth += delta
        maxdepth = max(maxdepth, depth)
        depth_hist[depth] += 1

    # Same-queue completion -> next submission, ordered by submit time.
    def gaps(items):
        seq = sorted(items, key=lambda kv: kv[1][0]["ts"])
        out = []
        for i in range(1, len(seq)):
            prev_comp = seq[i - 1][1][1]["ts"]
            this_sub = seq[i][1][0]["ts"]
            out.append((this_sub - prev_comp) * 1000.0)
        return out

    all_gaps = gaps(list(matched.items()))
    srv_gaps = gaps([kv for kv in matched.items() if kv[1][0]["server"]])
    exe = [(c["ts"] - s["ts"]) * 1000.0 for s, c in matched.values()]
    srv_n = sum(1 for s, _ in matched.values() if s["server"])

    return {
        "hq": hq, "n_sub_in_arm": len(in_arm), "matched": len(matched), "missing": missing,
        "dup": dict(rec["dup"]), "server_n": srv_n, "other_n": len(matched) - srv_n,
        "fence_ts_inversions": inversions,
        "max_depth": maxdepth,
        "depth_hist": dict(sorted(depth_hist.items())),
        "exe": exe, "all_gaps": all_gaps, "srv_gaps": srv_gaps,
    }


def main():
    rep = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
    srv = str(rep.get("server_pid"))
    arms = {a["label"]: a for a in rep["arms"]}
    for a in arms.values():
        se, ee = epoch(a["start_utc"]), epoch(a["end_utc"])
        if se and ee:
            a["start_epoch"], a["end_epoch"] = se, ee

    per_arm = {}
    for tr in rep["traces"]:
        dump = tr.get("dump")
        if not dump:
            continue
        lab = "etw-" + dump.split("-r")[-1].split(".")[0]
        arm = arms[lab]
        q = collect(dump, srv, arm["start_epoch"], arm["end_epoch"])
        rows = [analyse_queue(hq, rec, arm["start_epoch"], arm["end_epoch"])
                for hq, rec in q.items()]
        rows = [r for r in rows if r["matched"] > 0]
        rows.sort(key=lambda r: -r["matched"])
        per_arm[lab] = rows

        print("\n" + "=" * 100)
        print("ARM %s   queues=%d" % (lab, len(rows)))
        print("=" * 100)
        for r in rows:
            print("\n  queue %s" % r["hq"])
            print("    conservation   submits_in_arm=%-5d matched=%-5d missing_completion=%-4d dup=%s"
                  % (r["n_sub_in_arm"], r["matched"], r["missing"], r["dup"] or "none"))
            print("    attribution    server=%-5d other_pid=%-5d   (queue is SHARED; pid is metadata)"
                  % (r["server_n"], r["other_n"]))
            print("    ordering       fence-vs-timestamp inversions = %d" % r["fence_ts_inversions"])
            print("    IN-FLIGHT      max_depth=%-3d  depth histogram=%s"
                  % (r["max_depth"], r["depth_hist"]))
            e = r["exe"]
            print("    submit->complete   n=%-5d p10=%8.4f p50=%8.4f p90=%8.4f ms"
                  % (len(e), pct(e, .1), pct(e, .5), pct(e, .9)))
            for nm, g in (("complete->next submit (all pids)", r["all_gaps"]),
                          ("complete->next submit (server)  ", r["srv_gaps"])):
                if not g:
                    continue
                neg = sum(1 for x in g if x < 0)
                zero = sum(1 for x in g if x == 0)
                print("    %s n=%-5d p10=%8.4f p50=%8.4f p90=%8.4f  negative=%d (%.0f%%) zero=%d"
                      % (nm, len(g), pct(g, .1), pct(g, .5), pct(g, .9), neg, 100.0 * neg / len(g), zero))

    # ---- the gating verdict ----
    print("\n" + "=" * 100)
    print("GATING QUESTION: is per-queue completion -> next submit a valid PRE-SUBMISSION gap?")
    print("=" * 100)
    labs = list(per_arm)
    serial = True
    for lab in labs:
        for r in per_arm[lab]:
            if r["max_depth"] > 1:
                serial = False
    print("  every queue serial (max in-flight depth <= 1)?  %s" % ("YES" if serial else "NO"))
    for lab in labs:
        for r in per_arm[lab]:
            g = r["srv_gaps"] or r["all_gaps"]
            neg = 100.0 * sum(1 for x in g if x < 0) / len(g) if g else float("nan")
            print("    %-8s %s  max_depth=%-3d  negative_gaps=%.0f%%  median_gap=%.4f ms"
                  % (lab, r["hq"][-10:], r["max_depth"], neg, pct(g, .5) if g else float("nan")))

    if serial:
        print("\n  >>> LICENSED: queues are serial. The two-way decomposition is valid per queue.")
    else:
        print("\n  >>> NOT LICENSED: at least one queue carries MULTIPLE IN-FLIGHT FENCES.")
        print("      Per-queue 'completion -> next submit' is NOT the host gap you want.")
        print("      Next step is fence/in-flight DEPTH analysis, not forcing seriality.")
        print("      DO NOT circularize.")

    # cross-arm stability of what we can measure
    if len(labs) == 2:
        print("\n  --- cross-arm stability (same queue, both arms) ---")
        a, b = per_arm[labs[0]], per_arm[labs[1]]
        bmap = {r["hq"]: r for r in b}
        for r in a:
            o = bmap.get(r["hq"])
            if not o:
                continue
            print("    %s  exe_p50 %.4f vs %.4f ms   matched %d vs %d   max_depth %d vs %d"
                  % (r["hq"][-10:], pct(r["exe"], .5), pct(o["exe"], .5),
                     r["matched"], o["matched"], r["max_depth"], o["max_depth"]))


if __name__ == "__main__":
    main()
