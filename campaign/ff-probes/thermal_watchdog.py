#!/usr/bin/env python3
r"""A LIVE thermal watchdog for the replica-per-card block. Gate 8 scores; this one stops.

WHY THIS EXISTS, and it is not a hypothetical. On 2026-08-27 the replica-per-card experiment
climbed from 78 C to 96 C on the VRAM of ``0000:04:00.0`` in FORTY-EIGHT SECONDS and was still
rising ~2 C per tick when it was cut. It was cut by a LIVE 10-second watchdog. That watchdog is
gone; the campaign's current gate 8 parses a b70tools stream that has ALREADY SELF-TERMINATED --
it runs after ``stream.wait(...)`` -- and ``thermal_exceeded`` is not in the sweep-halt list, so a
cell that reaches 95 C is marked, excluded from the surface, and THE NEXT CELL LAUNCHES ANYWAY.

Gate 8 protects the dataset. It does not protect the hardware. Re-running the shape that produced
the only 96 C readings this box has ever recorded, without a live poll, would be running it with
less protection than it had the first time.

THE ONE CARD THAT MATTERS. ``0000:04:00.0`` VRAM is the binding constraint and it is a COOLING
DEFECT, not a load property: it runs 4-8 C above its sibling under identical load in every dataset
we hold, and at the 2026-08-27 abort its own GPU tile read 75 C while its VRAM read 96. No GPU tile
has ever exceeded 81 C on this box. Both counters are still watched on both cards -- but if you are
reading one number, read that one.

WHY A SLOPE RULE AND NOT JUST A LIMIT. 78 -> 96 in 48 s means the absolute limit fires with almost
no warning. The slope term (``>= SLOPE_RISE_C`` between consecutive readings while already above
``SLOPE_FLOOR_C``) fires on the SECOND reading of that exact series -- the 78 -> 88 step, at
06:40:08 against an abort at 06:40:44 -- which is **36 seconds** of warning where the absolute line
gives none. Replayed as a test, not asserted: ``test_the_slope_rule_fires_before_the_abort_line_on
_the_real_series``. (I first wrote "~24 s" here from eyeballing the table; the test said 36.)
It is a back-off, not a scoring term, and the absolute limit still catches a slow climb the slope
rule is designed to ignore.

BLINDNESS IS NOT SAFETY. If the stream carries no temperature rows for a card, or its rows have
gone stale, the verdict is ``blind`` -- never ``ok``. A watchdog that cannot see must stop the run,
because "no reading" and "cool" are the same value to a naive check and opposite facts to a card.
This is the ADR-0034-era lesson restated: a port probe passing is not a rung serving.

KILLING. By RECORDED PID, never by image name. ``restart-arc.cmd`` does ``taskkill /IM
llama-server.exe`` image-wide, which is why a hand-launched experiment server dies to any ArcServe
cycle from any lane -- and why a wedged one keeps production down. This module never does that. It
is handed the pids it started and terminates exactly those.

THRESHOLDS ARE IMPORTED, NOT REDECLARED. They come from ``sat_cell_runner`` so the live term and
the scored term can never drift apart. The abort line is Derek's call, made 2026-09-09: 95 C.

Timebase and row shape are the stream's own (see ``sat_cell_runner.thermal_summary``): ``t`` is
NANOSECONDS on the boot-relative perf_counter clock; identity rows are ``k=="ai"`` carrying the
durable PCI BDF; measurements are ``k=="ms"`` with ``n`` the counter and ``v`` the value. The
temperature counters are emitted ON CHANGE, not per tick, which is why staleness is measured
against a generous bound and reported rather than assumed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sat_cell_runner import (  # noqa: E402
    GPU_ABORT_C,
    GPU_WARN_C,
    THERMAL_COUNTERS,
    VRAM_ABORT_C,
    VRAM_WARN_C,
)

#: Back off when a counter rises this much between consecutive readings while already hot.
#: Sized on the real series: 04:00.0 VRAM ran 78 -> 88 -> 92 -> 94 -> 96 over five ~12 s ticks,
#: so a 6 C step above 85 C fires on the 78 -> 88 step -- the first reading that arrives above
#: the floor -- 36 s before that run reached 96.
SLOPE_RISE_C = 6.0

#: The slope rule is silent below this. A cold card gaining 10 C is a card waking up; the same
#: gain at 88 C is the run that ended at 96. Without this floor the term fires on every load start.
SLOPE_FLOOR_C = 85.0

#: After an abort, nothing restarts until EVERY watched counter on EVERY card is below this.
#: Inherited from the 2026-08-27 harness, which carried ``temperature_resume_below_c: 80``.
RESUME_BELOW_C = 80.0

#: A reading older than this is stale. The counters are emitted on change, and a 260 s capture
#: carried only 7-13 readings per counter, so this is deliberately loose -- it catches a DEAD
#: stream, not a quiet one. Staleness yields ``blind``, never ``ok``.
STALE_AFTER_S = 90.0

#: Poll cadence. The 2026-08-27 watchdog used 10 s and caught a 48 s excursion with 4 samples.
POLL_INTERVAL_S = 10.0

ABORT_C = {"gpu.temperature_c": GPU_ABORT_C, "vram.temperature_c": VRAM_ABORT_C}
WARN_C = {"gpu.temperature_c": GPU_WARN_C, "vram.temperature_c": VRAM_WARN_C}

NS = 1_000_000_000


# ------------------------------------------------------------------ parsing the stream --
def read_temperatures(events_path, since_offset: int = 0) -> tuple:
    """Parse temperature rows from a b70tools stream. Returns ``(readings, ident, offset)``.

    ``readings`` is ``{(adapter, counter): [(t_ns, celsius), ...]}`` in file order, ``ident`` is
    ``{adapter: {"desc", "bdf"}}``, and ``offset`` is where to resume next call so a long capture
    is not re-read from the top every poll.

    The iGPU is NOT filtered here -- filtering is the caller's, via ``ident``, so a test can see
    that the row was read and deliberately dropped rather than silently missed.
    """
    path = Path(events_path)
    readings: dict = {}
    ident: dict = {}
    if not path.is_file():
        return readings, ident, since_offset
    with path.open(encoding="utf-8-sig") as handle:
        handle.seek(since_offset)
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn last line during a live tail is normal, not an error
            kind = row.get("k")
            if kind == "ai":
                ident[row.get("a")] = {"desc": row.get("desc") or "", "bdf": row.get("bdf")}
                continue
            if kind != "ms" or row.get("n") not in THERMAL_COUNTERS:
                continue
            try:
                readings.setdefault((row.get("a"), row["n"]), []).append(
                    (int(row.get("t") or 0), float(row["v"])))
            except (KeyError, TypeError, ValueError):
                continue
        offset = handle.tell()
    return readings, ident, offset


class ThermalState:
    """Accumulates readings across polls and keeps the last two per card+counter.

    Two is all the slope term needs, and keeping only two means a four-hour capture costs the
    same memory as a four-minute one -- which matters, because the soak cell this exists to
    protect is the longest thing this campaign will have run.
    """

    def __init__(self) -> None:
        self.ident: dict = {}
        self.last: dict = {}   # (bdf, counter) -> (t_ns, celsius)
        self.prev: dict = {}   # (bdf, counter) -> (t_ns, celsius)
        self.peak: dict = {}   # (bdf, counter) -> celsius
        self.offset = 0

    def b70_adapters(self) -> dict:
        """Adapter -> BDF, B70s only. The iGPU is not part of the board's thermal picture."""
        return {a: (info.get("bdf") or a) for a, info in self.ident.items()
                if "B70" in (info.get("desc") or "")}

    def ingest(self, readings: dict, ident: dict) -> None:
        self.ident.update(ident or {})
        b70 = self.b70_adapters()
        for (adapter, counter), rows in (readings or {}).items():
            if adapter not in b70:
                continue
            key = (b70[adapter], counter)
            for t_ns, celsius in sorted(rows):
                if key in self.last and self.last[key][0] == t_ns:
                    continue
                self.prev[key] = self.last.get(key)
                self.last[key] = (t_ns, celsius)
                self.peak[key] = max(self.peak.get(key, celsius), celsius)

    def poll(self, events_path) -> None:
        readings, ident, self.offset = read_temperatures(events_path, self.offset)
        self.ingest(readings, ident)


