#!/usr/bin/env python3
r"""Reduce a multi-HOUR b70tools stream to a per-bucket time series. The shape IS the deliverable.

WHY THIS EXISTS. Every duty-cycle figure in this campaign divides by a frozen ~92-second prefill
burst (claim register #30), and its stated retirement condition is a SUSTAINED capture. But the
existing reducer, ``sat_reference_capture.reduce_stream``, collapses a whole capture to one
``p50/p95/max/mean`` per card and then serialises EVERY interval into the receipt --
``watts_by_interval``, 14,400 floats per card over four hours. Collapsing four hours to a single
p50 would swap one mislabelled denominator for another, and the thing we actually need is the
SHAPE: does power plateau, when, and how far does the idle floor drift.

Nothing in this repo does time-bucketed reduction. I checked ff-probes, lz-probes,
hearth/rotation/telemetry.py and corpus/ -- the closest is ``reduce_stream``'s TWO fixed windows
(ambient / burst), whose straddle semantics are correct and are copied here.

TWO TRAPS THIS ENCODES, both measured rather than assumed:

1. ⚠ TEMPERATURE COUNTERS ARE EMITTED ON CHANGE, NOT PER TICK. A 260 s capture carried 7-13
   readings per counter per card. So most one-minute buckets legitimately hold ZERO temperature
   readings, and a bucket with none reports ``null`` with a reason -- never a carried-forward
   value and never a zero. Forward-filling would invent a plateau, which is precisely the thing
   this capture exists to detect.

2. ⚠ READING-COUNT PERCENTILES UNDER-WEIGHT A PLATEAU. On-change emission is dense while a
   temperature MOVES and sparse once it settles, so a percentile over readings over-represents
   the ramp -- the opposite of what a sustained reference wants. Power is different: the energy
   counter streams per tick, so watt percentiles here are over regular ~1 s intervals and are
   sound. The receipt says which is which rather than leaving a reader to assume.

⚠ BOARD POWER IS UNOBTAINABLE AT ANY CAPTURE LENGTH. ``card.energy_j_counter`` is emitted ONCE per
capture in this build, so only ``gpu.energy_j_counter`` (the GPU tile) can be differenced. This
tool reports tile watts and says so; it never implies board watts.

Memory: one pass, and no more than one bucket's worth of watts is held at a time after sorting.
A four-hour capture costs the same as a four-minute one.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sat_cell_runner as runner  # noqa: E402

NS = 1_000_000_000
PROBE = "SAT-L1-SUSTAINED"
SCHEMA_VERSION = 1

#: One minute. Small enough to show a knee, large enough that a 4-hour capture is 240 rows.
DEFAULT_BUCKET_S = 60.0

#: An interval this far from the 1 Hz cadence is irregular and is counted and reported, the way
#: reduce_stream does it -- a stalled or throttled collector must not look like quiet hardware.
MIN_DT_S, MAX_DT_S = 0.5, 2.0


def _stats(watts: list) -> dict:
    """p50/p95/max/mean over a bucket's watts. Nearest-rank p95, as the harness uses elsewhere."""
    if not watts:
        return {"intervals": 0, "p50_w": None, "p95_w": None, "max_w": None, "mean_w": None}
    ordered = sorted(watts)
    return {
        "intervals": len(ordered),
        "p50_w": round(statistics.median(ordered), 2),
        "p95_w": round(ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))], 2),
        "max_w": round(ordered[-1], 2),
        "mean_w": round(statistics.fmean(ordered), 2),
    }


def temperature_readings(events_path) -> dict:
    """``{(bdf, counter): [(t_ns, celsius), ...]}`` for the B70s, in time order.

    Deliberately a separate pass from the power intervals: the counters have different cadences
    (per-tick energy vs on-change temperature) and conflating them is how a plateau gets invented.
    """
    ident: dict = {}
    rows: dict = {}
    path = Path(events_path)
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn trailing line during a live tail is normal
            if row.get("k") == "ai":
                ident[row.get("a")] = {"desc": row.get("desc") or "", "bdf": row.get("bdf")}
                continue
            if row.get("k") != "ms" or row.get("n") not in runner.THERMAL_COUNTERS:
                continue
            info = ident.get(row.get("a")) or {}
            if "B70" not in (info.get("desc") or ""):
                continue
            try:
                rows.setdefault((info.get("bdf") or row.get("a"), row["n"]), []).append(
                    (int(row.get("t") or 0), float(row["v"])))
            except (KeyError, TypeError, ValueError):
                continue
    for key in rows:
        rows[key].sort()
    return rows


