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
HEALTHY = {                   # ETW4, union over deep compute queues, both arms
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


def occupancy_verdict(deg):
    """Only called when the span gate licenses it. deg = union stats from the degraded trace."""
    rose = deg["f_depth0"] > HEALTHY["f_depth0"] + 10 * HEALTHY["band_pp"]
    fell = deg["f_depth_ge3"] < HEALTHY["f_depth_ge3"] - 10 * HEALTHY["band_pp"]
    longer = deg["longest_zero_ms"] > 2.0 * HEALTHY["longest_zero_ms"]
    near = (tolerant_equal(deg["f_depth0"], HEALTHY["f_depth0"], 0.02, HEALTHY["band_pp"] * 4)
            and tolerant_equal(deg["f_depth_ge3"], HEALTHY["f_depth_ge3"], 0.02, HEALTHY["band_pp"] * 4))
    if rose and fell and longer:
        return "FEED_STARVATION_SUPPORTED"
    if near:
        return "FEED_STARVATION_DISFAVOURED"
    return "MIXED_SURFACE - report the surface, do NOT force a binary classification"


def report(healthy_span, degraded_span, deg_stats):
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
    v = occupancy_verdict(deg_stats)
    print("  span gate: LICENSED (%s)" % reason)
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

    print("--- occupancy verdict ---")
    starved = {"f_depth0": 0.55, "f_depth_ge3": 0.20, "longest_zero_ms": 9.0}
    same = {"f_depth0": 0.1185, "f_depth_ge3": 0.7340, "longest_zero_ms": 1.31}
    mixed = {"f_depth0": 0.30, "f_depth_ge3": 0.70, "longest_zero_ms": 1.30}
    check("starved -> SUPPORTED", occupancy_verdict(starved), "FEED_STARVATION_SUPPORTED")
    check("unchanged -> DISFAVOURED", occupancy_verdict(same), "FEED_STARVATION_DISFAVOURED")
    check("mixed -> not forced binary", occupancy_verdict(mixed).startswith("MIXED_SURFACE"), True)

    print("--- end-to-end: a stretched degraded span must NOT reach an occupancy verdict ---")
    v = report(314.5, 470.0, starved)
    check("stretched span refuses even a starved-looking trace", v, "SPAN_NON_EQUIVALENT")

    print("\n%s" % ("ALL FIXTURES PASS" if ok else "FIXTURE FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