# ------------------------------------------------------------------------- the verdict --
def evaluate(state: ThermalState, now_ns: int | None = None,
             stale_after_s: float = STALE_AFTER_S) -> dict:
    """``{"verdict", "reason", "breaches", "warnings", "readings", "hottest"}``.

    Verdicts, in the order they are checked:
      ``abort_absolute``  a counter reached its abort line. Derek's 95 C.
      ``abort_slope``     a counter rose >= SLOPE_RISE_C between consecutive readings while
                          already above SLOPE_FLOOR_C. Fires earlier than the absolute term on
                          the real 2026-08-27 series, which is the whole point.
      ``blind``           no B70 identified, or a watched counter has no reading, or every
                          reading is stale. NEVER folded into ``ok``.
      ``warn``            a counter is at or above its warn line. Annotates; does not stop.
      ``ok``              everything seen, everything cool.
    """
    b70 = state.b70_adapters()
    readings = {"%s/%s" % (bdf, counter): {"c": v, "t_ns": t,
                                           "peak_c": state.peak.get((bdf, counter))}
                for (bdf, counter), (t, v) in sorted(state.last.items())}

    if not b70:
        return {"verdict": "blind", "reason": "no B70 adapter identified in the stream",
                "breaches": [], "warnings": [], "readings": readings, "hottest": None}

    expected = [(bdf, counter) for bdf in sorted(set(b70.values()))
                for counter in THERMAL_COUNTERS]
    missing = [k for k in expected if k not in state.last]

    breaches: list = []
    warnings: list = []
    for key in expected:
        entry = state.last.get(key)
        if entry is None:
            continue
        bdf, counter = key
        _t, celsius = entry
        if celsius >= ABORT_C[counter]:
            breaches.append({"kind": "absolute", "bdf": bdf, "counter": counter,
                             "c": celsius, "limit": ABORT_C[counter]})
            continue
        previous = state.prev.get(key)
        if previous is not None:
            rise = celsius - previous[1]
            if celsius >= SLOPE_FLOOR_C and rise >= SLOPE_RISE_C:
                breaches.append({"kind": "slope", "bdf": bdf, "counter": counter,
                                 "c": celsius, "from_c": previous[1], "rise_c": round(rise, 1),
                                 "floor_c": SLOPE_FLOOR_C, "rise_limit_c": SLOPE_RISE_C})
                continue
        if celsius >= WARN_C[counter]:
            warnings.append({"bdf": bdf, "counter": counter, "c": celsius,
                             "warn_c": WARN_C[counter]})

    hottest = None
    if state.last:
        (bdf, counter), (_t, celsius) = max(state.last.items(), key=lambda kv: kv[1][1])
        hottest = {"bdf": bdf, "counter": counter, "c": celsius}

    if breaches:
        absolute = [b for b in breaches if b["kind"] == "absolute"]
        chosen = absolute or breaches
        first = chosen[0]
        if first["kind"] == "absolute":
            reason = ("%s %s reached %.1f C, abort line %.1f"
                      % (first["bdf"], first["counter"], first["c"], first["limit"]))
            verdict = "abort_absolute"
        else:
            reason = ("%s %s rose %.1f C to %.1f C while above %.0f C -- backing off before the "
                      "abort line" % (first["bdf"], first["counter"], first["rise_c"],
                                      first["c"], first["floor_c"]))
            verdict = "abort_slope"
        return {"verdict": verdict, "reason": reason, "breaches": breaches,
                "warnings": warnings, "readings": readings, "hottest": hottest}

    if missing:
        return {"verdict": "blind",
                "reason": "no reading for %s -- a counter that cannot be seen is not cool"
                          % ", ".join("%s/%s" % k for k in missing),
                "breaches": [], "warnings": warnings, "readings": readings, "hottest": hottest}

    if now_ns is not None:
        stale = [("%s/%s" % k, round((now_ns - t) / NS, 1))
                 for k, (t, _v) in state.last.items() if (now_ns - t) / NS > stale_after_s]
        if stale:
            return {"verdict": "blind",
                    "reason": "every reading for %s is older than %.0f s -- the stream is dead, "
                              "not quiet" % (", ".join(n for n, _a in sorted(stale)), stale_after_s),
                    "breaches": [], "warnings": warnings, "readings": readings,
                    "hottest": hottest}

    if warnings:
        first = warnings[0]
        return {"verdict": "warn",
                "reason": "%s %s at %.1f C, warn line %.1f" % (first["bdf"], first["counter"],
                                                               first["c"], first["warn_c"]),
                "breaches": [], "warnings": warnings, "readings": readings, "hottest": hottest}

    return {"verdict": "ok", "reason": "all watched counters below their warn lines",
            "breaches": [], "warnings": [], "readings": readings, "hottest": hottest}


