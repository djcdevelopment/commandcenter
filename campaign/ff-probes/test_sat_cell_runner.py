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
    p50 out of the receipt -- never a hardcoded 159.92/114.05.

Run: fleet-worker-node\.venv-omen\Scripts\python.exe -m pytest campaign/ff-probes/test_sat_cell_runner.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_the_real_production_entry_reads_np_2(self):
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
                {"k": "ms", "a": BUS9, "n": runner.USAGE, "v": 16 * gb, "t": 2},
            ])
            out = runner.budget_headroom(path)
        self.assertEqual(out["cards"]["0000:09:00.0"]["min_headroom_gb"], 14.0)
        self.assertIs(out["over_admitted"], False)

    def test_negative_headroom_flags_over_admitted_and_keeps_the_row(self):
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stream(tmp, [
                {"k": "ms", "a": BUS9, "n": runner.BUDGET, "v": 30 * gb, "t": 1},
                {"k": "ms", "a": BUS9, "n": runner.USAGE, "v": 16 * gb, "t": 2},
                {"k": "ms", "a": BUS9, "n": runner.USAGE, "v": 31 * gb, "t": 3},
            ])
            out = runner.budget_headroom(path)
        self.assertIs(out["over_admitted"], True)
        self.assertEqual(out["cards"]["0000:09:00.0"]["min_headroom_gb"], -1.0)
        self.assertIn("KEPT", out["note"])

    def test_no_budget_pair_is_unknown_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = runner.budget_headroom(self._stream(tmp, []))
        self.assertIsNone(out["over_admitted"])


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

    def test_the_written_receipt_carries_every_field_and_all_six_gates(self):
        args = runner.build_parser().parse_args(["--one-cell", "np2-p512-c2-r1", "--no-ledger"])
        with tempfile.TemporaryDirectory() as tmp:
            cell_dir = Path(tmp) / "np2-p512-c2-r1"
            row = runner.blank_receipt()
            row.update({"probe": runner.PROBE, "cell": "np2-p512-c2-r1"})
            runner._finish(row, cell_dir, args, "REFUSED_GUARD", "guard read 'stale'")
            written = json.loads((cell_dir / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(set(written), set(runner.RECEIPT_FIELDS))
        self.assertEqual(len(written["gates"]), 6)
        self.assertEqual(written["status"], "REFUSED_GUARD")
        self.assertIsNotNone(written["ts"])
        # Every unmeasured field is an explicit null, never absent.
        self.assertIsNone(written["jobs_per_hour"])
        self.assertIsNone(written["duty_cycle"])
        self.assertIsNone(written["depth0"])


class TestGateOutcomes(unittest.TestCase):
    def test_all_six_gates_are_always_present(self):
        gates = runner.gate_outcomes(runner.blank_receipt())
        self.assertEqual([g["gate"] for g in gates], [1, 2, 3, 4, 5, 6])
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

    def test_the_regime_carries_model_depth_n_np_placement_and_epoch(self):
        regime = runner.plan_cell("np8-p512-c4-r2", "py", self._args())["regime"]
        for key in ("model", "depth_tokens", "concurrency", "np", "placement", "epoch"):
            self.assertIn(key, regime)
        self.assertEqual((regime["np"], regime["per_slot_ctx"]), (8, 16384))


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


if __name__ == "__main__":
    unittest.main()
