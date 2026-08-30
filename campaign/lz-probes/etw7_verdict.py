"""ETW7: the GPU-active-span equivalence gate, executable rather than documentary.

The span is ENDOGENOUS to the event stream -- it is derived from first-submit to
last-complete. So a degraded state can shrink or stretch its own denominator and then
"pass" an occupancy comparison by construction. This module refuses that.

TWO-STAGE, conservative:
  Stage 1  absolute: is each span near the fixed workload's expected active span?
           (healthy measured 314.5 / 314.6 ms for prompt 10.4 + predicted 305.1 ms)
  Stage 2  relative: are the healthy and degraded spans within a predeclared band?

The analyzer ALWAYS reports active_span_ms, span_delta_ms and span_delta_pct. It emits
FEED_STARVATION_SUPPORTED or FEED_STARVATION_DISFAVOURED only when BOTH stages pass.
Otherwise the verdict is SPAN_NON_EQUIVALENT and the raw surface is printed instead.
Nothing is silently normalised.

Fixtures (`--selftest`) institutionalise the two failure modes this campaign actually hit:
  * a gate that never fires is a hypothesis (my ETW1 elevation guard, verified firing)
  * a gate that fires WRONGLY is worse (my ETW4 licensing gate: a 0.0006 absolute
    difference on a near-zero mean tripped a 10% RELATIVE tolerance and returned
    NOT LICENSED on a valid baseline). Near-zero quantities need
    max(absolute_floor, relative_tolerance), never relative alone.

Usage: etw7_verdict.py --selftest
       etw7_verdict.py <healthy_report.json> <degraded_report.json>
"""
import json, io, sys

# ---- predeclared bands. Written before any degraded trace exists. ----
EXPECTED_SPAN_MS = 315.0      # prompt 10.4 + predicted 305.1, the fixed 32-token shape
SPAN_ABS_TOL_PCT = 15.0       # stage 1: each span vs the workload expectation
SPAN_REL_TOL_PCT = 10.0       # stage 2: healthy vs degraded
# ---------------------------------------------------------------------------
# COMPARISON HIERARCHY. A later analyzer must not treat the numbers below as
# immutable machine constants. They are validated observables from a healthy
# SHORT-session trace, not a universal baseline.
#
#   PRIMARY          within-trace healthy -> degraded -> recovery, same ETW
#                    session and same server epoch. This is the comparison the
#                    continuous recorder was built to produce, and it is
#                    SELF-CONTROLLED: any session cost applies to both arms.
#   SECONDARY        other states observed during that same continuous session.
#   CROSS-CHECK ONLY HEALTHY_CROSSCHECK below (ETW4, short sessions, 04:34).
#   HISTORICAL       pre-ETW ADR-0044 regimes (~106 / ~97-99 / ~65 / ~27.5).
#
# LOCAL COMPARATOR RULE. Do NOT classify a snapshot's pre-trigger portion as
# "healthy" merely because its rate exceeds the watcher's 90 tok/s threshold.
# Identify the comparator from the actual deep-probe observations inside the
# ring. A trace running ~97 -> ~65 -> ~97 answers the INC-A question perfectly
# well even though no part of it touches 106.
# ---------------------------------------------------------------------------
HEALTHY_CROSSCHECK = {        # ETW4, union over deep compute queues, both arms
    "mean_depth": 3.9075, "f_depth0": 0.1180, "f_depth_ge3": 0.7347,
    "longest_zero_ms": 1.29,
    "band_depth": 0.005,      # cross-arm agreement
    "band_pp": 0.0025,
}


def tolerant_equal(a, b, rel_tol, abs_floor):
    """max(absolute floor, relative tolerance).

    Relative tolerance alone is DEGENERATE NEAR ZERO. This is the ETW4 bug, fixtured:
    mean_depth 0.006 vs 0.006 differs by 0.0006, which is 10% relative, and a naive
    relative gate called that a disagreement.
    """
    d = abs(a - b)
    return d <= max(abs_floor, rel_tol * max(abs(a), abs(b)))