def may_resume(state: ThermalState, resume_below_c: float = RESUME_BELOW_C) -> dict:
    """After a back-off, has EVERY watched counter on EVERY card fallen below the resume line?

    Blind is not permission: an unreadable counter blocks resume the same way a hot one does.
    """
    b70 = state.b70_adapters()
    if not b70:
        return {"ok": False, "reason": "no B70 adapter identified", "above": []}
    above: list = []
    for bdf in sorted(set(b70.values())):
        for counter in THERMAL_COUNTERS:
            entry = state.last.get((bdf, counter))
            if entry is None:
                return {"ok": False,
                        "reason": "no reading for %s/%s -- cannot clear a card we cannot see"
                                  % (bdf, counter), "above": above}
            if entry[1] >= resume_below_c:
                above.append({"bdf": bdf, "counter": counter, "c": entry[1]})
    if above:
        return {"ok": False,
                "reason": "still above %.0f C: %s" % (resume_below_c, ", ".join(
                    "%s/%s %.1f" % (a["bdf"], a["counter"], a["c"]) for a in above)),
                "above": above}
    return {"ok": True, "reason": "every watched counter below %.0f C" % resume_below_c,
            "above": []}


# ------------------------------------------------------------------------ the actuator --
def terminate_pids(pids, killer=None) -> list:
    """Terminate EXACTLY these pids. Never an image name, never a command-line match.

    ``restart-arc.cmd`` kills ``llama-server.exe`` image-wide and that is precisely the behaviour
    this module refuses to imitate: a name match on this box once killed three production
    services. The caller records the pids it started; those are the only pids that die here.
    """
    results: list = []
    for pid in pids or ():
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            results.append({"pid": pid, "ok": False, "error": "not an integer pid"})
            continue
        try:
            if killer is not None:
                killer(pid)
            else:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               check=True, capture_output=True)
            results.append({"pid": pid, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"pid": pid, "ok": False, "error": str(exc)})
    return results


