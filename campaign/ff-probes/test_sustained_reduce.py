r"""A multi-hour reduction must show the SHAPE, and must never invent one.

The two tests that matter most:
  * ``test_an_empty_temperature_bucket_is_a_loud_null`` -- the counters emit ON CHANGE, so a
    settled temperature emits nothing. Forward-filling silence would fabricate exactly the
    plateau this capture exists to detect.
  * ``test_the_real_2026_08_27_climb_never_settles`` -- replays the excursion that ended the
    replica experiment and asserts the knee refuses to call a plateau on it. A tool that found
    a plateau in a monotone climb would be worse than no tool.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sustained_reduce as sr  # noqa: E402

B70 = "Intel(R) Arc(TM) Pro B70 Graphics"
A04, A09 = "adapter_00016def", "adapter_000171de"
BDF04, BDF09 = "0000:04:00.0", "0000:09:00.0"
NS = sr.NS


def stream(path: Path, energy=(), temps=(), with_igpu: bool = False) -> Path:
    """``energy`` = (adapter, seconds, joules); ``temps`` = (adapter, counter, seconds, celsius)."""
    lines = [json.dumps({"k": "ai", "a": A04, "desc": B70, "bdf": BDF04}),
             json.dumps({"k": "ai", "a": A09, "desc": B70, "bdf": BDF09})]
    if with_igpu:
        lines.append(json.dumps({"k": "ai", "a": "adapter_igpu", "desc": "Intel(R) Graphics",
                                 "bdf": "0000:00:02.0"}))
    rows = []
    for adapter, secs, joules in energy:
        rows.append((secs, json.dumps({"k": "ms", "a": adapter, "n": "gpu.energy_j_counter",
                                       "v": joules, "t": int(secs * NS)})))
    for adapter, counter, secs, celsius in temps:
        rows.append((secs, json.dumps({"k": "ms", "a": adapter, "n": counter,
                                       "v": celsius, "t": int(secs * NS)})))
    lines += [line for _s, line in sorted(rows, key=lambda r: r[0])]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ramp(adapter, seconds, watts_per_s, start_j=0.0):
    """A constant-power energy series: joules accumulate at ``watts_per_s`` per second."""
    return [(adapter, float(s), start_j + watts_per_s * s) for s in range(seconds + 1)]


def reduce_stream_file(energy=(), temps=(), bucket_s=60.0, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        path = stream(Path(tmp) / "events.jsonl", energy, temps, **kw)
        return sr.bucketize(path, bucket_s)


class BucketingTests(unittest.TestCase):
    def test_a_constant_load_reduces_to_its_watts_in_every_bucket(self):
        out = reduce_stream_file(energy=ramp(A09, 180, 100.0) + ramp(A04, 180, 25.0),
                                 bucket_s=60.0)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["buckets_total"], 3)
        for bucket in out["buckets"][:3]:
            self.assertAlmostEqual(bucket["cards"][BDF09]["power"]["p50_w"], 100.0, places=1)
            self.assertAlmostEqual(bucket["cards"][BDF04]["power"]["p50_w"], 25.0, places=1)

    def test_cards_are_keyed_by_durable_bdf_not_the_session_scoped_adapter_id(self):
        out = reduce_stream_file(energy=ramp(A09, 120, 80.0))
        self.assertIn(BDF09, out["cards"])
        self.assertNotIn(A09, json.dumps(out["cards"]))

    def test_the_igpu_is_not_in_the_reduction(self):
        out = reduce_stream_file(energy=ramp(A09, 120, 80.0), with_igpu=True)
        self.assertNotIn("0000:00:02.0", out["cards"])

    def test_a_rising_then_falling_load_shows_the_shape_not_one_number(self):
        # The whole point: a capture-wide p50 would hide this.
        energy = ramp(A09, 60, 30.0)
        base = energy[-1][2]
        energy += [(A09, 60.0 + s, base + 120.0 * s) for s in range(1, 61)]
        out = reduce_stream_file(energy=energy, bucket_s=60.0)
        first = out["buckets"][0]["cards"][BDF09]["power"]["p50_w"]
        second = out["buckets"][1]["cards"][BDF09]["power"]["p50_w"]
        self.assertLess(first, 50.0)
        self.assertGreater(second, 100.0)

    def test_an_interval_straddling_a_bucket_edge_is_counted_in_neither_and_reported(self):
        # Same rule reduce_stream uses for its window edges. Splitting would fabricate samples.
        # Buckets are relative to the FIRST sample, so the stream needs a t=0 sample to set the
        # origin before an interval can cross a boundary at t=60.
        energy = [(A09, 0.0, 0.0), (A09, 59.5, 5950.0), (A09, 60.5, 6050.0)]
        out = reduce_stream_file(energy=energy, bucket_s=60.0)
        self.assertEqual(out["cards"][BDF09]["straddling_intervals"], 1)
        # The first interval (0 -> 59.5) is wholly inside bucket 0 and IS counted.
        self.assertEqual(out["cards"][BDF09]["power"]["intervals"], 1)

    def test_an_irregular_interval_is_counted_so_a_stalled_collector_is_visible(self):
        # A 5 s gap is a collector problem, not quiet hardware, and must not pass silently.
        energy = [(A09, 0.0, 0.0), (A09, 5.0, 500.0), (A09, 6.0, 600.0)]
        out = reduce_stream_file(energy=energy, bucket_s=60.0)
        self.assertEqual(out["cards"][BDF09]["irregular_intervals"], 1)

    def test_an_empty_stream_fails_loudly_rather_than_returning_zeros(self):
        # The reason must NAME the missing counter and what the stream did carry, not flatten
        # to "no data" -- that message is how a wrong --counter gets diagnosed in one read.
        out = reduce_stream_file()
        self.assertFalse(out["ok"])
        self.assertIn("gpu.energy_j_counter", out["reason"])
        self.assertIn("present:", out["reason"])
        self.assertEqual(out["buckets"], [])

    def test_a_stream_with_temperatures_but_no_energy_still_fails_loudly(self):
        # Silence on the power counter must not read as a cool, quiet card.
        out = reduce_stream_file(temps=[(A09, "vram.temperature_c", 5.0, 70.0)])
        self.assertFalse(out["ok"])
        self.assertIn("gpu.energy_j_counter", out["reason"])


class TemperatureCadenceTests(unittest.TestCase):
    def test_an_empty_temperature_bucket_is_a_loud_null(self):
        # THE test. Counters emit ON CHANGE; a settled temperature emits nothing. Silence must
        # never be rendered as a value, a zero, or the previous reading carried forward.
        temps = [(A09, "vram.temperature_c", 5.0, 70.0)]
        out = reduce_stream_file(energy=ramp(A09, 180, 90.0), temps=temps, bucket_s=60.0)
        first = out["buckets"][0]["cards"][BDF09]["vram.temperature_c"]
        second = out["buckets"][1]["cards"][BDF09]["vram.temperature_c"]
        self.assertEqual(first["p50_c"], 70.0)
        self.assertIsNone(second["p50_c"])
        self.assertEqual(second["readings"], 0)
        self.assertIn("silence, not a value", second["reason"])

    def test_temperature_stats_are_per_bucket_not_one_capture_wide_percentile(self):
        temps = [(A09, "vram.temperature_c", 10.0, 60.0),
                 (A09, "vram.temperature_c", 70.0, 90.0)]
        out = reduce_stream_file(energy=ramp(A09, 180, 90.0), temps=temps, bucket_s=60.0)
        self.assertEqual(out["buckets"][0]["cards"][BDF09]["vram.temperature_c"]["max_c"], 60.0)
        self.assertEqual(out["buckets"][1]["cards"][BDF09]["vram.temperature_c"]["max_c"], 90.0)

    def test_the_summary_carries_first_and_last_so_drift_is_visible(self):
        temps = [(A09, "vram.temperature_c", 5.0, 55.0),
                 (A09, "vram.temperature_c", 150.0, 78.0)]
        out = reduce_stream_file(energy=ramp(A09, 180, 90.0), temps=temps)
        summary = out["cards"][BDF09]["vram.temperature_c"]
        self.assertEqual(summary["first_c"], 55.0)
        self.assertEqual(summary["last_c"], 78.0)


class KneeTests(unittest.TestCase):
    def _reduced(self, celsius_by_minute):
        temps = [(A09, "vram.temperature_c", 5.0 + 60 * i, c)
                 for i, c in enumerate(celsius_by_minute)]
        seconds = 60 * len(celsius_by_minute) + 30
        return reduce_stream_file(energy=ramp(A09, seconds, 90.0), temps=temps, bucket_s=60.0)

    def test_it_refuses_a_plateau_call_from_too_few_buckets(self):
        out = sr.knee(self._reduced([60.0, 65.0]), BDF09)
        self.assertIsNone(out["plateau"])
        self.assertIn("need >", out["reason"])

    def test_it_finds_the_plateau_in_a_settling_series(self):
        # Climbs, then holds. The knee is where it stops moving.
        out = sr.knee(self._reduced([60, 70, 78, 82, 83, 83, 84, 83, 84]), BDF09,
                      plateau_within_c=2.0, need_buckets=4)
        self.assertIsNotNone(out["plateau"])
        self.assertGreaterEqual(out["plateau"]["c"], 82)
        self.assertEqual(out["peak_c"], 84)

    def test_the_real_2026_08_27_climb_never_settles(self):
        # The excursion that ended the replica experiment: 78 -> 96 with no plateau. A tool that
        # found one here would be worse than no tool.
        out = sr.knee(self._reduced([78, 84, 88, 90, 92, 94, 96]), BDF09,
                      plateau_within_c=2.0, need_buckets=4)
        self.assertIsNone(out["plateau"])
        self.assertIn("never settled", out["reason"])
        self.assertEqual(out["peak_c"], 96)

    def test_silence_is_not_stability(self):
        # Buckets with no reading are SKIPPED, never counted as "unchanged". Otherwise a dead
        # sensor would read as a perfect plateau.
        reduced = self._reduced([70.0])
        out = sr.knee(reduced, BDF09, need_buckets=4)
        self.assertIsNone(out["plateau"])
        self.assertEqual(out["readings"], 1)


class AtomicWriteTests(unittest.TestCase):
    def test_a_partial_flush_never_truncates_a_good_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "summary.json"
            sr.write_atomic(target, '{"a": 1}')
            sr.write_atomic(target, '{"a": 2}')
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["a"], 2)
            self.assertFalse(target.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