def span_gate(healthy_span_ms, degraded_span_ms, expected_ms=EXPECTED_SPAN_MS):
    """Returns (licensed: bool, reason: str, metrics: dict). Never normalises."""
    m = {
        "healthy_span_ms": round(healthy_span_ms, 3),
        "degraded_span_ms": round(degraded_span_ms, 3),
        "expected_span_ms": expected_ms,
        "span_delta_ms": round(degraded_span_ms - healthy_span_ms, 3),
        "span_delta_pct": round(100.0 * (degraded_span_ms - healthy_span_ms) / healthy_span_ms, 2)
        if healthy_span_ms else float("nan"),
        "healthy_vs_expected_pct": round(100.0 * (healthy_span_ms - expected_ms) / expected_ms, 2),
        "degraded_vs_expected_pct": round(100.0 * (degraded_span_ms - expected_ms) / expected_ms, 2),
    }
    # Stage 1: absolute, each side against the fixed workload expectation.
    for side, v in (("healthy", m["healthy_vs_expected_pct"]), ("degraded", m["degraded_vs_expected_pct"])):
        if abs(v) > SPAN_ABS_TOL_PCT:
            return False, ("stage1 absolute: %s span is %+.1f%% from the %.0f ms workload "
                           "expectation (tol +-%.0f%%)" % (side, v, expected_ms, SPAN_ABS_TOL_PCT)), m
    # Stage 2: relative, healthy vs degraded.
    if abs(m["span_delta_pct"]) > SPAN_REL_TOL_PCT:
        return False, ("stage2 relative: spans differ by %+.1f%% (tol +-%.0f%%)"
                       % (m["span_delta_pct"], SPAN_REL_TOL_PCT)), m
    return True, "both stages passed", m


REFUSED_NO_LOCAL = ("NO_LOCAL_COMPARATOR - PRIMARY EVIDENCE MISSING, VERDICT REFUSED")


def occupancy_verdict(deg, local=None, mode="production"):
    """Only called when the span gate licenses it. FAILS CLOSED without PRIMARY evidence.

    deg   = union stats from the DEGRADED portion of the trace.
    local = union stats from the PRE-TRIGGER portion of the SAME trace. This is the
            PRIMARY comparator.
    mode  = "production" (default) REFUSES a verdict when local is absent.
            "crosscheck" is the ONLY way to reach HEALTHY_CROSSCHECK, and naming it is
            the point: a cross-session reference must be a deliberate, visible choice.

    There is deliberately no silent fallback. Substituting the ETW4 short-session floor
    for missing within-trace evidence would downgrade PRIMARY to CROSS-CHECK without
    anyone noticing, which is precisely how a stale observable becomes a machine constant.
    """
    if local is None:
        if mode != "crosscheck":
            return REFUSED_NO_LOCAL
        local = HEALTHY_CROSSCHECK
    ref = local
    rose = deg["f_depth0"] > ref["f_depth0"] + 10 * HEALTHY_CROSSCHECK["band_pp"]
    fell = deg["f_depth_ge3"] < ref["f_depth_ge3"] - 10 * HEALTHY_CROSSCHECK["band_pp"]
    longer = deg["longest_zero_ms"] > 2.0 * ref["longest_zero_ms"]
    near = (tolerant_equal(deg["f_depth0"], ref["f_depth0"], 0.02, HEALTHY_CROSSCHECK["band_pp"] * 4)
            and tolerant_equal(deg["f_depth_ge3"], ref["f_depth_ge3"], 0.02, HEALTHY_CROSSCHECK["band_pp"] * 4))
    if rose and fell and longer:
        return "FEED_STARVATION_SUPPORTED"
    if near:
        return "FEED_STARVATION_DISFAVOURED"
    return "MIXED_SURFACE - report the surface, do NOT force a binary classification"


def report(healthy_span, degraded_span, deg_stats, local=None, mode="production"):
    lic, reason, m = span_gate(healthy_span, degraded_span)
    print("  active_span_ms  healthy=%.3f  degraded=%.3f" % (m["healthy_span_ms"], m["degraded_span_ms"]))
    print("  span_delta_ms   %+.3f      span_delta_pct  %+.2f%%" % (m["span_delta_ms"], m["span_delta_pct"]))
    print("  vs expectation  healthy %+.2f%%   degraded %+.2f%%" % (m["healthy_vs_expected_pct"], m["degraded_vs_expected_pct"]))
    if not lic:
        print("  VERDICT: SPAN_NON_EQUIVALENT - OCCUPANCY COMPARISON NOT LICENSED")
        print("           %s" % reason)
        print("  raw surface (reported instead of a verdict):")
        for k, v in sorted(deg_stats.items()):
            print("      %-18s %s" % (k, v))
        return "SPAN_NON_EQUIVALENT"
    v = occupancy_verdict(deg_stats, local=local, mode=mode)
    print("  span gate: LICENSED (%s)" % reason)
    if v == REFUSED_NO_LOCAL:
        print("  VERDICT: %s" % v)
        print("           supply the pre-trigger union stats from the SAME trace as local=,")
        print("           or pass mode='crosscheck' to deliberately use the ETW4 floor.")
    else:
        print("  VERDICT: %s" % v)
    return v