def bucketize(events_path, bucket_s: float = DEFAULT_BUCKET_S, counter: str = "gpu") -> dict:
    """The time series. Per bucket, per card: watt stats, temperature stats, coverage.

    Power intervals come from ``sat_cell_runner.card_intervals``, which keeps the per-interval
    timestamps ``reduce_stream`` reduces away AND resolves the durable PCI BDF (ADR-0042 -- the
    b70tools adapter id is session-scoped and is never an identity).
    """
    try:
        cards = runner.card_intervals(events_path, counter)
    except RuntimeError as exc:
        # card_intervals fails LOUDLY when the counter is absent, and its message names what the
        # stream does carry. Keep that message rather than flattening it to "no data" -- but
        # return it as a result, because a multi-hour reduction is often run unattended.
        return {"ok": False, "reason": str(exc), "buckets": [], "cards": {}}
    temps = temperature_readings(events_path)
    bucket_ns = int(bucket_s * NS)

    starts = [t0 for card in cards.values() for (t0, _t1, _w) in card.get("intervals") or ()]
    starts += [t for rows in temps.values() for (t, _v) in rows]
    if not starts:
        return {"ok": False, "reason": "no B70 intervals or temperature readings in the stream",
                "buckets": [], "cards": {}}
    origin = min(starts)
    last = max([t1 for card in cards.values() for (_t0, t1, _w) in card.get("intervals") or ()]
               + [t for rows in temps.values() for (t, _v) in rows])

    by_bucket: dict = {}
    straddling: dict = {}
    irregular: dict = {}
    for card in cards.values():
        bdf = card.get("bdf") or card.get("adapter")
        for (t0, t1, watts) in card.get("intervals") or ():
            dt_s = (t1 - t0) / NS
            if dt_s < MIN_DT_S or dt_s > MAX_DT_S:
                irregular[bdf] = irregular.get(bdf, 0) + 1
            b0 = (t0 - origin) // bucket_ns
            b1 = (t1 - 1 - origin) // bucket_ns
            if b0 != b1:
                # Straddles an edge: counted in NEITHER bucket and reported, the same rule
                # reduce_stream uses for its window edges. Splitting it would fabricate samples.
                straddling[bdf] = straddling.get(bdf, 0) + 1
                continue
            by_bucket.setdefault(int(b0), {}).setdefault(bdf, []).append(watts)

    temp_bucket: dict = {}
    for (bdf, counter_name), rows in temps.items():
        for (t, celsius) in rows:
            idx = int((t - origin) // bucket_ns)
            temp_bucket.setdefault(idx, {}).setdefault((bdf, counter_name), []).append(celsius)

    bdfs = sorted({card.get("bdf") or card.get("adapter") for card in cards.values()})
    n_buckets = int((last - origin) // bucket_ns) + 1
    buckets = []
    for idx in range(n_buckets):
        entry = {
            "bucket": idx,
            "t_start_s": round(idx * bucket_s, 1),
            "t_end_s": round((idx + 1) * bucket_s, 1),
            "cards": {},
        }
        for bdf in bdfs:
            watts = (by_bucket.get(idx) or {}).get(bdf) or []
            card_entry = {"power": _stats(watts),
                          "seconds_covered": round(len(watts) * 1.0, 1) if watts else 0.0}
            for counter_name in runner.THERMAL_COUNTERS:
                readings = (temp_bucket.get(idx) or {}).get((bdf, counter_name)) or []
                if readings:
                    ordered = sorted(readings)
                    card_entry[counter_name] = {
                        "readings": len(ordered),
                        "p50_c": round(statistics.median(ordered), 1),
                        "max_c": round(ordered[-1], 1),
                        "min_c": round(ordered[0], 1),
                        "reason": None,
                    }
                else:
                    # A LOUD NULL. On-change emission means an unchanged temperature emits
                    # nothing, so an empty bucket is "no reading", never "cool" and never the
                    # previous value carried forward.
                    card_entry[counter_name] = {
                        "readings": 0, "p50_c": None, "max_c": None, "min_c": None,
                        "reason": "no reading in this bucket; counters emit ON CHANGE, so this "
                                  "is silence, not a value",
                    }
            entry["cards"][bdf] = card_entry
        buckets.append(entry)

    summary = {}
    for bdf in bdfs:
        all_watts = [w for idx in range(n_buckets)
                     for w in ((by_bucket.get(idx) or {}).get(bdf) or [])]
        card_summary = {"power": _stats(all_watts),
                        "straddling_intervals": straddling.get(bdf, 0),
                        "irregular_intervals": irregular.get(bdf, 0)}
        for counter_name in runner.THERMAL_COUNTERS:
            rows = temps.get((bdf, counter_name)) or []
            values = [v for _t, v in rows]
            card_summary[counter_name] = {
                "readings": len(values),
                "min_c": min(values) if values else None,
                "max_c": max(values) if values else None,
                "first_c": values[0] if values else None,
                "last_c": values[-1] if values else None,
            }
        summary[bdf] = card_summary

    return {
        "ok": True,
        "counter": "%s.energy_j_counter" % counter,
        "bucket_seconds": bucket_s,
        "buckets_total": n_buckets,
        "capture_seconds": round((last - origin) / NS, 1),
        "cards": summary,
        "buckets": buckets,
        "caveats": {
            "board_power": "UNOBTAINABLE at any capture length: card.energy_j_counter is emitted "
                           "once per capture in this build. These are GPU-TILE watts.",
            "temperature_cadence": "Temperature counters are emitted ON CHANGE, not per tick. "
                                   "Empty buckets are loud nulls; percentiles over readings "
                                   "UNDER-WEIGHT a plateau and are reported per bucket instead "
                                   "of as one capture-wide percentile.",
            "power_cadence": "Energy streams per tick, so watt stats are over regular ~1 s "
                             "intervals and are sound.",
            "straddling": "An interval crossing a bucket edge is counted in NEITHER bucket and "
                          "reported per card, the same rule reduce_stream uses for its windows.",
        },
    }


def knee(reduced: dict, bdf: str, counter: str = "vram.temperature_c",
         plateau_within_c: float = 2.0, need_buckets: int = 5) -> dict:
    """Where does the temperature stop climbing? The question Phase 2's Q6 exists to answer.

    Walks the per-bucket max for one card and one counter, and calls a plateau at the first
    bucket after which the running max moves by less than ``plateau_within_c`` for
    ``need_buckets`` consecutive buckets that HAVE readings. Buckets without readings are
    skipped, never treated as unchanged -- silence is not stability.
    """
    series = [(b["bucket"], b["t_start_s"],
               ((b["cards"].get(bdf) or {}).get(counter) or {}).get("max_c"))
              for b in reduced.get("buckets") or []]
    seen = [(idx, t, c) for idx, t, c in series if c is not None]
    if len(seen) < need_buckets + 1:
        return {"plateau": None, "reason": "only %d bucket(s) carry a %s reading; need > %d"
                                           % (len(seen), counter, need_buckets),
                "readings": len(seen)}
    peak = max(c for _i, _t, c in seen)
    for position in range(len(seen) - need_buckets):
        window = seen[position:position + need_buckets + 1]
        values = [c for _i, _t, c in window]
        if max(values) - min(values) <= plateau_within_c:
            idx, t_start, celsius = window[0]
            return {"plateau": {"bucket": idx, "t_start_s": t_start, "c": celsius,
                                "held_within_c": plateau_within_c,
                                "over_buckets": need_buckets},
                    "peak_c": peak, "readings": len(seen),
                    "reason": "first run of %d readings within %.1f C" % (need_buckets,
                                                                          plateau_within_c)}
    return {"plateau": None, "peak_c": peak, "readings": len(seen),
            "reason": "no run of %d readings stayed within %.1f C -- it never settled over this "
                      "capture, which is itself the finding" % (need_buckets, plateau_within_c)}


def write_atomic(path: Path, payload: str) -> None:
    """Write via a temp file and replace, so a partial flush never truncates a good summary."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bucketed reduction of a multi-hour b70tools stream")
    ap.add_argument("--events", required=True)
    ap.add_argument("--bucket-s", type=float, default=DEFAULT_BUCKET_S)
    ap.add_argument("--counter", default="gpu")
    ap.add_argument("--out", default=None, help="write the full reduction here (atomic)")
    ap.add_argument("--knee-bdf", default=None, help="report the thermal knee for this card")
    ap.add_argument("--summary-only", action="store_true",
                    help="print the summary and caveats, not the bucket series")
    args = ap.parse_args()

    reduced = bucketize(args.events, args.bucket_s, args.counter)
    reduced["schema_version"] = SCHEMA_VERSION
    reduced["probe"] = PROBE
    reduced["events"] = str(args.events)
    reduced["reduced_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    if reduced.get("ok") and args.knee_bdf:
        reduced["knee"] = {c: knee(reduced, args.knee_bdf, c)
                           for c in runner.THERMAL_COUNTERS}

    if args.out:
        write_atomic(Path(args.out), json.dumps(reduced, indent=1, ensure_ascii=False))

    if args.summary_only:
        shown = {k: v for k, v in reduced.items() if k != "buckets"}
        print(json.dumps(shown, indent=1, ensure_ascii=False))
    else:
        print(json.dumps(reduced, indent=1, ensure_ascii=False))
    return 0 if reduced.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