def watch(events_path, pids, *, poll_interval_s: float = POLL_INTERVAL_S,
          max_seconds: float | None = None, on_sample=None, killer=None,
          sleeper=None, clock=None) -> dict:
    """Poll the stream, and terminate ``pids`` the moment a breach or blindness is seen.

    Returns the terminal verdict with the full reading history that justified it. ``blind`` stops
    the run exactly like a breach does -- see the module docstring.
    """
    sleeper = sleeper or time.sleep
    clock = clock or time.perf_counter_ns
    state = ThermalState()
    started = clock()
    samples: list = []
    while True:
        state.poll(events_path)
        verdict = evaluate(state, now_ns=clock())
        samples.append({"at_ns": clock(), "verdict": verdict["verdict"],
                        "reason": verdict["reason"], "hottest": verdict["hottest"]})
        if on_sample is not None:
            on_sample(verdict)
        if verdict["verdict"] in ("abort_absolute", "abort_slope", "blind"):
            killed = terminate_pids(pids, killer=killer)
            return {"stopped": True, "verdict": verdict["verdict"], "reason": verdict["reason"],
                    "breaches": verdict["breaches"], "readings": verdict["readings"],
                    "killed": killed, "samples": samples,
                    "resume_line_c": RESUME_BELOW_C}
        if max_seconds is not None and (clock() - started) / NS >= max_seconds:
            return {"stopped": False, "verdict": verdict["verdict"], "reason": verdict["reason"],
                    "breaches": [], "readings": verdict["readings"], "killed": [],
                    "samples": samples, "resume_line_c": RESUME_BELOW_C}
        sleeper(poll_interval_s)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Live thermal watchdog for replica-per-card cells")
    ap.add_argument("--events", required=True, help="b70tools events.jsonl being written live")
    ap.add_argument("--pid", action="append", default=[], type=int,
                    help="a pid to terminate on breach; repeatable. Recorded pids ONLY.")
    ap.add_argument("--poll-s", type=float, default=POLL_INTERVAL_S)
    ap.add_argument("--max-s", type=float, default=None)
    ap.add_argument("--once", action="store_true", help="evaluate once and print, kill nothing")
    args = ap.parse_args()

    if args.once:
        state = ThermalState()
        state.poll(args.events)
        print(json.dumps(evaluate(state, now_ns=time.perf_counter_ns()), indent=1))
        return 0

    if not args.pid:
        print("refusing to watch with no pids to stop: a watchdog that cannot act is a logger",
              file=sys.stderr)
        return 2
    result = watch(args.events, args.pid, poll_interval_s=args.poll_s, max_seconds=args.max_s)
    print(json.dumps(result, indent=1))
    return 1 if result["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
