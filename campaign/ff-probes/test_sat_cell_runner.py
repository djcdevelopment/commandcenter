r"""Fixtures for the SAT-L1 cell runner. No GPU, no llama-server, no live rung.

Every case here is a place where the runner could otherwise produce a plausible number
instead of an error, or a receipt that looks complete because a field went missing rather
than null:

  * warm-to-flatness must detect flatness from the SPREAD, keep every iteration, and hand
    back the FIRST iteration's FIRST rep as the unwarmed rep-1 (P7 is scored against it);
  * the omen.yaml ``-np`` edit must be token-exact and byte-reversible -- a stray second
    match would leave production on a value nobody chose;
  * a receipt must carry every field or an explicit null;
  * ``/slots`` fractions must be over OK polls only, and ``None`` (not 0.0) with none;
  * duty cycle must be time-weighted, windowed, and read each card's OWN frozen reference
    p50 out of the receipt -- never a hardcoded 159.92/114.05;
  * ``ArcServeRestart`` is STOP-ONLY. ``set_np`` assumed it restarted, and on 2026-09-09 that
    took production down for real: yaml edited, task fired, ``wait_for_ready`` timed out,
    nothing listening on 8081 or 8082. The sequence must be stop -> OBSERVE the stop ->
    ``ArcServeBoot``, it must abort before booting into a maintenance lock taken while we
    were stopping, and a marker timeout must SAY production may be down and name the
    recovery command;
  * gate 8's thermal reduction must report the absolute AND the rise -- absolute readings
    track ambient here -- and an absent counter must be a loud null, never an implied "cool".

Run: fleet-worker-node\.venv-omen\Scripts\python.exe -m pytest campaign/ff-probes/test_sat_cell_runner.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sys
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sat_cell_runner as runner  # noqa: E402

NS = runner.NS


def measurement(reps, ok=True, error=None):
    if not ok:
        return {"ok": False, "error": error or "warmup failed"}
    spread = (max(reps) - min(reps)) / (sum(reps) / len(reps)) * 100 if len(reps) > 1 else None
    return {"ok": True, "decode_tok_s": round(sum(reps) / len(reps), 2),
            "decode_reps": list(reps), "prefill_tok_s": 1500.0,
            "repeat_spread_pct": round(spread, 2) if spread is not None else None}


def scripted(*sequences):
    """A ``measure()`` that returns each fixture in turn."""
    items = iter(sequences)
    return lambda: next(items)


# ------------------------------------------------------------------ cell identity --
class TestCellIdentity(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(runner.parse_cell_id("np2-p512-c2-r1"),
                         {"np": 2, "depth": 512, "n": 2, "rep": 1})
        self.assertEqual(runner.cell_id(8, 32768, 24, 5), "np8-p32768-c24-r5")

    def test_a_malformed_cell_is_refused(self):
        for bad in ("", "np2-p512-c2", "cell-1", "np2_p512_c2_r1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                runner.parse_cell_id(bad)

    def test_admissibility_follows_per_slot_context(self):
        # P8: -c 131072 fixed, so per-slot context is 64K / 32K / 16K at -np 2 / 4 / 8.
        self.assertTrue(runner.admissible(32768, 2))
        self.assertTrue(runner.admissible(32768, 4))
        self.assertFalse(runner.admissible(32768, 8))
        self.assertTrue(runner.admissible(8192, 8))

    def test_repeats_are_five_where_the_card_says_five(self):
        self.assertEqual(runner.repeats_for(2, 512, 2), 5)    # noise floor
        self.assertEqual(runner.repeats_for(2, 8192, 4), 5)   # P5
        self.assertEqual(runner.repeats_for(8, 512, 4), 5)    # P6
        self.assertEqual(runner.repeats_for(2, 512, 1), 3)

    def test_sweep_order_is_depth_blocks_outermost_n_ascending(self):
        cells = runner.plan_cells(1)
        parsed = [runner.parse_cell_id(c) for c in cells]
        depth_order = [p["depth"] for p in parsed]
        self.assertEqual(depth_order, sorted(depth_order))  # depth blocks outermost
        block = [p for p in parsed if p["depth"] == 512]
        self.assertEqual([p["n"] for p in block], sorted(p["n"] for p in block))

    def test_phase2_drops_inadmissible_depths(self):
        cells = [runner.parse_cell_id(c) for c in runner.plan_cells(2)]
        self.assertTrue(any(c["np"] == 8 for c in cells))
        self.assertFalse(any(c["np"] == 8 and c["depth"] == 32768 for c in cells))
        self.assertTrue(any(c["np"] == 4 and c["depth"] == 32768 for c in cells))

    def test_depth0_cells_are_n2_and_n16_first_repeat_only(self):
        self.assertTrue(runner.is_depth0_cell(2, 1))
        self.assertTrue(runner.is_depth0_cell(16, 1))
        self.assertFalse(runner.is_depth0_cell(2, 2))
        self.assertFalse(runner.is_depth0_cell(8, 1))


# ------------------------------------------------------- gate 1: warm to flatness --
class TestWarmToFlatness(unittest.TestCase):
    def test_stops_at_the_first_flat_iteration_and_keeps_every_one(self):
        result = runner.warm_to_flatness(scripted(
            measurement([27.5, 62.0, 88.0]),    # ADR-0043 idle collapse, climbing
            measurement([98.0, 104.0, 106.0]),  # still 7.6% apart
            measurement([105.0, 105.6, 106.4]), # 1.32% -- flat
            measurement([106.0, 106.0, 106.0]), # never reached
        ))
        self.assertTrue(result["flat"])
        self.assertEqual(result["iterations_used"], 3)
        self.assertEqual(len(result["iterations"]), 3)
        self.assertLessEqual(result["final_spread_pct"], runner.FLATNESS_SPREAD_PCT)

    def test_the_unwarmed_rep1_is_the_first_iterations_first_rep(self):
        # P7 predicts an unwarmed rep-1 at 65-90% of warm; discarding it discards P7.
        result = runner.warm_to_flatness(scripted(
            measurement([72.0, 95.0, 104.0]),
            measurement([105.0, 105.5, 106.0]),
        ))
        self.assertEqual(result["unwarmed_rep1_tok_s"], 72.0)
        self.assertAlmostEqual(result["unwarmed_rep1_tok_s"] / result["final_decode_tok_s"],
                               0.681, places=2)

    def test_exactly_two_percent_counts_as_flat(self):
        flat = runner.warm_to_flatness(scripted(measurement([99.0, 100.0, 101.0])))
        self.assertEqual(flat["final_spread_pct"], 2.0)
        self.assertTrue(flat["flat"])

    def test_a_rung_that_never_settles_fails_within_the_cap(self):
        noisy = [measurement([60.0, 90.0, 120.0]) for _ in range(20)]
        result = runner.warm_to_flatness(scripted(*noisy), max_iterations=4)
        self.assertFalse(result["flat"])
        self.assertEqual(result["iterations_used"], 4)
        self.assertEqual(result["max_iterations"], 4)

    def test_a_failed_measurement_stops_the_loop_and_is_recorded(self):
        result = runner.warm_to_flatness(scripted(
            measurement([90.0, 100.0, 110.0]),
            measurement(None, ok=False, error="warmup HTTP 503 (still loading)"),
            measurement([105.0, 105.5, 106.0]),
        ))
        self.assertFalse(result["flat"])
        self.assertIn("503", result["error"])
        self.assertEqual(result["iterations_used"], 2)


# ---------------------------------------------------- Phase 2: the omen.yaml edit --
YAML_FIXTURE = (
    "startPort: 18300\n"
    "globalTTL: 0\n"
    "\n"
    "models:\n"
    "  # ---- PRODUCTION ----\n"
    '  "qwen3-30b-a3b":\n'
    "    cmd: >\n"
    "      E:\\work\\llamacpp-knee\\build\\bin\\llama-server.exe\n"
    "      -ngl 99 -sm layer -ts 1,1\n"
    "      -c 131072 -np 2 -ub 1024\n"
    "      --host 127.0.0.1 --port 8082\n"
    "    proxy: http://127.0.0.1:8082\n"
    "    ttl: 0\n"
    "\n"
    '  "phi4-vk1":\n'
    "    cmd: >\n"
    "      E:\\work\\llamacpp-knee\\build\\bin\\llama-server.exe\n"
    "      -c 8192 -np 1\n"
    "    proxy: http://127.0.0.1:18301\n"
)


class TestNpEdit(unittest.TestCase):
    def test_edit_is_token_exact(self):
        edited, old = runner.edit_np_yaml(YAML_FIXTURE, 8)
        self.assertEqual(old, 2)
        self.assertIn("-c 131072 -np 8 -ub 1024", edited)
        # Exactly one character differs, and it is the -np digit.
        self.assertEqual(len(edited), len(YAML_FIXTURE))
        diff = [i for i, (a, b) in enumerate(zip(edited, YAML_FIXTURE)) if a != b]
        self.assertEqual(len(diff), 1)
        self.assertEqual(YAML_FIXTURE[diff[0]], "2")
        self.assertEqual(edited[diff[0]], "8")

    def test_the_side_seat_np_is_untouched(self):
        edited, _ = runner.edit_np_yaml(YAML_FIXTURE, 4)
        self.assertIn("-c 8192 -np 1", edited)       # phi4-vk1 unchanged
        self.assertIn("-c 131072 -np 4 -ub 1024", edited)

    def test_edit_is_reversible_byte_for_byte(self):
        edited, old = runner.edit_np_yaml(YAML_FIXTURE, 8)
        restored, back = runner.edit_np_yaml(edited, old)
        self.assertEqual(back, 8)
        self.assertEqual(restored, YAML_FIXTURE)

    def test_reversible_on_a_fixture_copy_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "omen.yaml"
            path.write_text(YAML_FIXTURE, encoding="utf-8", newline="")
            original = path.read_bytes()
            for target in (4, 8, 2):
                with path.open(encoding="utf-8", newline="") as handle:
                    text = handle.read()
                new_text, _ = runner.edit_np_yaml(text, target)
                path.write_text(new_text, encoding="utf-8", newline="")
            self.assertEqual(path.read_bytes(), original)

    def test_the_real_production_entry_reads_the_standing_np(self):
        text = runner.read_exact(runner.REPO / "fleet" / "arcserve" / "llama-swap" / "omen.yaml")
        self.assertEqual(runner.read_np_yaml(text), runner.BASE_NP)
        edited, old = runner.edit_np_yaml(text, 8)
        restored, _ = runner.edit_np_yaml(edited, old)
        self.assertEqual(restored, text)
        self.assertIn("-c 131072 -np 8 -ub 1024", edited)

    def test_read_exact_does_not_translate_newlines(self):
        # Path.read_text turns CRLF into LF; writing that back would rewrite every line
        # while claiming one token changed.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crlf.yaml"
            path.write_bytes(YAML_FIXTURE.replace("\n", "\r\n").encode("utf-8"))
            text = runner.read_exact(path)
            self.assertIn("\r\n", text)
            edited, old = runner.edit_np_yaml(text, 8)
            runner.write_exact(path, edited)
            restored, _ = runner.edit_np_yaml(runner.read_exact(path), old)
            runner.write_exact(path, restored)
            self.assertEqual(path.read_bytes(),
                             YAML_FIXTURE.replace("\n", "\r\n").encode("utf-8"))

    def test_a_missing_model_block_raises(self):
        with self.assertRaises(ValueError):
            runner.edit_np_yaml(YAML_FIXTURE, 4, model_key="not-a-model")

    def test_two_np_tokens_in_one_block_refuse_to_guess(self):
        doubled = YAML_FIXTURE.replace("      --host 127.0.0.1 --port 8082\n",
                                       "      --host 127.0.0.1 --port 8082 -np 3\n")
        with self.assertRaises(ValueError) as caught:
            runner.edit_np_yaml(doubled, 8)
        self.assertIn("refusing to guess", str(caught.exception))

    def test_a_block_with_no_np_raises(self):
        stripped = YAML_FIXTURE.replace("-c 131072 -np 2 -ub 1024", "-c 131072 -ub 1024")
        with self.assertRaises(ValueError):
            runner.edit_np_yaml(stripped, 8)


class TestEpochBoundary(unittest.TestCase):
    def test_the_row_matches_the_shape_already_on_disk(self):
        on_disk = json.loads(runner.BASELINES.read_text(encoding="utf-8-sig"))
        existing = on_disk["epoch_boundaries"][0]
        row = runner.epoch_boundary_row("2026-09-09T12:00:00-07:00", 8, 91.2)
        self.assertEqual(set(row), set(existing))
        self.assertIn("-np 8", row["reason"])
        self.assertIn("-ub %d" % runner.UB, row["reason"])
        self.assertIs(row["baseline_preserved"], False)  # an -np change is a config change

    def test_append_preserves_the_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rate-baselines.json"
            path.write_text(runner.BASELINES.read_text(encoding="utf-8-sig"), encoding="utf-8")
            before = json.loads(path.read_text(encoding="utf-8-sig"))
            row = runner.epoch_boundary_row("2026-09-09T12:00:00-07:00", 4, 88.0)
            after = runner.append_epoch_boundary(path, row)
            self.assertEqual(after["rungs"], before["rungs"])
            self.assertEqual(after["epoch_boundaries"][:-1], before["epoch_boundaries"])
            self.assertEqual(after["epoch_boundaries"][-1], row)


# --------------------------------------------------------- gate 4: /slots, /metrics --
def slots_sample(t, *processing):
    return {"t": t, "slots": [{"id": i, "is_processing": bool(p)}
                              for i, p in enumerate(processing)]}


class TestSlotBusy(unittest.TestCase):
    def test_fractions_from_a_fixture(self):
        samples = [slots_sample(0.0, True, True), slots_sample(1.0, True, False),
                   slots_sample(2.0, True, True), slots_sample(3.0, False, False)]
        out = runner.slot_busy(samples)
        self.assertEqual(out["ok_polls"], 4)
        self.assertEqual(out["slots_seen"], 2)
        self.assertEqual(out["any_busy_fraction"], 0.75)
        self.assertEqual(out["all_busy_fraction"], 0.5)
        self.assertEqual(out["mean_busy_slots"], 1.25)
        self.assertEqual(out["busy_slot_fraction"], 0.625)

    def test_p5_shape_both_slots_busy_at_least_ninety_percent(self):
        samples = [slots_sample(float(i), True, True) for i in range(19)]
        samples.append(slots_sample(19.0, True, False))
        self.assertEqual(runner.slot_busy(samples)["all_busy_fraction"], 0.95)

    def test_errors_are_counted_and_excluded_from_the_denominator(self):
        samples = [slots_sample(0.0, True, True),
                   {"t": 1.0, "error": "HTTP 401: Invalid API Key"},
                   slots_sample(2.0, False, False)]
        out = runner.slot_busy(samples)
        self.assertEqual((out["ok_polls"], out["error_polls"]), (2, 1))
        self.assertEqual(out["any_busy_fraction"], 0.5)
        self.assertIn("401", out["error_sample"])

    def test_no_successful_poll_yields_null_not_zero(self):
        out = runner.slot_busy([{"t": 0.0, "error": "HTTP 401: Invalid API Key"}])
        self.assertIsNone(out["any_busy_fraction"])
        self.assertIsNone(out["all_busy_fraction"])
        self.assertIsNone(out["slots_seen"])
        self.assertEqual(out["ok_polls"], 0)

    def test_an_empty_poll_list_is_null_throughout(self):
        out = runner.slot_busy([])
        self.assertEqual(out["polls"], 0)
        self.assertIsNone(out["busy_slot_fraction"])

    def test_every_poll_survives_beside_the_span_figures(self):
        # `slots_poll.polls = 19` could not attribute r4's 0.45 s slot wait to a request.
        samples = [slots_sample(10.0, True, True),
                   {"t": 11.0, "error": "HTTP 401: Invalid API Key"},
                   slots_sample(12.0, True, False)]
        out = runner.slot_busy(samples)
        for field in ("polls", "ok_polls", "error_polls", "slots_seen", "any_busy_fraction",
                      "all_busy_fraction", "mean_busy_slots", "busy_slot_fraction",
                      "error_sample"):
            self.assertIn(field, out)      # the old names, unchanged
        rows = out["samples"]
        self.assertEqual([r["t_wall"] for r in rows], [10.0, 11.0, 12.0])
        self.assertEqual([r["ok"] for r in rows], [True, False, True])
        self.assertEqual([r["n_busy"] for r in rows], [2, None, 1])
        self.assertEqual([r["n_slots"] for r in rows], [2, None, 2])
        self.assertIn("401", rows[1]["error"])

    def test_a_receipts_own_samples_can_be_re_reduced_offline(self):
        rows = runner.slot_busy([slots_sample(float(i), True, True) for i in range(4)])["samples"]
        again = runner.slot_busy(rows)
        self.assertEqual(again["all_busy_fraction"], 1.0)
        self.assertEqual(again["ok_polls"], 4)


class TestLoadWindowSlotBusy(unittest.TestCase):
    """Gate 4 must be scored over the LOAD, not over the whole ff_cell subprocess.

    ``metrics_before`` / the poller / ``metrics_after`` bracket ff_cell, which runs its own
    single-stream pre- and post-rate probes around the load. Across the first live repeats
    that made ``both_slots_busy_fraction`` read 0.47-0.53 while both slots were busy the
    whole time INSIDE the load -- and P5 asks about the load.
    """

    def _span(self):
        pre = [slots_sample(float(i), True, False) for i in range(0, 5)]
        load = [slots_sample(float(i), True, True) for i in range(5, 15)]
        post = [slots_sample(float(i), True, False) for i in range(15, 20)]
        return pre + load + post

    def test_only_in_window_polls_are_counted(self):
        out = runner.window_slot_busy(self._span(), 5.0, 14.0)
        self.assertEqual(out["n_samples"], 10)
        self.assertEqual(out["ok_polls"], 10)
        self.assertEqual(out["all_busy_fraction"], 1.0)
        self.assertEqual(out["busy_slot_fraction"], 1.0)
        self.assertEqual((out["t0"], out["t1"], out["window_s"]), (5.0, 14.0, 9.0))

    def test_the_span_figure_is_diluted_by_ff_cells_own_probes(self):
        span = runner.slot_busy(self._span())
        self.assertEqual(span["all_busy_fraction"], 0.5)          # the diluted reading
        window = runner.window_slot_busy(self._span(), 5.0, 14.0)
        self.assertEqual(window["all_busy_fraction"], 1.0)        # the gate-4 term

    def test_both_bounds_are_inclusive(self):
        samples = [slots_sample(1.0, True, True), slots_sample(2.0, True, True),
                   slots_sample(3.0, True, True)]
        self.assertEqual(runner.window_slot_busy(samples, 1.0, 3.0)["n_samples"], 3)
        self.assertEqual(runner.window_slot_busy(samples, 1.5, 2.5)["n_samples"], 1)

    def test_no_poll_inside_the_window_is_null_not_zero(self):
        out = runner.window_slot_busy([slots_sample(0.0, True, True)], 100.0, 200.0)
        self.assertEqual(out["n_samples"], 0)
        self.assertIsNone(out["all_busy_fraction"])
        self.assertIsNone(out["busy_slot_fraction"])
        self.assertIn("inside the load window", out["reason"])

    def test_a_missing_bound_selects_nothing_and_says_why(self):
        out = runner.window_slot_busy([slots_sample(0.0, True, True)], None, 5.0)
        self.assertEqual(out["n_samples"], 0)
        self.assertIsNone(out["all_busy_fraction"])
        self.assertIn("no load window", out["reason"])

    def test_the_compact_rows_can_be_windowed_directly(self):
        rows = runner.slot_busy(self._span())["samples"]
        self.assertEqual(runner.window_slot_busy(rows, 5.0, 14.0)["all_busy_fraction"], 1.0)


class TestLoadWindowBounds(unittest.TestCase):
    def test_min_started_at_and_max_completed_at(self):
        rows = [{"started_at": "2026-09-09T10:36:14.826836Z",
                 "completed_at": "2026-09-09T10:36:17.947447Z"},
                {"started_at": "2026-09-09T10:36:15.000000Z",
                 "completed_at": "2026-09-09T10:36:21.500000Z"}]
        t0, t1 = runner.load_window_bounds(rows)
        self.assertEqual(t0, runner._iso_epoch(rows[0]["started_at"]))
        self.assertEqual(t1, runner._iso_epoch(rows[1]["completed_at"]))
        self.assertAlmostEqual(t1 - t0, 6.673164, places=5)

    def test_the_stamp_parser_accepts_z_and_an_explicit_offset(self):
        self.assertEqual(runner._iso_epoch("2026-09-09T10:36:14.826836Z"),
                         runner._iso_epoch("2026-09-09T10:36:14.826836+00:00"))
        # The harness writes UTC; a naive stamp is read as UTC, never as local time.
        self.assertEqual(runner._iso_epoch("2026-09-09T10:36:14.826836"),
                         runner._iso_epoch("2026-09-09T10:36:14.826836Z"))

    def test_an_unparsable_stamp_is_none_not_a_guess(self):
        for bad in (None, "", "not a timestamp", 1757413000.0):
            with self.subTest(bad=bad):
                self.assertIsNone(runner._iso_epoch(bad))

    def test_half_a_window_is_not_a_window(self):
        self.assertEqual(runner.load_window_bounds([]), (None, None))
        self.assertEqual(runner.load_window_bounds([{"started_at": "2026-09-09T10:36:14Z"}]),
                         (None, None))
        self.assertEqual(runner.load_window_bounds([{"completed_at": "2026-09-09T10:36:14Z"}]),
                         (None, None))


METRICS = """\
# HELP llamacpp:n_busy_slots_per_decode Average busy slots per decode
# TYPE llamacpp:n_busy_slots_per_decode counter
llamacpp:n_busy_slots_per_decode 1024
llamacpp:predicted_tokens_seconds 106.4
llamacpp:kv_cache_usage_ratio 0.03
"""


class TestMetrics(unittest.TestCase):
    def test_parses_bare_series_and_skips_comments(self):
        parsed = runner.parse_prometheus(METRICS)
        self.assertEqual(parsed[runner.BUSY_SLOTS_SERIES], 1024.0)
        self.assertEqual(parsed["llamacpp:predicted_tokens_seconds"], 106.4)
        self.assertNotIn("# HELP", parsed)

    def test_busy_slots_delta(self):
        after = runner.parse_prometheus(METRICS.replace("1024", "1090"))
        self.assertEqual(runner.busy_slots_delta(runner.parse_prometheus(METRICS), after), 66.0)

    def test_a_missing_series_is_null_not_zero(self):
        self.assertIsNone(runner.busy_slots_delta({}, runner.parse_prometheus(METRICS)))
        self.assertIsNone(runner.busy_slots_delta(runner.parse_prometheus(METRICS), None))


# ------------------------------------------------------------- gate 7: real prefill --
def prompt_metrics(uncached, cached=None):
    out = {runner.PROMPT_TOKENS_SERIES: float(uncached)}
    if cached is not None:
        out[runner.PROMPT_TOKENS_CACHED_SERIES] = float(cached)
    return out


def load_rows(count=6, prompt_tokens=440):
    return [{"prompt_tokens": prompt_tokens} for _ in range(count)]


class TestPrefillReal(unittest.TestCase):
    """The surface's size axis (512 / 8K / 32K) IS prefill, so a cached prefix is a defect.

    The harness's own ``_performed_full_prefill`` NULLS a cached rate, which is right for a
    rate and wrong for this surface: the cache has to be defeated, and the SERVER's own
    counters are the only witness that it was.
    """

    def test_the_measured_cache_hit_fails(self):
        # np2-p512-c2-r4, measured 2026-09-09: prompt_tokens_total moved 64 while
        # prompt_tokens_cached_total moved 3,073, against 6 x 440 = 2,640 expected.
        out = runner.prefill_real(prompt_metrics(172631, 20647),
                                  prompt_metrics(172695, 23720), load_rows())
        self.assertEqual((out["uncached"], out["cached"], out["expected"]), (64.0, 3073.0, 2640))
        self.assertEqual(out["fraction"], 0.0242)
        self.assertEqual(out["outcome"], "fail")
        self.assertIn("prompt cache", out["reason"])

    def test_real_prefill_passes(self):
        out = runner.prefill_real(prompt_metrics(1000, 500),
                                  prompt_metrics(3640, 500), load_rows())
        self.assertEqual(out["outcome"], "pass")
        self.assertEqual(out["fraction"], 1.0)
        self.assertEqual(out["cached"], 0.0)
        self.assertIsNone(out["probe_contribution"])

    def test_ninety_percent_is_the_line(self):
        rows = load_rows()                                  # 2,640 expected -> 2,376
        self.assertEqual(
            runner.prefill_real(prompt_metrics(0), prompt_metrics(2376), rows)["outcome"], "pass")
        self.assertEqual(
            runner.prefill_real(prompt_metrics(0), prompt_metrics(2375), rows)["outcome"], "fail")

    def test_an_absent_cached_series_is_zero_not_unknown(self):
        out = runner.prefill_real(prompt_metrics(0), prompt_metrics(2640), load_rows())
        self.assertNotIn(runner.PROMPT_TOKENS_CACHED_SERIES, prompt_metrics(0))
        self.assertEqual(out["cached"], 0.0)
        self.assertEqual(out["outcome"], "pass")

    def test_ff_cells_probes_are_reported_never_subtracted(self):
        # The scrape bracket also holds ff_cell's single-stream pre/post rate probes, which
        # prefill the ratecheck prompt. An unknown correction is never applied silently.
        out = runner.prefill_real(prompt_metrics(0), prompt_metrics(3520), load_rows())
        self.assertEqual(out["probe_contribution"], 880.0)
        self.assertEqual(out["outcome"], "pass")
        self.assertIn("nothing is subtracted", out["note"])

    def test_missing_metrics_are_null_never_a_pass(self):
        for before, after in ((None, prompt_metrics(10)), (prompt_metrics(10), None),
                              ({}, {}), ({"http_status": 401}, {"http_status": 401})):
            out = runner.prefill_real(before, after, load_rows())
            with self.subTest(before=before):
                self.assertEqual(out["outcome"], "null")
                self.assertIsNone(out["uncached"])
                self.assertIsNone(out["fraction"])

    def test_rows_without_prompt_tokens_are_null_not_zero(self):
        out = runner.prefill_real(prompt_metrics(0), prompt_metrics(64), [{"latency_s": 3.0}])
        self.assertEqual(out["outcome"], "null")
        self.assertIsNone(out["expected"])
        self.assertEqual(out["rows_counted"], 0)

    def test_the_series_are_the_ones_the_live_server_publishes(self):
        # Both names come off the real /metrics scrape recorded in np2-p512-c2-r4's receipt.
        self.assertEqual(runner.PROMPT_TOKENS_SERIES, "llamacpp:prompt_tokens_total")
        self.assertEqual(runner.PROMPT_TOKENS_CACHED_SERIES,
                         "llamacpp:prompt_tokens_cached_total")


# ------------------------------------------------ gate 5: duty cycle vs the reference --
BUS9 = "adapter_000171de"
BUS4 = "adapter_00016def"
B70 = "Intel(R) Arc(TM) Pro B70 Graphics"


def write_stream(path: Path, watts_by_adapter: dict, start_ns: int = 5_000_000_000,
                 extra_rows: list | None = None) -> Path:
    """A b70tools-shaped events.jsonl: identity rows plus a cumulative energy counter at 1 Hz."""
    lines = [
        json.dumps({"k": "ai", "a": BUS9, "desc": B70, "bdf": "0000:09:00.0"}),
        json.dumps({"k": "ai", "a": BUS4, "desc": B70, "bdf": "0000:04:00.0"}),
        json.dumps({"k": "ai", "a": "adapter_igpu", "desc": "Intel(R) Graphics",
                    "bdf": "0000:00:02.0"}),
    ]
    for adapter, watts in watts_by_adapter.items():
        joules = 1000.0
        t = start_ns
        lines.append(json.dumps({"k": "ms", "a": adapter, "n": "gpu.energy_j_counter",
                                 "v": joules, "t": t}))
        for w in watts:
            joules += w          # 1 s ticks, so dJ == watts
            t += NS
            lines.append(json.dumps({"k": "ms", "a": adapter, "n": "gpu.energy_j_counter",
                                     "v": joules, "t": t}))
    lines.extend(extra_rows or [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_reference(root: Path, bus9_p50: float, bus4_p50: float) -> Path:
    """A frozen-reference receipt in the real shape, plus the sibling stream that names BDFs."""
    ref = root / "ref-fixture"
    (ref / "b70").mkdir(parents=True)
    (ref / "b70" / "events.jsonl").write_text("\n".join([
        json.dumps({"k": "ai", "a": BUS9, "desc": B70, "bdf": "0000:09:00.0"}),
        json.dumps({"k": "ai", "a": BUS4, "desc": B70, "bdf": "0000:04:00.0"}),
    ]) + "\n", encoding="utf-8")
    receipt = {"probe": "SAT-L1-REFERENCE", "status": "executed",
               "power": {"counter": "gpu.energy_j_counter", "cards": {
                   BUS9: {"desc": B70, "ticks": 140,
                          "burst": {"p50_w": bus9_p50, "p95_w": 184.74, "max_w": 191.81},
                          "ambient": {"p50_w": 26.67}},
                   BUS4: {"desc": B70, "ticks": 140,
                          "burst": {"p50_w": bus4_p50, "p95_w": 164.37, "max_w": 192.83},
                          "ambient": {"p50_w": 26.60}}}}}
    (ref / "receipt.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    return ref / "receipt.json"


class TestReference(unittest.TestCase):
    def test_reads_per_card_p50_from_the_receipt_keyed_by_bdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = runner.load_reference(write_reference(Path(tmp), 159.92, 114.05))
        self.assertEqual(ref["keyed_by"], "bdf")
        self.assertEqual(ref["cards"]["0000:09:00.0"]["burst_p50_w"], 159.92)
        self.assertEqual(ref["cards"]["0000:04:00.0"]["burst_p50_w"], 114.05)

    def test_the_numbers_come_from_the_receipt_not_from_the_code(self):
        # Change the frozen receipt and the thresholds must move with it.
        with tempfile.TemporaryDirectory() as tmp:
            ref = runner.load_reference(write_reference(Path(tmp), 200.0, 50.0))
        self.assertEqual(ref["cards"]["0000:09:00.0"]["burst_p50_w"], 200.0)
        self.assertEqual(ref["cards"]["0000:04:00.0"]["burst_p50_w"], 50.0)

    def test_the_real_frozen_reference_is_readable_and_per_card(self):
        if not runner.REFERENCE_RECEIPT.is_file():
            self.skipTest("the frozen reference receipt is not on this box")
        ref = runner.load_reference(runner.REFERENCE_RECEIPT)
        p50s = sorted(c["burst_p50_w"] for c in ref["cards"].values())
        self.assertEqual(len(p50s), 2)
        self.assertNotEqual(p50s[0], p50s[1])  # per card, not a single scalar

    def test_a_receipt_without_power_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps({"probe": "SAT-L1-REFERENCE"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.load_reference(path)


class TestDutyCycle(unittest.TestCase):
    def _fixture(self, tmp, bus9_watts, bus4_watts):
        events = write_stream(Path(tmp) / "events.jsonl",
                              {BUS9: bus9_watts, BUS4: bus4_watts})
        reference = runner.load_reference(write_reference(Path(tmp), 159.92, 114.05))
        return runner.card_intervals(events), reference

    def test_time_weighted_fraction_above_each_cards_own_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            # bus9 threshold 143.93 W: 6 of 10 ticks above.
            # bus4 threshold 102.64 W: 3 of 10 ticks above -- a DIFFERENT threshold, its own.
            cards, reference = self._fixture(
                tmp,
                [180.0] * 6 + [27.0] * 4,
                [120.0] * 3 + [27.0] * 7)
            out = runner.duty_cycle(cards, reference)
        self.assertEqual(out["cards"]["0000:09:00.0"]["duty_cycle"], 0.6)
        self.assertEqual(out["cards"]["0000:04:00.0"]["duty_cycle"], 0.3)
        self.assertEqual(out["cards"]["0000:09:00.0"]["threshold_w"], 143.93)
        self.assertEqual(out["cards"]["0000:04:00.0"]["threshold_w"], 102.64)

    def test_a_card_scored_against_the_other_cards_reference_would_be_wrong(self):
        # 120 W is above bus4's threshold and below bus9's. One scalar reference would
        # score both cards identically; per-card references do not.
        with tempfile.TemporaryDirectory() as tmp:
            cards, reference = self._fixture(tmp, [120.0] * 10, [120.0] * 10)
            out = runner.duty_cycle(cards, reference)
        self.assertEqual(out["cards"]["0000:09:00.0"]["duty_cycle"], 0.0)
        self.assertEqual(out["cards"]["0000:04:00.0"]["duty_cycle"], 1.0)

    def test_the_window_excludes_intervals_outside_the_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards, reference = self._fixture(
                tmp, [27.0] * 4 + [180.0] * 4 + [27.0] * 2, [27.0] * 10)
            start = 5_000_000_000 + 4 * NS       # the 4 idle ticks are ambient, not the cell
            end = start + 4 * NS
            out = runner.duty_cycle(cards, reference, window_ns=(start, end))
        card = out["cards"]["0000:09:00.0"]
        self.assertEqual(card["intervals_considered"], 4)
        self.assertEqual(card["duty_cycle"], 1.0)
        self.assertEqual(card["seconds_considered"], 4.0)

    def test_the_igpu_is_not_part_of_the_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = write_stream(Path(tmp) / "events.jsonl",
                                  {BUS9: [180.0] * 4, BUS4: [30.0] * 4,
                                   "adapter_igpu": [5.0] * 4})
            cards = runner.card_intervals(events)
        self.assertEqual(set(cards), {BUS9, BUS4})

    def test_a_card_with_no_reference_is_null_not_a_borrowed_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards, reference = self._fixture(tmp, [180.0] * 4, [180.0] * 4)
            reference["cards"].pop("0000:04:00.0")
            out = runner.duty_cycle(cards, reference)
        self.assertIsNone(out["cards"]["0000:04:00.0"]["duty_cycle"])
        self.assertIn("no frozen reference", out["cards"]["0000:04:00.0"]["reason"])
        self.assertEqual(out["cards"]["0000:09:00.0"]["duty_cycle"], 1.0)

    def test_the_denominator_caveat_rides_on_every_duty_result(self):
        # A duty cycle is a fraction OF a ~92 s burst standing in for hours-long workloads.
        # A receipt that carries the fraction without the denominator is a numerator, so the
        # caveat and the reference's own burst window are part of the result, not a docstring.
        with tempfile.TemporaryDirectory() as tmp:
            cards, reference = self._fixture(tmp, [180.0] * 4, [180.0] * 4)
            out = runner.duty_cycle(cards, reference)
        self.assertEqual(out["reference_caveat"], runner.DUTY_REFERENCE_CAVEAT)
        self.assertIn("92 s", out["reference_caveat"])
        self.assertIn("reference_burst_window", out)

    def test_the_caveat_survives_a_reference_that_predates_it(self):
        # An old frozen receipt has no `caveat` key; the fraction still must not ship bare.
        with tempfile.TemporaryDirectory() as tmp:
            cards, reference = self._fixture(tmp, [180.0] * 4, [180.0] * 4)
            reference.pop("caveat", None)
            out = runner.duty_cycle(cards, reference)
        self.assertEqual(out["reference_caveat"], runner.DUTY_REFERENCE_CAVEAT)

    def test_the_burst_window_is_read_from_the_receipt_not_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_reference(Path(tmp), 159.92, 114.05)
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["burst"] = {"seconds": 90.0, "clients": 2, "prompt_tokens": 8192,
                            "requests": 22}
            doc["power"]["cards"][BUS9]["burst"]["intervals"] = 92
            doc["power"]["cards"][BUS4]["burst"]["intervals"] = 92
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            ref = runner.load_reference(path)
        self.assertEqual(ref["burst_window"]["declared_s"], 90.0)
        self.assertEqual(ref["burst_window"]["clients"], 2)
        self.assertEqual(ref["burst_window"]["measured_intervals_s"], 92)
        self.assertEqual(ref["caveat"], runner.DUTY_REFERENCE_CAVEAT)

    def test_a_reference_with_no_burst_block_still_loads(self):
        # The fixture (and any pre-2026-09-09 receipt) carries no top-level `burst`.
        with tempfile.TemporaryDirectory() as tmp:
            ref = runner.load_reference(write_reference(Path(tmp), 159.92, 114.05))
        self.assertIsNone(ref["burst_window"]["declared_s"])
        self.assertIsNone(ref["burst_window"]["measured_intervals_s"])
        self.assertEqual(ref["cards"]["0000:09:00.0"]["burst_p50_w"], 159.92)

    def test_a_stream_without_the_counter_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(json.dumps({"k": "ms", "a": BUS9, "n": "gpu.temperature_c",
                                        "v": 52.0, "t": 1}) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                runner.card_intervals(path)

    def test_t_is_read_as_nanoseconds(self):
        # 1 s apart in NS. Reading t as 10 MHz QPC ticks would make this 100 s and the
        # watts 100x too small -- the error sat_reference_capture measured on 2026-09-09.
        with tempfile.TemporaryDirectory() as tmp:
            events = write_stream(Path(tmp) / "events.jsonl", {BUS9: [150.0], BUS4: [150.0]})
            cards = runner.card_intervals(events)
        (t0, t1, watts) = cards[BUS9]["intervals"][0]
        self.assertEqual(t1 - t0, NS)
        self.assertAlmostEqual(watts, 150.0)


# ------------------------------------------------------------------ gate 3: admission --
class TestBudgetHeadroom(unittest.TestCase):
    def _stream(self, tmp, rows):
        path = Path(tmp) / "events.jsonl"
        lines = [json.dumps({"k": "ai", "a": BUS9, "desc": B70, "bdf": "0000:09:00.0"}),
                 json.dumps({"k": "ms", "a": BUS9, "n": "gpu.energy_j_counter", "v": 1.0, "t": 1})]
        lines.extend(json.dumps(r) for r in rows)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_headroom_is_budget_minus_usage(self):
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stream(tmp, [
                {"k": "ms", "a": BUS9, "n": runner.BUDGET, "v": 30 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.COMMITTED, "v": 16 * gb, "t": 2},
            ])
            out = runner.budget_headroom(path)
        self.assertEqual(out["cards"]["0000:09:00.0"]["min_headroom_gb"], 14.0)
        self.assertIs(out["over_admitted"], False)

    def test_negative_headroom_flags_over_admitted_and_keeps_the_row(self):
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stream(tmp, [
                {"k": "ms", "a": BUS9, "n": runner.BUDGET, "v": 30 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.COMMITTED, "v": 16 * gb, "t": 2},
                {"k": "ms", "a": BUS9, "n": runner.COMMITTED, "v": 31 * gb, "t": 3},
            ])
            out = runner.budget_headroom(path)
        self.assertIs(out["over_admitted"], True)
        self.assertEqual(out["cards"]["0000:09:00.0"]["min_headroom_gb"], -1.0)
        self.assertIn("KEPT", out["note"])

    def test_no_budget_pair_is_unknown_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = runner.budget_headroom(self._stream(tmp, []))
        self.assertIsNone(out["over_admitted"])

    def test_the_per_process_usage_metric_is_never_the_headroom_term(self):
        # First live cell, 2026-09-09: DXGI CurrentUsage through b70tools reported b70tools'
        # OWN 4,096 bytes on a card holding 16 GB of weights, and "budget - usage" was ~31 GB on
        # every cell -- a green gate proving nothing. The adapter-wide committed figure is the term.
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stream(tmp, [
                {"k": "ms", "a": BUS9, "n": runner.BUDGET, "v": 33 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.PROCESS_USAGE, "v": 4096, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.COMMITTED, "v": 16 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.AVAILABLE, "v": 17 * gb, "t": 1},
            ])
            out = runner.budget_headroom(path)
        card = out["cards"]["0000:09:00.0"]
        self.assertEqual(card["min_headroom_gb"], 17.0)
        self.assertEqual(card["committed_gb"], 16.0)
        self.assertEqual(card["available_for_reservation_gb"], 17.0)
        self.assertEqual(card["process_usage_gb"], 0.0)
        self.assertIs(out["over_admitted"], False)
        self.assertIn(runner.COMMITTED, out["headroom_term"])

    def test_budget_plus_process_usage_alone_is_unknown_not_a_vacuous_pass(self):
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stream(tmp, [
                {"k": "ms", "a": BUS9, "n": runner.BUDGET, "v": 33 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.PROCESS_USAGE, "v": 4096, "t": 1},
            ])
            out = runner.budget_headroom(path)
        self.assertIsNone(out["over_admitted"])
        self.assertIsNone(out["cards"]["0000:09:00.0"]["min_headroom_gb"])
        self.assertEqual(out["cards"]["0000:09:00.0"]["samples"], 0)


class TestRingLiveness(unittest.TestCase):
    def _ring(self, tmp, age_s):
        path = Path(tmp) / "lz_dxgk_ring.etl"
        path.write_bytes(b"x")
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def test_a_ring_written_seconds_ago_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, age = runner.ring_is_live(self._ring(tmp, 10))
        self.assertTrue(live)
        self.assertLess(age, 60)

    def test_a_ring_last_written_minutes_ago_is_dead(self):
        # 2026-09-09: manifest from Aug 30 + ring last written Sep 3 both EXISTED; a file-exists
        # check passed, 19 GB were copied and tracerpt ran before the packager refused.
        with tempfile.TemporaryDirectory() as tmp:
            live, age = runner.ring_is_live(self._ring(tmp, 6 * 24 * 3600))
        self.assertFalse(live)
        self.assertGreater(age, runner.ETW_RING_LIVE_S)

    def test_a_missing_ring_is_dead_with_no_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, age = runner.ring_is_live(Path(tmp) / "absent.etl")
        self.assertFalse(live)
        self.assertIsNone(age)


# ------------------------------------------------------------------- gate 6: depth-0 --
ETW4_STDOUT = """
========================================================================================
ARM etw-r1   raw window 164.0000 s   GPU-ACTIVE SPAN 160.0000 s   DISCARDED BOUNDARY 4.0 s (2.4%)
  server-side work for comparison: prompt_ms=1200 + predicted_ms=3400