def selftest():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print("  [%s] %-56s got=%s" % ("PASS" if good else "FAIL", name, got))

    print("--- span gate ---")
    check("equivalent spans license the comparison", span_gate(314.5, 316.0)[0], True)
    check("degraded span stretched 40% -> refused", span_gate(314.5, 440.0)[0], False)
    check("degraded span shrunk 40% -> refused", span_gate(314.5, 190.0)[0], False)
    check("both spans far from workload expectation -> refused", span_gate(500.0, 505.0)[0], False)
    check("stage2 catches equal-but-drifted pair", span_gate(300.0, 340.0)[0], False)

    print("--- near-zero tolerance (the ETW4 gate that fired WRONGLY) ---")
    # 0.0068, not 0.0066: at 0.0066 the delta is 0.0006 while 10% of 0.0066 is 0.00066, so a
    # naive relative gate would NOT have fired and the fixture asserted nothing. The fixture
    # caught that on its first run -- which is exactly what it is for.
    check("0.006 vs 0.0068 is EQUAL under abs floor", tolerant_equal(0.006, 0.0068, 0.10, 0.05), True)
    check("naive relative alone WOULD have called it unequal", abs(0.006 - 0.0068) > 0.10 * 0.0068, True)
    check("3.910 vs 3.905 equal", tolerant_equal(3.910, 3.905, 0.10, 0.05), True)
    check("3.9 vs 1.2 NOT equal", tolerant_equal(3.9, 1.2, 0.10, 0.05), False)

    print("--- FAIL CLOSED: production analysis without a within-trace comparator ---")
    starved = {"f_depth0": 0.55, "f_depth_ge3": 0.20, "longest_zero_ms": 9.0}
    check("no local= -> verdict REFUSED", occupancy_verdict(starved), REFUSED_NO_LOCAL)
    check("no local= -> does NOT emit feed-starvation",
          "STARVATION" in occupancy_verdict(starved), False)
    check("crosscheck mode must be NAMED to reach the ETW4 floor",
          occupancy_verdict(starved, mode="crosscheck"), "FEED_STARVATION_SUPPORTED")
    check("end-to-end report() also refuses without local=",
          report(314.5, 316.0, starved), REFUSED_NO_LOCAL)

    print("--- occupancy verdict, against a LOCAL within-trace comparator ---")
    # A local arc that never touches 106: ~97 healthy prehistory is a valid comparator.
    local97 = {"f_depth0": 0.150, "f_depth_ge3": 0.700, "longest_zero_ms": 1.60}
    same = {"f_depth0": 0.1520, "f_depth_ge3": 0.6985, "longest_zero_ms": 1.63}
    mixed = {"f_depth0": 0.30, "f_depth_ge3": 0.69, "longest_zero_ms": 1.65}
    check("starved vs local -> SUPPORTED",
          occupancy_verdict(starved, local=local97), "FEED_STARVATION_SUPPORTED")
    check("unchanged vs local -> DISFAVOURED",
          occupancy_verdict(same, local=local97), "FEED_STARVATION_DISFAVOURED")
    check("mixed vs local -> not forced binary",
          occupancy_verdict(mixed, local=local97).startswith("MIXED_SURFACE"), True)
    check("a ~97 local comparator is NOT judged against the 106-era floor",
          occupancy_verdict(same, local=local97) != occupancy_verdict(same, mode="crosscheck"), True)

    print("--- end-to-end: a stretched degraded span must NOT reach an occupancy verdict ---")
    v = report(314.5, 470.0, starved, local=local97)
    check("stretched span refuses even a starved-looking trace", v, "SPAN_NON_EQUIVALENT")

    print("\n%s" % ("ALL FIXTURES PASS" if ok else "FIXTURE FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
