r"""The watchdog must stop a hot run, and must never call blindness safety.

The decisive test is ``test_the_slope_rule_fires_before_the_abort_line_on_the_real_series``: it
replays the ACTUAL 2026-08-27 replica-per-card excursion (04:00.0 VRAM 78 -> 88 -> 92 -> 94 -> 96)
and asserts we back off at the 88 -> 94 step rather than at 96. That is the ~24 seconds of warning
the current post-hoc gate 8 does not provide, on the exact series that produced the only 96 C
readings this box has ever recorded.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thermal_watchdog as tw  # noqa: E402

B70 = "Intel(R) Arc(TM) Pro B70 Graphics"
A04, A09 = "adapter_00016a2f", "adapter_000171de"
BDF04, BDF09 = "0000:04:00.0", "0000:09:00.0"
IGPU = "adapter_igpu"
NS = tw.NS


def stream(path: Path, rows, *, with_igpu: bool = False) -> Path:
    """A b70tools-shaped events.jsonl. ``rows`` are (adapter, counter, seconds, celsius)."""
    lines = [
        json.dumps({"k": "ai", "a": A04, "desc": B70, "bdf": BDF04}),
        json.dumps({"k": "ai", "a": A09, "desc": B70, "bdf": BDF09}),
    ]
    if with_igpu:
        lines.append(json.dumps({"k": "ai", "a": IGPU, "desc": "Intel(R) Graphics",
                                 "bdf": "0000:00:02.0"}))
    for adapter, counter, secs, celsius in rows:
        lines.append(json.dumps({"k": "ms", "a": adapter, "n": counter,
                                 "v": celsius, "t": int(secs * NS)}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cool(t: float = 1.0):
    """Both cards, both counters, comfortably cool -- the backdrop for a targeted breach."""
    return [(A04, "gpu.temperature_c", t, 60.0), (A04, "vram.temperature_c", t, 62.0),
            (A09, "gpu.temperature_c", t, 58.0), (A09, "vram.temperature_c", t, 60.0)]


def append(path: Path, rows) -> None:
    """Append measurement rows the way a live b70tools tail grows the file."""
    with path.open("a", encoding="utf-8") as handle:
        for adapter, counter, secs, celsius in rows:
            handle.write(json.dumps({"k": "ms", "a": adapter, "n": counter,
                                     "v": celsius, "t": int(secs * NS)}) + "\n")


def replay(tmp: Path, series, *, counter: str = "vram.temperature_c", adapter: str = A04,
           baseline_c: float | None = None):
    """Feed ``series`` one reading at a time to a live-tailing state, as the watchdog sees it.

    Returns ``(state, verdicts)``. The first value of ``series`` IS the first reading for that
    counter unless ``baseline_c`` is given, so the slope term compares real consecutive samples
    rather than a jump from an unrelated cool backdrop.
    """
    path = Path(tmp) / "events.jsonl"
    backdrop = [r for r in cool(0.5) if not (r[0] == adapter and r[1] == counter)]
    if baseline_c is not None:
        backdrop.append((adapter, counter, 0.5, baseline_c))
    stream(path, backdrop)
    state = tw.ThermalState()
    state.poll(path)
    verdicts = []
    for i, celsius in enumerate(series):
        append(path, [(adapter, counter, 1.0 + i, celsius)])
        state.poll(path)
        verdicts.append(tw.evaluate(state))
    return state, verdicts


def verdict_for(rows, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        state = tw.ThermalState()
        state.poll(stream(Path(tmp) / "events.jsonl", rows, **kw))
        return state, tw.evaluate(state)


class AbsoluteLimitTests(unittest.TestCase):
    def test_vram_at_the_abort_line_stops_the_run(self):
        _s, v = verdict_for(cool() + [(A04, "vram.temperature_c", 2.0, 95.0)])
        self.assertEqual(v["verdict"], "abort_absolute")
        self.assertIn(BDF04, v["reason"])
        self.assertIn("vram", v["reason"])

    def test_gpu_at_the_abort_line_also_stops_the_run(self):
        _s, v = verdict_for(cool() + [(A09, "gpu.temperature_c", 2.0, 96.0)])
        self.assertEqual(v["verdict"], "abort_absolute")
        self.assertEqual(v["breaches"][0]["bdf"], BDF09)

    def test_the_limit_is_the_scored_gates_constant_not_a_second_copy(self):
        # Two thresholds in two files is exactly the drift this lab keeps catching.
        import sat_cell_runner as runner
        self.assertEqual(tw.ABORT_C["vram.temperature_c"], runner.VRAM_ABORT_C)
        self.assertEqual(tw.ABORT_C["gpu.temperature_c"], runner.GPU_ABORT_C)
        self.assertEqual(tw.WARN_C["vram.temperature_c"], runner.VRAM_WARN_C)

    def test_below_the_warn_line_is_ok(self):
        _s, v = verdict_for(cool())
        self.assertEqual(v["verdict"], "ok")

    def test_the_warn_line_annotates_but_does_not_stop(self):
        # A card sitting AT the warn line, arrived at gently -- not a jump from a cool backdrop,
        # which would be a slope breach and rightly so.
        with tempfile.TemporaryDirectory() as tmp:
            _s, verdicts = replay(Path(tmp), [88.0], baseline_c=87.0)
        self.assertEqual(verdicts[-1]["verdict"], "warn")
        self.assertEqual(verdicts[-1]["warnings"][0]["c"], 88.0)


class SlopeRuleTests(unittest.TestCase):
    def test_the_slope_rule_fires_before_the_abort_line_on_the_real_series(self):
        # THE test. 2026-08-27, 0000:04:00.0 VRAM, ~12 s between readings, the excursion that
        # ended the replica experiment. Readings at 06:39:56 / :40:08 / :20 / :32 / :44.
        series = [78.0, 88.0, 92.0, 94.0, 96.0]
        with tempfile.TemporaryDirectory() as tmp:
            _s, verdicts = replay(Path(tmp), series)
        fired = [(i, series[i], v["verdict"]) for i, v in enumerate(verdicts)
                 if v["verdict"].startswith("abort")]
        self.assertTrue(fired, "the watchdog never fired on the series that hit 96 C")
        index, celsius, kind = fired[0]
        self.assertEqual(kind, "abort_slope")
        # 78 -> 88 is a 10 C rise arriving already above the 85 C floor, so it fires on the
        # SECOND reading -- 06:40:08, which is 36 s before the 96 C the real run reached.
        self.assertEqual(celsius, 88.0)
        self.assertEqual(index, 1)
        # And it fires strictly before the absolute term would have.
        self.assertTrue(all(v["verdict"] != "abort_absolute" for v in verdicts[:index + 1]))

    def test_the_absolute_term_still_catches_a_slow_climb_the_slope_rule_misses(self):
        # 2 C per reading never trips the slope rule, so the limit must still be there.
        with tempfile.TemporaryDirectory() as tmp:
            _s, verdicts = replay(Path(tmp), [88.0, 90.0, 92.0, 94.0, 95.0], baseline_c=87.0)
        kinds = [v["verdict"] for v in verdicts]
        self.assertNotIn("abort_slope", kinds)
        self.assertEqual(kinds[-1], "abort_absolute")

    def test_a_big_rise_while_cold_does_not_fire(self):
        # A card waking up gains 15 C and that is not an emergency. Without the floor this
        # term would fire on every single load start.
        with tempfile.TemporaryDirectory() as tmp:
            _s, verdicts = replay(Path(tmp), [70.0], baseline_c=55.0)
        self.assertEqual(verdicts[-1]["verdict"], "ok")

    def test_a_gentle_rise_while_hot_only_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, verdicts = replay(Path(tmp), [88.0], baseline_c=86.0)
        self.assertEqual(verdicts[-1]["verdict"], "warn")


class BlindnessTests(unittest.TestCase):
    """Blindness is not safety. Every one of these must refuse to say ok."""

    def test_a_stream_with_no_temperature_rows_is_blind_not_ok(self):
        _s, v = verdict_for([])
        self.assertEqual(v["verdict"], "blind")

    def test_a_missing_counter_on_one_card_is_blind(self):
        rows = [r for r in cool() if not (r[0] == A04 and r[1] == "vram.temperature_c")]
        _s, v = verdict_for(rows)
        self.assertEqual(v["verdict"], "blind")
        self.assertIn("04:00.0/vram.temperature_c", v["reason"])

    def test_stale_readings_are_blind(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = tw.ThermalState()
            state.poll(stream(Path(tmp) / "events.jsonl", cool(1.0)))
            v = tw.evaluate(state, now_ns=int(500 * NS))
        self.assertEqual(v["verdict"], "blind")
        self.assertIn("dead", v["reason"])

    def test_a_missing_file_is_blind(self):
        state = tw.ThermalState()
        state.poll(Path(tempfile.gettempdir()) / "definitely-not-a-stream-8471.jsonl")
        self.assertEqual(tw.evaluate(state)["verdict"], "blind")

    def test_blindness_stops_the_run_exactly_like_a_breach(self):
        killed = []
        with tempfile.TemporaryDirectory() as tmp:
            path = stream(Path(tmp) / "events.jsonl", [])
            out = tw.watch(path, [4242], killer=killed.append, sleeper=lambda _s: None,
                           max_seconds=1.0)
        self.assertTrue(out["stopped"])
        self.assertEqual(out["verdict"], "blind")
        self.assertEqual(killed, [4242])


class AdapterSelectionTests(unittest.TestCase):
    def test_the_igpu_is_not_part_of_the_thermal_picture(self):
        rows = cool() + [(IGPU, "gpu.temperature_c", 2.0, 99.0)]
        state, v = verdict_for(rows, with_igpu=True)
        self.assertEqual(v["verdict"], "ok")
        self.assertNotIn("0000:00:02.0", json.dumps(v["readings"]))

    def test_readings_are_keyed_by_durable_bdf_never_the_adapter_id(self):
        # ADR-0042: the b70tools adapter id is session-scoped; the BDF is the identity.
        _s, v = verdict_for(cool())
        self.assertIn("%s/vram.temperature_c" % BDF04, v["readings"])
        self.assertNotIn(A04, json.dumps(v["readings"]))

    def test_the_peak_survives_a_later_cooler_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            state = tw.ThermalState()
            for i, celsius in enumerate((70.0, 91.0, 72.0)):
                stream(path, cool(0.5) + [(A04, "vram.temperature_c", 1.0 + i, celsius)])
                state.offset = 0
                state.poll(path)
            self.assertEqual(state.peak[(BDF04, "vram.temperature_c")], 91.0)


class ResumeLineTests(unittest.TestCase):
    def test_resume_is_refused_while_any_counter_is_above_the_line(self):
        state, _v = verdict_for(cool() + [(A04, "vram.temperature_c", 2.0, 84.0)])
        out = tw.may_resume(state)
        self.assertFalse(out["ok"])
        self.assertEqual(out["above"][0]["c"], 84.0)

    def test_resume_is_granted_when_everything_is_below_the_line(self):
        state, _v = verdict_for(cool())
        self.assertTrue(tw.may_resume(state)["ok"])

    def test_an_unreadable_counter_blocks_resume(self):
        rows = [r for r in cool() if not (r[0] == A09 and r[1] == "gpu.temperature_c")]
        state, _v = verdict_for(rows)
        out = tw.may_resume(state)
        self.assertFalse(out["ok"])
        self.assertIn("cannot clear a card we cannot see", out["reason"])


class TerminationTests(unittest.TestCase):
    def test_only_the_recorded_pids_are_killed(self):
        killed = []
        out = tw.terminate_pids([101, 202], killer=killed.append)
        self.assertEqual(killed, [101, 202])
        self.assertTrue(all(r["ok"] for r in out))

    def test_a_failing_kill_is_reported_not_swallowed(self):
        def boom(_pid):
            raise OSError("access denied")
        out = tw.terminate_pids([303], killer=boom)
        self.assertFalse(out[0]["ok"])
        self.assertIn("access denied", out[0]["error"])

    def test_it_never_shells_out_to_an_image_name_match(self):
        # taskkill /IM killed three production services on this box once, and restart-arc.cmd
        # still does it image-wide. The ARGUMENT form must never appear here -- the prose may
        # name it, which is why this checks the quoted argument and not the substring.
        source = Path(tw.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"/IM"', source)
        self.assertNotIn("'/IM'", source)
        self.assertIn('"/PID"', source)

    def test_a_breach_kills_and_reports_the_reason(self):
        killed = []
        with tempfile.TemporaryDirectory() as tmp:
            path = stream(Path(tmp) / "events.jsonl",
                          cool() + [(A04, "vram.temperature_c", 2.0, 97.0)])
            out = tw.watch(path, [777], killer=killed.append, sleeper=lambda _s: None)
        self.assertTrue(out["stopped"])
        self.assertEqual(out["verdict"], "abort_absolute")
        self.assertEqual(killed, [777])
        self.assertEqual(out["resume_line_c"], tw.RESUME_BELOW_C)

    def test_a_cool_run_reaches_its_deadline_without_killing_anything(self):
        # The clock is injected so the fixture's stream timestamps and "now" share an origin;
        # with the real perf_counter the readings would look hours stale and go blind, which
        # is itself the correct behaviour and is covered by test_stale_readings_are_blind.
        killed = []
        with tempfile.TemporaryDirectory() as tmp:
            path = stream(Path(tmp) / "events.jsonl", cool(1.0))
            out = tw.watch(path, [888], killer=killed.append, sleeper=lambda _s: None,
                           max_seconds=0.0, clock=lambda: int(2 * NS))
        self.assertFalse(out["stopped"])
        self.assertEqual(out["verdict"], "ok")
        self.assertEqual(killed, [])

    def test_a_breach_is_reported_with_the_reading_that_caused_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = stream(Path(tmp) / "events.jsonl",
                          cool(1.0) + [(A04, "vram.temperature_c", 2.0, 97.0)])
            out = tw.watch(path, [], killer=lambda _p: None, sleeper=lambda _s: None,
                           clock=lambda: int(3 * NS))
        self.assertEqual(out["breaches"][0]["bdf"], BDF04)
        self.assertEqual(out["breaches"][0]["c"], 97.0)
        self.assertIn("%s/vram.temperature_c" % BDF04, out["readings"])


class IncrementalReadTests(unittest.TestCase):
    def test_a_growing_stream_is_not_re_read_from_the_top(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream(path, cool(1.0))
            state = tw.ThermalState()
            state.poll(path)
            first = state.offset
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"k": "ms", "a": A04, "n": "vram.temperature_c",
                                         "v": 70.0, "t": int(2 * NS)}) + "\n")
            state.poll(path)
            self.assertGreater(state.offset, first)
            self.assertEqual(state.last[(BDF04, "vram.temperature_c")][1], 70.0)
            self.assertEqual(state.prev[(BDF04, "vram.temperature_c")][1], 62.0)

    def test_a_torn_last_line_during_a_live_tail_is_survivable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream(path, cool(1.0))
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"k": "ms", "a": "adapter_00016a2f", "n": "vram.temp')
            state = tw.ThermalState()
            state.poll(path)
            self.assertEqual(tw.evaluate(state)["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