========================================================================================

  UNION of 2 deep compute queues  (GPU unfed only when ALL are empty)
    TIME-WEIGHTED  mean_depth=3.895  median_depth=4
    OCCUPANCY      depth0=12.10%   depth<=1=20.00%   depth>=8=1.00%
    STARVATION     longest_zero=0.5000 ms  zero n=10   p50=0.1000 p90=0.2000 ms

  queue 0xFFFFBB041C43A150   submits(trace)=320  server_share=100%
    OCCUPANCY      depth0=18.40%   depth<=1=30.00%   depth>=8=0.50%     (raw-window depth0 was 25.10% -- edge inflation)
"""


class TestDepth0Parsing(unittest.TestCase):
    def test_f0_per_queue_and_the_union(self):
        parsed = runner.parse_etw4_depth(ETW4_STDOUT)
        arm = parsed["arms"]["etw-r1"]
        self.assertEqual(arm["UNION-deep-compute"], 0.121)
        self.assertEqual(arm["0xFFFFBB041C43A150"], 0.184)  # the CORRECTED figure, not 0.251
        self.assertEqual(parsed["f0_union"], {"etw-r1": 0.121})

    def test_unparsable_output_yields_nothing_rather_than_a_guess(self):
        parsed = runner.parse_etw4_depth("need exactly 2 arms\n")
        self.assertEqual(parsed["arms"], {})


# ------------------------------------------------------------------------- receipt --
class TestReceiptSchema(unittest.TestCase):
    def test_a_blank_receipt_carries_every_field_as_null(self):
        row = runner.blank_receipt()
        self.assertEqual(set(row), set(runner.RECEIPT_FIELDS))
        self.assertTrue(all(v is None for v in row.values()))
        runner.assert_receipt_complete(row)

    def test_the_card_s_analysis_plan_fields_are_all_present(self):
        row = runner.blank_receipt()
        for field in ("jobs_per_hour", "latency_p50_s", "latency_p95_s", "latency_p99_s",
                      "ttft_p50_s", "ttft_p95_s", "ttft_p99_s", "slot_busy_fraction",
                      "duty_cycle", "depth0", "budget_headroom", "over_admitted",
                      "guard_verdict", "regime", "gates"):
            self.assertIn(field, row)

    def test_the_load_window_and_prefill_fields_are_in_the_schema(self):
        row = runner.blank_receipt()
        for field in ("cache_prompt", "prefill_real", "prefill_cached",
                      "slots_poll_load_window", "slot_busy_fraction_load_window",
                      "both_slots_busy_fraction_load_window"):
            self.assertIn(field, row)
        # The span fields stay; they are labelled, not replaced.
        self.assertIn("slot_busy_fraction", row)
        self.assertIn("both_slots_busy_fraction", row)

    def test_the_nine_provenance_fields_are_present(self):
        row = runner.blank_receipt()
        for field in ("incumbent_process_epoch", "incumbent_restarted_since_cotenancy",
                      "incumbent_rate_fraction_pre", "incumbent_rate_fraction_post",
                      "placement_assertion", "placement_evidence", "health_gate_passed",
                      "receipt_status", "receipt_status_reason"):
            self.assertIn(field, row)

    def test_a_missing_field_is_schema_drift_not_a_shrug(self):
        row = runner.blank_receipt()
        row.pop("jobs_per_hour")
        with self.assertRaises(ValueError):
            runner.assert_receipt_complete(row)

    def test_an_unexpected_field_is_also_drift(self):
        row = runner.blank_receipt()
        row["improvised"] = 1
        with self.assertRaises(ValueError):
            runner.assert_receipt_complete(row)

    def test_a_receipt_serializes_to_json(self):
        row = runner.blank_receipt()
        row["gates"] = runner.gate_outcomes(row)
        json.dumps(row)  # must not raise

    def test_the_written_receipt_carries_every_field_and_all_eight_gates(self):
        args = runner.build_parser().parse_args(["--one-cell", "np2-p512-c2-r1", "--no-ledger"])
        with tempfile.TemporaryDirectory() as tmp:
            cell_dir = Path(tmp) / "np2-p512-c2-r1"
            row = runner.blank_receipt()
            row.update({"probe": runner.PROBE, "cell": "np2-p512-c2-r1"})
            runner._finish(row, cell_dir, args, "REFUSED_GUARD", "guard read 'stale'")
            written = json.loads((cell_dir / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(set(written), set(runner.RECEIPT_FIELDS))
        self.assertEqual(len(written["gates"]), 8)
        self.assertEqual(written["status"], "REFUSED_GUARD")
        self.assertIsNotNone(written["ts"])
        # Every unmeasured field is an explicit null, never absent.
        self.assertIsNone(written["jobs_per_hour"])
        self.assertIsNone(written["duty_cycle"])
        self.assertIsNone(written["depth0"])


class TestGateOutcomes(unittest.TestCase):
    def test_all_eight_gates_are_always_present(self):
        gates = runner.gate_outcomes(runner.blank_receipt())
        self.assertEqual([g["gate"] for g in gates], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual([g["name"] for g in gates], [name for _n, name in runner.GATES])
        self.assertTrue(all(g["outcome"] == "null" for g in gates))

    def test_a_low_symmetry_ratio_makes_duty_cycle_partially_scored(self):
        row = runner.blank_receipt()
        row["symmetry"] = {"ratio": 0.25}
        row["duty_cycle"] = {"cards": {"0000:09:00.0": {"duty_cycle": 0.42}}}
        gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "board_duty_cycle")
        self.assertEqual(gate["outcome"], "partially_scored")

    def test_a_negative_headroom_is_reported_as_over_admitted(self):
        row = runner.blank_receipt()
        row["budget_headroom"] = {"over_admitted": True}
        gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "admission")
        self.assertEqual(gate["outcome"], "over_admitted")
        self.assertIn("kept", gate["detail"])

    def test_gate_four_scores_the_load_window_and_still_reports_the_span(self):
        row = runner.blank_receipt()
        row["slots_poll"] = {"ok_polls": 20, "all_busy_fraction": 0.5}
        row["slots_poll_load_window"] = {"ok_polls": 10, "all_busy_fraction": 1.0,
                                         "busy_slot_fraction": 1.0, "window_s": 9.0}
        gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "in_flight")
        self.assertEqual(gate["outcome"], "pass")
        self.assertIn("load window 10 polls", gate["detail"])
        self.assertIn("all-slots-busy 1.0", gate["detail"])
        self.assertIn("span 20 polls, all-slots-busy 0.5", gate["detail"])
        self.assertIn("pre/post probes", gate["detail"])

    def test_gate_four_is_null_when_nothing_was_polled_inside_the_load(self):
        row = runner.blank_receipt()
        row["slots_poll"] = {"ok_polls": 20, "all_busy_fraction": 0.5}
        row["slots_poll_load_window"] = {"ok_polls": 0,
                                         "reason": "no /slots poll fell inside the load window"}
        gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "in_flight")
        self.assertEqual(gate["outcome"], "null")
        self.assertIn("span 20 polls", gate["detail"])

    def test_gate_seven_carries_the_prefill_verdict_and_its_numbers(self):
        for outcome in ("pass", "fail", "null"):
            row = runner.blank_receipt()
            row["prefill_real"] = {"outcome": outcome, "reason": "the reason", "uncached": 64,
                                   "cached": 3073, "expected": 2640, "fraction": 0.0242}
            gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "prefill_real")
            with self.subTest(outcome=outcome):
                self.assertEqual(gate["outcome"], outcome)
                self.assertIn("fraction 0.0242", gate["detail"])
                self.assertIn("cached 3073", gate["detail"])

    def test_gate_seven_on_an_unmeasured_cell_is_null(self):
        gate = next(g for g in runner.gate_outcomes(runner.blank_receipt())
                    if g["name"] == "prefill_real")
        self.assertEqual(gate["outcome"], "null")
        self.assertIn("prefill unknown", gate["detail"])

    def test_a_stop_verdict_and_an_unknowable_verdict_are_distinguished(self):
        for verdict, expected in (("at_rate", "pass"), ("degraded", "fail"),
                                  ("stalled", "fail"), ("unreachable", "fail"),
                                  ("stale", "unknown"), ("warn", "unknown")):
            row = runner.blank_receipt()
            row["guard_before"] = {"verdict": verdict}
            gate = next(g for g in runner.gate_outcomes(row) if g["name"] == "production_guard")
            with self.subTest(verdict=verdict):
                self.assertEqual(gate["outcome"], expected)


# ---------------------------------------------------------------- the dry-run plan --
class TestPlanAndBearer(unittest.TestCase):
    def _args(self, **over):
        args = runner.build_parser().parse_args(["--one-cell", "np2-p512-c2-r1"])
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def test_dry_run_is_the_default(self):
        self.assertTrue(runner.build_parser().parse_args(["--one-cell", "x"]).dry_run)
        self.assertFalse(runner.build_parser().parse_args(["--one-cell", "x", "--live"]).dry_run)

    def test_the_full_sweep_is_never_implicit(self):
        self.assertEqual(runner.main([]), 2)

    def test_the_plan_names_the_bearer_and_never_its_value(self):
        info = runner.bearer_info({"OMEN_ARC_TOKEN": "sk-super-secret-value"})
        self.assertEqual(info["length"], len("sk-super-secret-value"))
        self.assertNotIn("secret", json.dumps(info))
        self.assertNotIn("secret", runner.redacted_bearer(info))

    def test_the_printed_command_chain_carries_no_token(self):
        plan = runner.plan_cell("np2-p512-c2-r1", "python.exe", self._args())
        blob = json.dumps(plan)
        self.assertNotIn("Bearer ", blob)
        self.assertIn("OMEN_ARC_TOKEN", blob)
        self.assertIn("QWEN38_API_KEY", blob)

    def test_the_plan_composes_ff_cell_with_the_load_as_its_command(self):
        plan = runner.plan_cell("np2-p512-c2-r1", "py", self._args())
        step7 = next(s for s in plan["steps"] if s["step"] == 7)
        argv = step7["argv"]
        self.assertIn("ff_cell.py", " ".join(str(a) for a in argv))
        self.assertIn("--json-out", argv)
        self.assertIn("--no-ledger", argv)
        command = argv[argv.index("--command") + 1]
        self.assertIn("qwen38_campaign.py load", command)
        self.assertIn("--concurrency 2", command)
        self.assertIn("--prompt-tokens 512", command)

    def test_the_discarded_warm_load_is_depth_matched_at_n2(self):
        plan = runner.plan_cell("np2-p8192-c16-r1", "py", self._args())
        step3 = next(s for s in plan["steps"] if s["step"] == 3)
        argv = step3["argv"]
        self.assertEqual(argv[argv.index("--prompt-tokens") + 1], "8192")
        self.assertEqual(argv[argv.index("--concurrency") + 1], "2")
        self.assertIn("warmdiscard", argv[argv.index("--run-id") + 1])

    def test_every_cell_defeats_the_prompt_cache(self):
        argv = runner.load_argv("py", "np2-p512-c2-r1", 512, 2)
        self.assertIn("--no-cache-prompt", argv)

    def test_the_warm_discard_and_the_measured_load_both_defeat_the_cache(self):
        plan = runner.plan_cell("np2-p512-c2-r1", "py", self._args())
        step3 = next(s for s in plan["steps"] if s["step"] == 3)
        step7 = next(s for s in plan["steps"] if s["step"] == 7)
        self.assertIn("--no-cache-prompt", step3["argv"])
        command = step7["argv"][step7["argv"].index("--command") + 1]
        self.assertIn("--no-cache-prompt", command)

    def test_the_regime_carries_model_depth_n_np_placement_and_epoch(self):
        regime = runner.plan_cell("np8-p512-c4-r2", "py", self._args())["regime"]
        for key in ("model", "depth_tokens", "concurrency", "np", "placement", "epoch"):
            self.assertIn(key, regime)
        self.assertEqual((regime["np"], regime["per_slot_ctx"]), (8, 16384))


class TestPrefillCachedIsKeptNotDropped(unittest.TestCase):
    """A gate-7 fail marks the reason and NEVER the status -- exactly like over_admitted.

    The row is real data about a real cell. Changing ``status`` would make the reducer treat
    it as a refusal instead of an exclusion, and the cell would vanish from the reconciliation
    between what is on disk and what is in the surface.
    """

    def _finish(self, tmp, **fields):
        args = runner.build_parser().parse_args(["--one-cell", "np2-p512-c2-r1", "--no-ledger"])
        cell_dir = Path(tmp) / "np2-p512-c2-r1"
        row = runner.blank_receipt()
        row.update({"probe": runner.PROBE, "cell": "np2-p512-c2-r1"})
        row.update(fields)
        runner._finish(row, cell_dir, args, "scored", "cell completed")
        return json.loads((cell_dir / "receipt.json").read_text(encoding="utf-8"))

    def test_a_cached_prefill_marks_the_reason_and_keeps_the_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._finish(
                tmp, prefill_cached=True,
                prefill_real={"outcome": "fail", "reason": "served from the prompt cache",
                              "uncached": 64, "cached": 3073, "expected": 2640,
                              "fraction": 0.0242})
        self.assertEqual(written["status"], "scored")
        self.assertIn("prefill cached", written["status_reason"])
        self.assertIn("cell completed", written["status_reason"])
        self.assertIs(written["prefill_cached"], True)
        gate = next(g for g in written["gates"] if g["name"] == "prefill_real")
        self.assertEqual(gate["outcome"], "fail")

    def test_a_real_prefill_leaves_the_reason_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._finish(tmp, prefill_cached=False,
                                   prefill_real={"outcome": "pass", "reason": "ok"})
        self.assertEqual(written["status_reason"], "cell completed")
        self.assertIs(written["prefill_cached"], False)

    def test_an_unmeasured_prefill_leaves_the_reason_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._finish(tmp)
        self.assertEqual(written["status_reason"], "cell completed")
        self.assertIsNone(written["prefill_cached"])


# ------------------------------------------- the one extension made to ff_cell.py --
class TestFfCellJsonOut(unittest.TestCase):
    """``--json-out`` is the ONLY change to ff_cell.py. It must be purely additive.

    The runner needs ff_cell's row structurally -- the nine provenance fields, the pre/post
    rates, the placement assertion and its evidence -- so that exactly ONE ledger row per
    cell (this runner's SAT-L1 receipt) carries them. Scraping stdout would be fragile and
    re-deriving them would duplicate the invariant the harness exists to enforce.
    """

    def test_the_flag_defaults_to_off(self):
        import ff_cell

        row = {"probe": "FF-CELL", "cell": "np2-p512-c2-r1"}
        with tempfile.TemporaryDirectory() as tmp:
            ff_cell._append(row, skip=True)  # no json_out -> nothing written anywhere
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_the_row_is_written_even_with_no_ledger(self):
        import ff_cell

        row = {"probe": "FF-CELL", "cell": "np2-p512-c2-r1", "placement_assertion": "both-b70",
               "incumbent_rate_fraction_pre": 0.99, "health_gate_passed": True}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "ff-cell-row.json"
            ff_cell._append(row, skip=True, json_out=str(out))
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), row)

    def test_the_runner_folds_the_nine_fields_out_of_that_row(self):
        # The receipt's provenance fields are ff_cell's keys, not re-derived names.
        import ff_cell

        argv = runner.ff_cell_argv("py", "np2-p512-c2-r1", 3, "load ...", Path("row.json"))
        self.assertIn("--json-out", argv)
        self.assertIn("--no-ledger", argv)
        self.assertTrue(hasattr(ff_cell, "incumbent_epoch"))


# ------------------------------------------------- per-round launch skew (instrument) --
#: Realistic ``-lv 5`` lines. The timestamp is MINUTES.SS.mmm.uuu of SERVER UPTIME, the
#: minutes field runs past 60, the function column renders as ``operator ()``, and a release
#: line's ``n_tokens`` is the tokens GENERATED (106), not the 440-token prompt.
SERVE_LOG_FIXTURE = """\
991.07.423.118 I slot  operator (): id  0 | task 30994 | new prompt, n_ctx_slot = 65536, n_keep = 0, task.n_tokens = 440
991.07.423.281 I slot  operator (): id  1 | task 30996 | new prompt, n_ctx_slot = 65536, n_keep = 0, task.n_tokens = 440
991.07.500.000 D srv  update_slots: decoding batch, n_tokens = 2
this line is not a log line at all
991.10.745.402 I slot      release: id  0 | task 30994 | stop processing: n_tokens = 106, truncated = 0
991.10.745.517 I slot      release: id  1 | task 30996 | stop processing: n_tokens = 106, truncated = 0
991.10.760.001 I slot  operator (): id  0 | task 31100 | new prompt, n_ctx_slot = 65536, n_keep = 0, task.n_tokens = 7
991.11.020.113 I slot      release: id  0 | task 31100 | stop processing: n_tokens = 32, truncated = 0
"""


def launch_line(uptime_s: float, slot: int, task: int, n_tokens: int = 440) -> str:
    minutes = int(uptime_s // 60)
    rest = uptime_s - minutes * 60
    seconds = int(rest)
    micros = int(round((rest - seconds) * 1e6))
    return ("%d.%02d.%03d.%03d I slot  operator (): id  %d | task %d | new prompt, "
            "n_ctx_slot = 65536, n_keep = 0, task.n_tokens = %d"
            % (minutes, seconds, micros // 1000, micros % 1000, slot, task, n_tokens))


def release_line(uptime_s: float, slot: int, task: int, generated: int = 106) -> str:
    minutes = int(uptime_s // 60)
    rest = uptime_s - minutes * 60
    seconds = int(rest)
    micros = int(round((rest - seconds) * 1e6))
    return ("%d.%02d.%03d.%03d I slot      release: id  %d | task %d | stop processing: "
            "n_tokens = %d, truncated = 0"
            % (minutes, seconds, micros // 1000, micros % 1000, slot, task, generated))


def synth_cell(skews_ms, *, t0=59460.0, period=3.32, duration=3.30, warm=False,
               trailing_partial=False, n_tokens=440, task_base=30000):
    """A whole cell's launch/release lines: one round per entry in ``skews_ms``.

    ``task_base`` exists because llama-server's task ids are unique and monotonic across the
    whole process lifetime; two synthetic cells sharing ids would be a fixture that the real
    log can never produce.
    """
    lines, task = [], task_base
    if warm:
        # A lone warm request that RELEASES ~25 ms before round 1 launches. It is only ~0.6 s
        # ahead, far inside any gap threshold a 3.3 s round can produce -- the case the
        # non-overlap rule exists for.
        lines.append(launch_line(t0 - 0.60, 1, task, n_tokens))
        lines.append(release_line(t0 - 0.025, 1, task))
        task += 1
    for index, skew_ms in enumerate(skews_ms):
        start = t0 + index * period
        lines.append(launch_line(start, 0, task, n_tokens))
        lines.append(launch_line(start + skew_ms / 1000.0, 1, task + 1, n_tokens))
        lines.append(release_line(start + duration, 0, task))
        lines.append(release_line(start + duration + skew_ms / 1000.0, 1, task + 1))
        task += 2
    if trailing_partial:
        start = t0 + len(skews_ms) * period
        lines.append(launch_line(start, 0, task, n_tokens))
    return "\n".join(lines) + "\n"


class TestServeLogParser(unittest.TestCase):
    def test_uptime_is_minutes_seconds_millis_micros(self):
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE)
        first = [e for e in events if e["task"] == 30994 and e["event"] == "launch"][0]
        # 991 MINUTES, not hours and not wall clock: 991*60 + 7.423118
        self.assertAlmostEqual(first["uptime_s"], 991 * 60 + 7.423118, places=6)

    def test_both_event_kinds_are_returned_and_junk_is_skipped(self):
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE)
        kinds = sorted((e["event"], e["task"]) for e in events)
        self.assertEqual(kinds, [("launch", 30994), ("launch", 30996), ("launch", 31100),
                                 ("release", 30994), ("release", 30996), ("release", 31100)])
        # the decoding-batch line and the prose line contribute nothing and raise nothing
        self.assertEqual(len(events), 6)

    def test_slots_and_tokens_come_off_the_line(self):
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE)
        launch = [e for e in events if e["task"] == 30996 and e["event"] == "launch"][0]
        self.assertEqual(launch["slot"], 1)
        self.assertEqual(launch["n_tokens"], 440)

    def test_the_filter_keeps_the_releases_of_the_launches_it_kept(self):
        # A release line reports GENERATED tokens (106), so filtering releases on their own
        # number would drop exactly the releases belonging to the 440-token launches.
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE, n_tokens=440)
        self.assertEqual(sorted({e["task"] for e in events}), [30994, 30996])
        releases = [e for e in events if e["event"] == "release"]
        self.assertEqual(len(releases), 2)
        self.assertEqual({e["n_tokens"] for e in releases}, {106})

    def test_the_filter_drops_other_prompt_sizes(self):
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE, n_tokens=7)
        self.assertEqual(sorted({e["task"] for e in events}), [31100])

    def test_lines_may_be_passed_as_a_sequence(self):
        events = runner.parse_serve_log_launches(SERVE_LOG_FIXTURE.splitlines(), n_tokens=440)
        self.assertEqual(len(events), 4)

    def test_a_concatenated_record_is_still_found(self):
        # The live log runs records together with no newline; a match must not straddle the
        # seam, and the record after it must still be found.
        glued = ("991.07.400.000 D No parser definition detected, assuming pure content "
                 "parser." + launch_line(59227.5, 0, 42, 440))
        events = runner.parse_serve_log_launches(glued, n_tokens=440)
        self.assertEqual([e["task"] for e in events], [42])

    def test_empty_input_is_an_empty_list(self):
        self.assertEqual(runner.parse_serve_log_launches(""), [])


class TestReadServeLogTail(unittest.TestCase):
    def test_only_the_tail_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "arc-serve.log"
            path.write_bytes(b"x" * 4096 + b"TAIL\n")
            doc = runner.read_serve_log_tail(path, max_bytes=16)
            self.assertEqual(doc["bytes_read"], 16)
            self.assertEqual(doc["file_bytes"], 4101)
            self.assertTrue(doc["truncated"])
            self.assertTrue(doc["text"].endswith("TAIL\n"))

    def test_a_missing_file_is_a_loud_null(self):
        doc = runner.read_serve_log_tail(Path("no-such-directory") / "arc-serve.log")
        self.assertIsNone(doc["text"])
        self.assertIn("unreadable", doc["reason"])


class TestRoundLaunchSkews(unittest.TestCase):
    def skews(self, text, concurrency=2, **kw):
        events = runner.parse_serve_log_launches(text, n_tokens=440)
        return runner.round_launch_skews(events, expected_concurrency=concurrency, **kw)

    def test_a_clean_cell_is_three_rounds_near_zero(self):
        doc = self.skews(synth_cell([0.2, 0.2, 0.1]))
        self.assertEqual(len(doc["rounds"]), 3)
        self.assertEqual([r["size"] for r in doc["rounds"]], [2, 2, 2])
        self.assertTrue(all(r["complete"] for r in doc["rounds"]))
        self.assertLess(doc["max_skew_ms"], 1.0)
        self.assertEqual(doc["delayed_rounds"], 0)
        self.assertEqual(doc["threshold_ms"], 50.0)

    def test_one_delayed_round_is_counted_and_does_not_move_the_median(self):
        doc = self.skews(synth_cell([0.2, 0.2, 222.0]))
        self.assertEqual([round(r["skew_ms"], 1) for r in doc["rounds"]], [0.2, 0.2, 222.0])
        self.assertAlmostEqual(doc["max_skew_ms"], 222.0, places=1)
        self.assertAlmostEqual(doc["median_skew_ms"], 0.2, places=1)
        self.assertEqual(doc["delayed_rounds"], 1)

    def test_a_leading_lone_warm_request_never_forms_a_round(self):
        # It launches 0.6 s ahead -- inside any gap threshold a 3.3 s round yields -- so only
        # the non-overlap rule can separate it. If it were folded into round 1, every round
        # would shift by one launch and the 222 ms round would vanish.
        doc = self.skews(synth_cell([0.2, 0.2, 222.0], warm=True))
        self.assertEqual(len(doc["rounds"]), 3)
        self.assertEqual([round(r["skew_ms"], 1) for r in doc["rounds"]], [0.2, 0.2, 222.0])
        self.assertEqual(doc["partial_groups"], 1)
        self.assertGreaterEqual(doc["serialized_splits"], 1)
        self.assertIn("fewer than 2 launches", doc["reason"])

    def test_an_incomplete_trailing_round_is_partial_not_a_round(self):
        doc = self.skews(synth_cell([0.2, 0.2], trailing_partial=True))
        self.assertEqual(len(doc["rounds"]), 2)
        self.assertEqual(doc["partial_groups"], 1)
        self.assertEqual(doc["delayed_rounds"], 0)

    def test_the_threshold_is_what_decides_delayed(self):
        text = synth_cell([0.2, 60.0, 222.0])
        self.assertEqual(self.skews(text)["delayed_rounds"], 2)
        self.assertEqual(self.skews(text, threshold_ms=100.0)["delayed_rounds"], 1)
        self.assertEqual(self.skews(text, threshold_ms=500.0)["delayed_rounds"], 0)

    def test_no_launches_is_a_stated_reason_not_an_exception(self):
        doc = runner.round_launch_skews([], expected_concurrency=2)
        self.assertEqual(doc["rounds"], [])
        self.assertIsNone(doc["max_skew_ms"])
        self.assertEqual(doc["delayed_rounds"], 0)
        self.assertIn("no launches", doc["reason"])

    def test_without_releases_the_rule_degrades_and_says_so(self):
        launches = [e for e in runner.parse_serve_log_launches(synth_cell([0.2, 0.2, 222.0]),
                                                               n_tokens=440)
                    if e["event"] == "launch"]
        doc = runner.round_launch_skews(launches, expected_concurrency=2)
        self.assertIsNone(doc["split_gap_s"])
        self.assertIn("no release events", doc["reason"])
        # chunking by concurrency alone still recovers the rounds when nothing is missing
        self.assertEqual([round(r["skew_ms"], 1) for r in doc["rounds"]], [0.2, 0.2, 222.0])

    def test_concurrency_one_has_no_skew_to_measure(self):
        doc = self.skews(synth_cell([0.2, 0.2, 0.2]), concurrency=1)
        self.assertEqual(doc["rounds"], [])
        self.assertEqual(doc["partial_groups"], 6)
        self.assertIn("no launch skew to measure", doc["reason"])


class TestAnchorServeLog(unittest.TestCase):
    """The uptime clock carries no wall stamp, so it has to be anchored -- or refused."""

    T0 = 59460.0
    OFFSET = 1788892648.72

    def rows(self, starts_uptime, latency=3.30, prompt_tokens=440):
        out = []
        for uptime in starts_uptime:
            started = dt.datetime.fromtimestamp(self.OFFSET + uptime, dt.timezone.utc)
            completed = dt.datetime.fromtimestamp(self.OFFSET + uptime + latency,
                                                  dt.timezone.utc)
            out.append({"started_at": started.isoformat().replace("+00:00", "Z"),
                        "completed_at": completed.isoformat().replace("+00:00", "Z"),
                        "latency_s": latency, "prompt_tokens": prompt_tokens})
        return out

    def cell(self, **kw):
        text = synth_cell([0.2, 0.2, 222.0], t0=self.T0, **kw)
        events = runner.parse_serve_log_launches(text, n_tokens=440)
        starts = [self.T0, self.T0, self.T0 + 3.32, self.T0 + 3.32,
                  self.T0 + 6.64, self.T0 + 6.64]
        return events, self.rows(starts)

    def test_the_offset_is_recovered_and_the_method_recorded(self):
        events, rows = self.cell()
        end = max(e["uptime_s"] for e in events)
        doc = runner.anchor_serve_log(events, rows, log_end_epoch=self.OFFSET + end,
                                      log_end_uptime_s=end, prompt_tokens=440)
        self.assertAlmostEqual(doc["offset"], self.OFFSET, places=3)
        self.assertLess(doc["residual_s"], 0.01)
        self.assertIn("log mtime", doc["method"])
        self.assertIsNotNone(doc["duration_residual_s"])
        self.assertFalse(doc["coverage_limited"])

    def test_a_prompt_size_absent_from_the_tail_is_refused_as_coverage(self):
        events, rows = self.cell()
        end = max(e["uptime_s"] for e in events)
        doc = runner.anchor_serve_log(events, rows, log_end_epoch=self.OFFSET + end,
                                      log_end_uptime_s=end, prompt_tokens=8192)
        self.assertIsNone(doc["offset"])
        self.assertTrue(doc["coverage_limited"])
        self.assertIn("task.n_tokens", doc["reason"])

    def test_a_tail_that_does_not_reach_the_cell_is_refused_as_coverage(self):
        events, rows = self.cell()
        end = max(e["uptime_s"] for e in events)
        # the log's end is an hour later than the cell: the coarse anchor points nowhere near
        doc = runner.anchor_serve_log(events, rows, log_end_epoch=self.OFFSET + end + 3600,
                                      log_end_uptime_s=end, prompt_tokens=440)
        self.assertIsNone(doc["offset"])
        self.assertTrue(doc["coverage_limited"])
        self.assertIn("coarse anchor", doc["reason"])

    def test_no_wall_stamp_means_no_anchor(self):
        events, rows = self.cell()
        doc = runner.anchor_serve_log(events, rows, log_end_epoch=None, prompt_tokens=440)
        self.assertIsNone(doc["offset"])
        self.assertIn("cannot be anchored", doc["reason"])

    def test_rows_without_a_started_at_are_refused(self):
        events, _ = self.cell()
        end = max(e["uptime_s"] for e in events)
        doc = runner.anchor_serve_log(events, [{"prompt_tokens": 440}],
                                      log_end_epoch=self.OFFSET + end,
                                      log_end_uptime_s=end, prompt_tokens=440)
        self.assertIsNone(doc["offset"])
        self.assertIn("started_at", doc["reason"])

    def test_an_ambiguous_anchor_is_refused_rather_than_guessed(self):
        # Two identical cells 2 s apart, both inside the coarse tolerance: the fit cannot
        # tell them apart, so it must say so instead of picking one.
        text = (synth_cell([0.2, 0.2, 0.2], t0=self.T0, task_base=30000)
                + synth_cell([0.2, 0.2, 0.2], t0=self.T0 + 2.0, task_base=31000))
        events = runner.parse_serve_log_launches(text, n_tokens=440)
        rows = self.rows([self.T0, self.T0 + 3.32, self.T0 + 6.64])
        end = max(e["uptime_s"] for e in events)
        doc = runner.anchor_serve_log(events, rows, log_end_epoch=self.OFFSET + end,
                                      log_end_uptime_s=end, prompt_tokens=440)
        self.assertIsNone(doc["offset"])
        self.assertIn("ambiguous", doc["reason"])
        self.assertFalse(doc["coverage_limited"])


class TestLaunchSkewForCell(unittest.TestCase):
    OFFSET = 1788892648.72
    T0 = 59460.0

    def rows(self, starts_uptime, latency=3.30, prompt_tokens=440):
        out = []
        for uptime in starts_uptime:
            started = dt.datetime.fromtimestamp(self.OFFSET + uptime, dt.timezone.utc)
            completed = dt.datetime.fromtimestamp(self.OFFSET + uptime + latency,
                                                  dt.timezone.utc)
            out.append({"started_at": started.isoformat().replace("+00:00", "Z"),
                        "completed_at": completed.isoformat().replace("+00:00", "Z"),
                        "latency_s": latency, "prompt_tokens": prompt_tokens})
        return out

    def write_log(self, tmp, text, *, end_uptime=None):
        path = Path(tmp) / "arc-serve.log"
        path.write_text(text, encoding="utf-8")
        end = end_uptime if end_uptime is not None else max(
            e["uptime_s"] for e in runner.parse_serve_log_launches(text))
        os.utime(path, (self.OFFSET + end, self.OFFSET + end))
        return path

    def cell_rows(self):
        return self.rows([self.T0, self.T0, self.T0 + 3.32, self.T0 + 3.32,
                          self.T0 + 6.64, self.T0 + 6.64])

    def test_the_measured_cell_is_timed_and_the_warm_request_is_not_a_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_log(tmp, synth_cell([0.2, 0.2, 222.0], t0=self.T0, warm=True))
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path)
        self.assertEqual([round(r["skew_ms"], 1) for r in doc["rounds"]], [0.2, 0.2, 222.0])
        self.assertEqual(doc["delayed_rounds"], 1)
        self.assertEqual(doc["prompt_tokens"], 440)
        self.assertEqual(doc["partial_groups"], 1)
        self.assertIn("not a failure", doc["note"])

    def test_the_prompt_size_comes_from_the_rows_not_the_request(self):
        # The cell ASKS for 512 and the harness reports 440. Filtering on 512 matches nothing.
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_log(tmp, synth_cell([0.2, 0.2, 0.2], t0=self.T0))
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path)
        self.assertEqual(doc["prompt_tokens"], 440)
        self.assertIsNotNone(doc["rounds"])

    def test_the_discarded_warm_load_16s_earlier_is_not_mistaken_for_the_cell(self):
        # Each cell is preceded by a discarded warm load of IDENTICAL shape. Duration
        # matching alone anchors onto it happily; the coarse mtime offset is what separates
        # them. This is the r9 failure, reproduced.
        with tempfile.TemporaryDirectory() as tmp:
            text = (synth_cell([0.2, 0.2, 221.6], t0=self.T0 - 16.6, task_base=30000)
                    + synth_cell([0.2, 0.2, 222.4], t0=self.T0, task_base=31000))
            path = self.write_log(tmp, text)
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path)
        self.assertAlmostEqual(doc["max_skew_ms"], 222.4, places=1)
        self.assertAlmostEqual(doc["anchor"]["offset"], self.OFFSET, places=3)

    def test_a_missing_log_is_a_loud_null(self):
        doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                          log_path=Path("no-such-dir") / "arc-serve.log")
        self.assertIsNone(doc["rounds"])
        self.assertIn("unreadable", doc["reason"])

    def test_no_matching_launches_is_a_loud_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_log(tmp, synth_cell([0.2, 0.2], t0=self.T0, n_tokens=7))
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path)
        self.assertIsNone(doc["rounds"])
        self.assertIn("anchor failed", doc["reason"])

    def test_an_anchor_failure_is_a_loud_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            # the log's end is stamped an hour after its newest event: the coarse offset is
            # wrong by an hour and nothing can be matched
            text = synth_cell([0.2, 0.2, 0.2], t0=self.T0)
            end = max(e["uptime_s"] for e in runner.parse_serve_log_launches(text))
            path = self.write_log(tmp, text, end_uptime=end + 3600)
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path)
        self.assertIsNone(doc["rounds"])
        self.assertIn("anchor failed", doc["reason"])

    def test_rows_without_prompt_tokens_are_a_loud_null(self):
        rows = [{"started_at": "2026-09-09T11:08:29.507594Z",
                 "completed_at": "2026-09-09T11:08:32.830296Z", "latency_s": 3.32}]
        doc = runner.launch_skew_for_cell(rows, expected_concurrency=2,
                                          log_path=Path("unused.log"))
        self.assertIsNone(doc["rounds"])
        self.assertIn("prompt_tokens", doc["reason"])

    def test_no_load_window_is_a_loud_null(self):
        rows = [{"prompt_tokens": 440, "latency_s": 3.32}]
        doc = runner.launch_skew_for_cell(rows, expected_concurrency=2,
                                          log_path=Path("unused.log"))
        self.assertIsNone(doc["rounds"])
        self.assertIn("window", doc["reason"])

    def test_the_tail_grows_only_for_a_coverage_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_log(tmp, synth_cell([0.2, 0.2, 222.0], t0=self.T0))
            doc = runner.launch_skew_for_cell(self.cell_rows(), expected_concurrency=2,
                                              log_path=path, max_bytes=200)
        # 200 bytes cannot reach the cell; the read grows, bounded, until it can
        self.assertIsNotNone(doc["rounds"])
        self.assertGreater(len(doc["tail_attempts"]), 1)
        self.assertAlmostEqual(doc["max_skew_ms"], 222.0, places=1)


class TestLaunchSkewIsNotAGate(unittest.TestCase):
    def test_the_receipt_carries_the_field(self):
        self.assertIn("launch_skew", runner.RECEIPT_FIELDS)
        self.assertIsNone(runner.blank_receipt()["launch_skew"])

    def test_no_gate_is_named_for_the_instrument(self):
        # Gate 8 exists now, but it is the THERMAL gate. Nothing reads launch_skew.
        self.assertEqual(len(runner.GATES), 8)
        self.assertEqual(runner.GATES[7], (8, "thermal"))
        self.assertNotIn("launch_skew", [name for _, name in runner.GATES])

    def test_a_delayed_round_changes_no_gate_outcome(self):
        clean = runner.blank_receipt()
        clean["launch_skew"] = {"rounds": [], "max_skew_ms": 0.2, "delayed_rounds": 0}
        delayed = runner.blank_receipt()
        delayed["launch_skew"] = {"rounds": [{"index": 0, "skew_ms": 222.0}],
                                  "max_skew_ms": 222.0, "delayed_rounds": 1}
        self.assertEqual(runner.gate_outcomes(clean), runner.gate_outcomes(delayed))


# ------------------------------------------ ArcServeRestart is STOP-ONLY (2026-09-09) --
def fake_clock(step: float = 1.0):
    """A monotonic clock that advances ``step`` seconds on every read."""
    state = {"t": 0.0}

    def clock():
        now = state["t"]
        state["t"] += step
        return now
    return clock


class TestPortListening(unittest.TestCase):
    def test_a_refused_connection_is_not_listening(self):
        def refuse(address, timeout):
            raise OSError("connection refused")
        self.assertFalse(runner.port_listening(8082, connect=refuse))

    def test_an_accepted_connection_is_listening_and_the_probe_socket_is_closed(self):
        closed = []

        class Sock:
            def close(self):
                closed.append(True)

        seen = {}

        def accept(address, timeout):
            seen["address"], seen["timeout"] = address, timeout
            return Sock()

        self.assertTrue(runner.port_listening(8082, connect=accept))
        self.assertEqual(seen["address"], ("127.0.0.1", 8082))
        self.assertEqual(closed, [True])

    def test_the_default_probe_is_a_real_socket_and_is_never_reached_here(self):
        # The other way round: no injection, so the default must be socket.create_connection.
        # Patched to refuse, so this test still touches no network.
        with mock.patch.object(runner.socket, "create_connection",
                               side_effect=OSError("refused")) as fake:
            self.assertFalse(runner.port_listening(9, timeout=0.25))
        fake.assert_called_once_with(("127.0.0.1", 9), 0.25)


class TestWaitForStop(unittest.TestCase):
    def test_the_bound_covers_both_lifecycle_ports(self):
        # llama-swap owns 8081 and the production upstream answers on 8082 (ADR-0045).
        self.assertIn(runner.PRODUCTION_PORT, runner.STOP_PORTS)
        self.assertIn(8081, runner.STOP_PORTS)
        self.assertEqual(runner.STOP_TIMEOUT_S, 60.0)

    def test_an_already_quiet_rung_stops_on_the_first_poll(self):
        doc = runner.wait_for_stop(ports=(8081, 8082), probe=lambda port: False,
                                   sleep=lambda s: None, clock=lambda: 0.0)
        self.assertTrue(doc["stopped"])
        self.assertEqual(doc["polls"], 1)
        self.assertEqual(doc["still_listening"], [])

    def test_it_keeps_polling_until_the_ports_go_quiet(self):
        seen = {"n": 0}

        def probe(port):
            seen["n"] += 1
            return seen["n"] <= 4          # two full rounds of both ports, then silence

        slept = []
        doc = runner.wait_for_stop(ports=(8081, 8082), probe=probe, sleep=slept.append,
                                   clock=fake_clock(1.0))
        self.assertTrue(doc["stopped"])
        self.assertEqual(doc["polls"], 3)
        self.assertEqual(slept, [runner.STOP_POLL_S, runner.STOP_POLL_S])

    def test_a_stop_that_never_lands_times_out_loudly(self):
        doc = runner.wait_for_stop(ports=(8082,), timeout_s=5.0, probe=lambda port: True,
                                   sleep=lambda s: None, clock=fake_clock(2.0))
        self.assertFalse(doc["stopped"])
        self.assertEqual(doc["still_listening"], [8082])
        self.assertIn("did NOT take effect", doc["detail"])
        self.assertLessEqual(doc["waited_s"], 10.0)


class FakeRun:
    """Records every command line ``set_np`` hands to ``subprocess.run``."""

    def __init__(self, failures=()):
        self.calls: list = []
        self.failures = tuple(failures)

    def __call__(self, argv, **kwargs):
        text = " ".join(str(a) for a in argv)
        self.calls.append(text)
        rc = 1 if any(needle in text for needle in self.failures) else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

    def index_of(self, needle: str) -> int:
        for i, text in enumerate(self.calls):
            if needle in text:
                return i
        return -1


@contextlib.contextmanager
def set_np_env(*, ready=True, stopped=True, sentinel_before=False,
               sentinel_during_stop=False, failures=()):
    """``set_np`` with every outside effect replaced. No schtasks, no rung, no network."""
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        root = Path(tmp)
        yaml_path = root / "omen.yaml"
        yaml_path.write_text(YAML_FIXTURE, encoding="utf-8", newline="")
        sentinel = root / "arc-maintenance.stop"
        if sentinel_before:
            sentinel.write_text("held", encoding="utf-8")
        baselines = root / "rate-baselines.json"
        baselines.write_text(json.dumps({"rungs": {"omen-arc": {
            "baseline_decode_tok_s": 108.0}}, "epoch_boundaries": []}), encoding="utf-8")
        run = FakeRun(failures)

        def fake_wait_for_stop(*a, **kw):
            if sentinel_during_stop:
                sentinel.write_text("taken by imagegen while we were stopping",
                                    encoding="utf-8")
            return {"stopped": stopped, "ports": list(runner.STOP_PORTS),
                    "still_listening": [] if stopped else [runner.PRODUCTION_PORT],
                    "polls": 1, "waited_s": 0.0, "timeout_s": runner.STOP_TIMEOUT_S,
                    "detail": "fixture stop: stopped=%s" % stopped}

        stack.enter_context(mock.patch.object(runner.subprocess, "run", run))
        stack.enter_context(mock.patch.object(runner, "OMEN_YAML", yaml_path))
        stack.enter_context(mock.patch.object(runner, "MAINTENANCE_STOP", sentinel))
        stack.enter_context(mock.patch.object(runner, "BASELINES", baselines))
        stack.enter_context(mock.patch.object(runner, "wait_for_stop", fake_wait_for_stop))
        stack.enter_context(mock.patch.object(runner.ff_cell, "wait_for_ready",
                                              lambda **kw: ready))
        stack.enter_context(mock.patch.object(
            runner, "_health_and_completion",
            lambda: (True, {"predicted_per_second": 100.0, "prompt_per_second": 1500.0})))
        stack.enter_context(mock.patch.object(
            runner.ff_ratecheck, "measure",
            lambda rung, reps: measurement([100.0, 100.5, 100.2])))
        args = runner.build_parser().parse_args(
            ["--set-np", "4", "--live", "--out", str(root), "--no-ledger"])
        yield types.SimpleNamespace(run=run, args=args, yaml=yaml_path, sentinel=sentinel,
                                    baselines=baselines, root=root)


class TestSetNpStopThenBoot(unittest.TestCase):
    """2026-09-09: ``ArcServeRestart`` stopped production and nothing brought it back."""

    def test_the_boot_is_issued_and_it_is_issued_after_the_stop(self):
        with set_np_env() as env:
            record = runner.set_np(4, "py.exe", env.args)
            stop_at = env.run.index_of("schtasks /Run /TN ArcServeRestart")
            boot_at = env.run.index_of("schtasks /Run /TN ArcServeBoot")
            self.assertNotEqual(boot_at, -1, "ArcServeBoot was never issued")
            self.assertNotEqual(stop_at, -1, "the stop was never issued")
            self.assertLess(stop_at, boot_at)
        names = [s["step"] for s in record["steps"]]
        self.assertEqual(names[:6], ["preflight", "yaml", "stop", "wait_for_stop", "boot",
                                     "ready_marker"])

    def test_the_stop_step_says_it_is_stop_only_and_names_the_boot(self):
        with set_np_env() as env:
            record = runner.set_np(4, "py.exe", env.args)
        step = next(s for s in record["steps"] if s["step"] == "stop")
        self.assertIn("STOP-ONLY", step["detail"])
        self.assertIn("ArcServeBoot", step["detail"])
        self.assertEqual(step["task"], "ArcServeRestart")

    def test_the_stop_is_waited_for_before_the_boot_goes_out(self):
        order = []

        with set_np_env() as env:
            original = env.run.__call__

            def watch(argv, **kwargs):
                text = " ".join(str(a) for a in argv)
                if "ArcServeBoot" in text:
                    order.append("boot")
                if "ArcServeRestart" in text:
                    order.append("stop")
                return original(argv, **kwargs)

            with mock.patch.object(runner.subprocess, "run", watch):
                with mock.patch.object(runner, "wait_for_stop",
                                       lambda *a, **kw: (order.append("waited") or {
                                           "stopped": True, "ports": [], "polls": 1,
                                           "still_listening": [], "waited_s": 0.0,
                                           "timeout_s": 60.0, "detail": "fixture"})):
                    runner.set_np(4, "py.exe", env.args)
        self.assertEqual(order, ["stop", "waited", "boot"])

    def test_a_full_sequence_succeeds_and_the_yaml_really_moved(self):
        with set_np_env() as env:
            record = runner.set_np(4, "py.exe", env.args)
            self.assertIn("-np 4", env.yaml.read_text(encoding="utf-8"))
        self.assertTrue(record["ok"])
        self.assertFalse(record["production_may_be_down"])
        self.assertIsNone(record["recovery_command"])

    def test_a_sentinel_taken_while_we_were_stopping_aborts_before_the_boot(self):
        with set_np_env(sentinel_during_stop=True) as env:
            record = runner.set_np(4, "py.exe", env.args)
            self.assertEqual(env.run.index_of("ArcServeBoot"), -1,
                             "booted into a held maintenance lock")
            # A SHARED lock is never deleted on someone else's behalf.
            self.assertTrue(env.sentinel.exists())
        names = [s["step"] for s in record["steps"]]
        self.assertIn("abort_before_boot", names)
        self.assertNotIn("boot", names)
        self.assertFalse(record["ok"])
        self.assertTrue(record["production_may_be_down"])
        self.assertEqual(record["recovery_command"], runner.RECOVERY_COMMAND)
        abort = next(s for s in record["steps"] if s["step"] == "abort_before_boot")
        self.assertIn("NOT deleted here", abort["detail"])
        self.assertIn(runner.RECOVERY_COMMAND, abort["detail"])

    def test_a_sentinel_already_held_refuses_before_anything_is_touched(self):
        with set_np_env(sentinel_before=True) as env:
            record = runner.set_np(4, "py.exe", env.args)
            self.assertEqual(env.run.calls, [])
            self.assertIn("-np 2", env.yaml.read_text(encoding="utf-8"))
            self.assertTrue(env.sentinel.exists())
        self.assertEqual([s["step"] for s in record["steps"]], ["sentinel"])

    def test_a_ready_timeout_says_production_may_be_down_and_names_the_recovery(self):
        with set_np_env(ready=False) as env:
            record = runner.set_np(4, "py.exe", env.args)
        self.assertFalse(record["ok"])
        self.assertTrue(record["production_may_be_down"])
        self.assertEqual(record["recovery_command"], "schtasks /Run /TN ArcServeBoot")
        step = next(s for s in record["steps"] if s["step"] == "production_may_be_down")
        self.assertIn("PRODUCTION MAY BE DOWN", step["detail"])
        self.assertIn("schtasks /Run /TN ArcServeBoot", step["detail"])
        self.assertIn("8081", step["detail"])

    def test_a_stop_that_did_not_take_effect_is_recorded_as_not_ok(self):
        with set_np_env(stopped=False) as env:
            record = runner.set_np(4, "py.exe", env.args)
            self.assertNotEqual(env.run.index_of("ArcServeBoot"), -1)
        wait = next(s for s in record["steps"] if s["step"] == "wait_for_stop")
        self.assertFalse(wait["ok"])
        self.assertEqual(wait["wait"]["still_listening"], [runner.PRODUCTION_PORT])

    def test_the_docstring_says_the_sentinel_is_never_deleted_here(self):
        self.assertIn("NEVER DELETED HERE", runner.set_np.__doc__)


# --------------------------------------------------------------- gate 8: thermal --
def thermal_stream(path: Path, points_by_key: dict, extra_rows: list | None = None) -> Path:
    """A b70tools-shaped stream carrying only identity rows and temperature readings.

    ``points_by_key`` maps ``(adapter, counter)`` to ``[(t_ns, celsius), ...]``.
    """
    lines = [
        json.dumps({"k": "ai", "a": BUS9, "desc": B70, "bdf": "0000:09:00.0"}),
        json.dumps({"k": "ai", "a": BUS4, "desc": B70, "bdf": "0000:04:00.0"}),
        json.dumps({"k": "ai", "a": "adapter_igpu", "desc": "Intel(R) Graphics",
                    "bdf": "0000:00:02.0"}),
    ]
    for (adapter, counter), points in points_by_key.items():
        for t, v in points:
            lines.append(json.dumps({"k": "ms", "a": adapter, "n": counter, "v": v, "t": t,
                                     "u": "Celsius", "d": "Thermal"}))
    lines.extend(extra_rows or [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


#: A 100 s capture: leading quarter 0-25 s, load window 30-70 s.
CAPTURE_WINDOW = (30 * NS, 70 * NS)
COOL_POINTS = {
    (BUS4, "vram.temperature_c"): [(0, 62), (5 * NS, 60), (35 * NS, 64), (60 * NS, 66),
                                   (90 * NS, 63)],
    (BUS4, "gpu.temperature_c"): [(0, 58), (5 * NS, 56), (35 * NS, 59), (60 * NS, 60),
                                  (90 * NS, 57)],
    (BUS9, "vram.temperature_c"): [(0, 60), (35 * NS, 62), (60 * NS, 62), (90 * NS, 58)],
    (BUS9, "gpu.temperature_c"): [(0, 57), (35 * NS, 58), (60 * NS, 59), (90 * NS, 56)],
    ("adapter_igpu", "gpu.temperature_c"): [(0, 45), (35 * NS, 99), (90 * NS, 44)],
}


class TestThermalSummary(unittest.TestCase):
    def _summary(self, points=None, window=CAPTURE_WINDOW):
        with tempfile.TemporaryDirectory() as tmp:
            path = thermal_stream(Path(tmp) / "events.jsonl",
                                  COOL_POINTS if points is None else points)
            return runner.thermal_summary(path, window)

    def test_idle_is_the_coolest_reading_in_the_leading_quarter(self):
        card = self._summary()["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(card["idle_c"], 60.0)      # min(62, 60), both before 25 s
        self.assertEqual(card["idle_samples"], 2)

    def test_busy_percentiles_and_delta_come_off_the_load_window(self):
        card = self._summary()["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(card["busy_samples"], 2)    # 35 s and 60 s; 90 s is outside
        self.assertEqual(card["busy_p50_c"], 64.0)
        self.assertEqual(card["busy_p95_c"], 66.0)
        self.assertEqual(card["delta_c"], 6.0)       # busy p95 - idle

    def test_max_is_the_hottest_reading_anywhere_in_the_capture(self):
        # A spike outside the load window is still a spike: the abort term must see it.
        points = dict(COOL_POINTS)
        points[(BUS4, "vram.temperature_c")] = [(0, 62), (35 * NS, 64), (90 * NS, 96)]
        card = self._summary(points)["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(card["max_c"], 96.0)
        self.assertEqual(card["busy_p95_c"], 64.0)

    def test_the_card_carries_its_bdf_so_the_report_can_name_it(self):
        cards = self._summary()["cards"]
        self.assertEqual(cards[BUS4]["bdf"], "0000:04:00.0")
        self.assertEqual(cards[BUS9]["bdf"], "0000:09:00.0")

    def test_the_igpu_is_not_part_of_the_board(self):
        self.assertNotIn("adapter_igpu", self._summary()["cards"])

    def test_an_absent_counter_is_a_loud_null_not_an_implied_cool(self):
        points = {k: v for k, v in COOL_POINTS.items() if k[1] != "vram.temperature_c"}
        summary = self._summary(points)
        entry = summary["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertIsNone(entry["max_c"])
        self.assertIsNone(entry["delta_c"])
        self.assertEqual(entry["samples"], 0)
        self.assertIn("stream carries no 'vram.temperature_c'", entry["reason"])
        self.assertNotIn("vram.temperature_c", summary["counters_present"])

    def test_a_counter_present_for_one_card_only_says_so_for_the_other(self):
        points = {k: v for k, v in COOL_POINTS.items()
                  if k != (BUS9, "vram.temperature_c")}
        entry = self._summary(points)["cards"][BUS9]["counters"]["vram.temperature_c"]
        self.assertIsNone(entry["max_c"])
        self.assertIn("no 'vram.temperature_c' samples for this adapter", entry["reason"])

    def test_a_single_sample_counter_is_that_sample_everywhere(self):
        points = dict(COOL_POINTS)
        points[(BUS9, "vram.temperature_c")] = [(40 * NS, 71)]
        entry = self._summary(points)["cards"][BUS9]["counters"]["vram.temperature_c"]
        self.assertEqual(entry["samples"], 1)
        self.assertEqual((entry["busy_p50_c"], entry["busy_p95_c"], entry["max_c"]),
                         (71.0, 71.0, 71.0))
        # No reading in the leading quarter, so there is no baseline and no delta.
        self.assertIsNone(entry["idle_c"])
        self.assertIsNone(entry["delta_c"])
        self.assertIn("no reading in the capture's leading quarter", entry["reason"])

    def test_no_sample_inside_the_window_is_a_stated_reason(self):
        points = dict(COOL_POINTS)
        points[(BUS9, "gpu.temperature_c")] = [(0, 57), (90 * NS, 58)]
        entry = self._summary(points)["cards"][BUS9]["counters"]["gpu.temperature_c"]
        self.assertEqual(entry["busy_samples"], 0)
        self.assertIsNone(entry["busy_p95_c"])
        self.assertIsNone(entry["delta_c"])
        self.assertIn("no 'gpu.temperature_c' sample inside the load window", entry["reason"])
        self.assertEqual(entry["max_c"], 58.0)      # the capture still saw readings

    def test_t_is_read_as_nanoseconds(self):
        # The same trap the duty cycle carries: reading t as 10 MHz ticks is wrong by 100x.
        summary = self._summary(window=(30 * NS, 70 * NS))
        self.assertEqual(summary["capture_ns"], [0, 90 * NS])
        self.assertEqual(summary["idle_window_ns"], [0, int(0.25 * 90 * NS)])
        self.assertIn("ns on the perf_counter clock", summary["timebase"])

    def test_without_a_window_the_busy_figures_say_they_cover_the_capture(self):
        entry = self._summary(window=None)["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(entry["busy_samples"], 5)
        self.assertIn("no load window given", entry["reason"])


class TestThermalGate(unittest.TestCase):
    """Gate 8's thresholds and its four outcomes."""

    def _scored(self, points=None, window=CAPTURE_WINDOW):
        with tempfile.TemporaryDirectory() as tmp:
            path = thermal_stream(Path(tmp) / "events.jsonl",
                                  COOL_POINTS if points is None else points)
            return runner.score_thermal(runner.thermal_summary(path, window))

    def _gate(self, scored):
        row = runner.blank_receipt()
        row["thermal"] = scored
        return next(g for g in runner.gate_outcomes(row) if g["name"] == "thermal")

    def test_the_abort_limit_is_the_number_derek_set(self):
        self.assertEqual(runner.VRAM_ABORT_C, 95.0)
        self.assertEqual(runner.GPU_ABORT_C, 95.0)
        self.assertEqual(runner.VRAM_WARN_C, 88.0)
        self.assertEqual(runner.GPU_WARN_C, 88.0)
        self.assertEqual(runner.DELTA_WARN_C, 15.0)

    def test_the_attribution_travels_with_the_number(self):
        # Whoever reads a receipt, or the constant, must see WHOSE call 95 C is before
        # "improving" it. Both the source comment and the receipt carry it.
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn("DEREK'S CALL", source.upper())
        self.assertIn("VRAM_ABORT_C = 95.0", source)
        self.assertIn("Derek", runner.THERMAL_ATTRIBUTION)
        self.assertIn("2026-09-09", runner.THERMAL_ATTRIBUTION)
        self.assertIn("Derek", runner.thermal_thresholds()["attribution"])

    def test_a_cool_cell_passes_and_the_detail_states_the_precedent_and_the_ambient(self):
        scored = self._scored()
        self.assertEqual(scored["outcome"], "pass")
        self.assertFalse(scored["exceeded"])
        gate = self._gate(scored)
        self.assertEqual(gate["outcome"], "pass")
        self.assertIn("abort 95 C", gate["detail"])
        self.assertIn("Derek", gate["detail"])
        self.assertIn("ambient", gate["detail"].lower())
        self.assertIn("VRAM p95 66.0 C (idle 60.0, delta 6.0", gate["detail"])
        self.assertIn("0000:04:00.0", gate["detail"])

    def test_a_hot_absolute_below_the_abort_warns(self):
        points = dict(COOL_POINTS)
        points[(BUS4, "vram.temperature_c")] = [(0, 62), (35 * NS, 89), (60 * NS, 90)]
        scored = self._scored(points)
        self.assertEqual(scored["outcome"], "warn")
        self.assertFalse(scored["exceeded"])
        entry = scored["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(entry["outcome"], "warn")
        self.assertIn("88 C warn line", entry["reason"])

    def test_a_big_rise_warns_even_when_the_absolute_is_comfortable(self):
        # The delta half: a cool room but the workload is heating the card.
        points = dict(COOL_POINTS)
        points[(BUS4, "vram.temperature_c")] = [(0, 50), (35 * NS, 66), (60 * NS, 70)]
        scored = self._scored(points)
        entry = scored["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual(entry["delta_c"], 20.0)
        self.assertEqual(entry["outcome"], "warn")
        self.assertIn("WORKLOAD", entry["reason"])
        self.assertEqual(scored["outcome"], "warn")

    def test_the_abort_limit_fails_the_cell_and_the_row_is_kept(self):
        points = dict(COOL_POINTS)
        # 96 C on the VRAM of 0000:04:00.0 -- the real quarantine precedent.
        points[(BUS4, "vram.temperature_c")] = [(0, 62), (35 * NS, 90), (60 * NS, 96)]
        scored = self._scored(points)
        self.assertEqual(scored["outcome"], "fail")
        self.assertTrue(scored["exceeded"])
        self.assertEqual(scored["cards"][BUS4]["outcome"], "fail")
        self.assertEqual(scored["cards"][BUS9]["outcome"], "pass")
        gate = self._gate(scored)
        self.assertEqual(gate["outcome"], "fail")
        self.assertIn("KEPT", gate["detail"])
        self.assertIn("never dropped", gate["detail"])

    def test_ninety_five_is_the_line_not_ninety_six(self):
        for peak, expected in ((94, "warn"), (95, "fail"), (96, "fail")):
            points = dict(COOL_POINTS)
            points[(BUS4, "gpu.temperature_c")] = [(0, 58), (60 * NS, peak)]
            with self.subTest(peak=peak):
                self.assertEqual(self._scored(points)["outcome"], expected)

    def test_an_unmeasured_cell_is_null_and_says_the_limit_was_never_evaluated(self):
        scored = self._scored({})
        self.assertEqual(scored["outcome"], "null")
        self.assertIsNone(scored["exceeded"])
        gate = self._gate(scored)
        self.assertEqual(gate["outcome"], "null")
        self.assertIn("never evaluated", gate["detail"])
        self.assertIn("Derek", gate["detail"])

    def test_a_receipt_with_no_thermal_block_at_all_is_null_not_a_pass(self):
        gate = next(g for g in runner.gate_outcomes(runner.blank_receipt())
                    if g["name"] == "thermal")
        self.assertEqual(gate["outcome"], "null")
        self.assertIn("never evaluated", gate["detail"])

    def test_the_thresholds_ride_along_in_the_receipt(self):
        scored = self._scored()
        self.assertEqual(scored["thresholds"]["vram.temperature_c"],
                         {"warn_c": 88.0, "abort_c": 95.0})
        self.assertEqual(scored["thresholds"]["delta_warn_c"], 15.0)
        entry = scored["cards"][BUS4]["counters"]["vram.temperature_c"]
        self.assertEqual((entry["warn_c"], entry["abort_c"]), (88.0, 95.0))

    def test_a_measurement_caveat_is_not_overwritten_by_the_score(self):
        points = dict(COOL_POINTS)
        points[(BUS9, "vram.temperature_c")] = [(40 * NS, 90)]   # no idle baseline, warn
        entry = self._scored(points)["cards"][BUS9]["counters"]["vram.temperature_c"]
        self.assertIn("no reading in the capture's leading quarter", entry["reason"])
        self.assertIn("88 C warn line", entry["reason"])


class TestThermalExceededKeepsTheRow(unittest.TestCase):
    """A too-hot cell is real data about a real cell -- exactly like ``over_admitted``."""

    def test_the_receipt_carries_both_fields(self):
        row = runner.blank_receipt()
        self.assertIn("thermal", row)
        self.assertIn("thermal_exceeded", row)
        self.assertIsNone(row["thermal"])
        self.assertIsNone(row["thermal_exceeded"])

    def test_the_row_is_written_whole_with_the_status_and_the_reason(self):
        args = runner.build_parser().parse_args(["--one-cell", "np2-p512-c2-r1", "--no-ledger"])
        with tempfile.TemporaryDirectory() as tmp:
            path = thermal_stream(Path(tmp) / "events.jsonl", {
                (BUS4, "vram.temperature_c"): [(0, 62), (35 * NS, 90), (60 * NS, 96)]})
            row = runner.blank_receipt()
            row.update({"probe": runner.PROBE, "cell": "np2-p512-c2-r1",
                        "jobs_per_hour": 2301.4})
            row["thermal"] = runner.score_thermal(
                runner.thermal_summary(path, CAPTURE_WINDOW))
            row["thermal_exceeded"] = row["thermal"]["exceeded"]
            cell_dir = Path(tmp) / "cell"
            runner._finish(row, cell_dir, args, "thermal_exceeded",
                           "%s; row kept, excluded from the surface" % row["thermal"]["detail"])
            written = json.loads((cell_dir / "receipt.json").read_text(encoding="utf-8"))
        # Kept whole: every field, the measured numbers intact, nothing nulled out.
        self.assertEqual(set(written), set(runner.RECEIPT_FIELDS))
        self.assertEqual(written["status"], "thermal_exceeded")
        self.assertTrue(written["thermal_exceeded"])
        self.assertEqual(written["jobs_per_hour"], 2301.4)
        self.assertIn("row kept", written["status_reason"])
        self.assertIn("95 C", written["status_reason"])
        gate = next(g for g in written["gates"] if g["gate"] == 8)
        self.assertEqual(gate["outcome"], "fail")


if __name__ == "__main__":
    unittest.main()
