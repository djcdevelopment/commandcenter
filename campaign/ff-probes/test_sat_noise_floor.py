r"""Fixtures for the SAT-L1 noise-floor reducer. Pure offline: no rung, no GPU, no E:\.

Every case here is a place where the reducer could otherwise report a floor that is not a
floor:

  * a non-scored or over-admitted receipt must be EXCLUDED and LISTED -- a silent drop turns
    a bad block into a tight one;
  * spread and cv must be the population figures over the repeats that were actually
    present, with missing values counted rather than treated as zero;
  * the bootstrap must be deterministic for a seed and must bracket the mean it describes;
  * P7's two halves must be scored separately -- an already-warm rep-1 is ``untested``, never
    "supported", because a rung that never went cold cannot confirm a claim about cold rungs;
  * discovery must sort ``r10`` after ``r2`` (numeric, not lexical), or a tenth repeat
    silently reorders the table;
  * one repeat must EXIT 2 -- a "noise floor" over a single reading is not a floor.

Run: fleet-worker-node\.venv-omen\Scripts\python.exe -m pytest campaign/ff-probes/test_sat_noise_floor.py -q
"""
from __future__ import annotations

import io
import json
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sat_noise_floor as nf  # noqa: E402

BDF_A = "0000:04:00.0"
BDF_B = "0000:09:00.0"
ADAPTER_A = "adapter_00016def"
ADAPTER_B = "adapter_000171de"


def receipt(rep: int, *, jobs_per_hour=2300.0, status="scored", over_admitted=False,
            unwarmed=105.0, warm=105.0, runner_commit="abc1234", latency_p95=3.16,
            slot_busy=0.6842, duty_a=0.0, duty_b=0.0, headroom_a=16.0,
            omit=(), **extra) -> dict:
    """A receipt shaped like ``sat_cell_runner``'s, trimmed to what the reducer reads."""
    row = {
        "schema_version": 1,
        "probe": "SAT-L1",
        "cell": "np2-p512-c2-r%d" % rep,
        "status": status,
        "status_reason": "cell completed with the incumbent healthy before and after",
        "runner_commit": runner_commit,
        "regime": {"model": "qwen3-30b-a3b", "quant": "Q4_K_M", "depth_tokens": 512,
                   "concurrency": 2, "np": 2, "ctx": 131072, "placement": "both-b70",
                   "repeat": rep},
        "jobs_per_hour": jobs_per_hour,
        "latency_p50_s": 3.14,
        "latency_p95_s": latency_p95,
        "latency_p99_s": latency_p95,
        "ttft_p50_s": 0.0534,
        "ttft_p95_s": 0.0826,
        "decode_rate_p50_tokens_per_s": 64.19,
        "slot_busy_fraction": slot_busy,
        "both_slots_busy_fraction": 0.5263,
        "incumbent_rate_fraction_pre": 0.9934,
        "incumbent_rate_fraction_post": 0.9639,
        "over_admitted": over_admitted,
        # real-prefill regime by default (gate 7 era); block-1 receipts omit both keys
        "cache_prompt": False,
        "prefill_cached": False,
        "load_requests": 6,
        "ts": "2026-09-09T03:34:05-07:00",
        "warm": {"unwarmed_rep1_tok_s": unwarmed, "final_decode_tok_s": warm,
                 "iterations_used": 1, "flat": True},
        "symmetry": {"ratio": 0.996},
        "guard_before": {"verdict": "at_rate"},
        "guard_after": {"verdict": "at_rate"},
        "duty_cycle": {"cards": {BDF_A: {"duty_cycle": duty_a, "reference_p50_w": 114.05},
                                 BDF_B: {"duty_cycle": duty_b, "reference_p50_w": 159.92}}},
        "power": {"cards": {ADAPTER_A: {"burst": {"p50_w": 73.64}},
                            ADAPTER_B: {"burst": {"p50_w": 73.67}}}},
        "budget_headroom": {"cards": {BDF_A: {"min_headroom_gb": headroom_a},
                                      BDF_B: {"min_headroom_gb": 15.019}}},
    }
    for key in omit:
        row.pop(key, None)
    row.update(extra)
    return row


def reduce(receipts, **kw):
    kw.setdefault("resamples", 200)
    return nf.reduce_repeats(receipts, **kw)


def write_cells(root: Path, reps, **kw) -> None:
    """Lay out ``<root>/np2-p512-c2-r<k>/receipt.json`` for each rep in ``reps``."""
    for rep in reps:
        directory = root / ("np2-p512-c2-r%d" % rep)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "receipt.json").write_text(
            json.dumps(receipt(rep, **kw)), encoding="utf-8")


def run_cli(argv):
    """Run ``main`` capturing stdout/stderr. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = nf.main(argv)
    return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------- the include rule --
class TestInclusion(unittest.TestCase):
    def test_a_non_scored_receipt_is_excluded_and_listed(self):
        rows = [receipt(1), receipt(2),
                receipt(3, status="REFUSED_GUARD",
                        status_reason="production read degraded before the cell")]
        doc = reduce(rows)
        self.assertEqual(doc["n_included"], 2)
        self.assertEqual(doc["n_excluded"], 1)
        (excluded,) = doc["excluded"]
        self.assertEqual(excluded["repeat"], "np2-p512-c2-r3")
        self.assertIn("REFUSED_GUARD", excluded["reason"])
        self.assertIn("degraded", excluded["reason"])
        self.assertNotIn("np2-p512-c2-r3", doc["repeats"])

    def test_an_over_admitted_receipt_is_kept_and_excluded(self):
        rows = [receipt(1), receipt(2), receipt(3, over_admitted=True)]
        doc = reduce(rows)
        self.assertEqual(doc["n_included"], 2)
        (excluded,) = doc["excluded"]
        self.assertEqual(excluded["repeat"], "np2-p512-c2-r3")
        self.assertTrue(excluded["over_admitted"])
        self.assertIn("over_admitted", excluded["reason"])

    def test_exclusions_reach_the_markdown(self):
        rows = [receipt(1), receipt(2), receipt(3, over_admitted=True)]
        doc = reduce(rows)
        text = nf.render_markdown(doc, cell_prefix="np2-p512-c2")
        self.assertIn("repeats excluded: **1**", text)
        self.assertIn("np2-p512-c2-r3", text.split("## Per-field")[0])

    def test_nothing_included_is_not_a_crash(self):
        doc = reduce([receipt(1, status="STOPPED_AFTER_CELL")])
        self.assertEqual(doc["n_included"], 0)
        self.assertIsNone(doc["regime"])
        self.assertEqual(doc["fields"]["jobs_per_hour"]["n"], 0)
        self.assertIsNone(doc["fields"]["jobs_per_hour"]["spread_pct"])

    # -- regime: the surface is real prefill; block-1 (cached-prefix) receipts stay out --
    def test_a_block1_receipt_without_cache_prompt_is_excluded_by_default(self):
        rows = [receipt(1), receipt(2), receipt(3, omit=("cache_prompt", "prefill_cached"))]
        doc = reduce(rows)
        self.assertEqual(doc["n_included"], 2)
        (excluded,) = doc["excluded"]
        self.assertEqual(excluded["repeat"], "np2-p512-c2-r3")
        self.assertIn("cached-prefix regime", excluded["reason"])
        self.assertIn("--include-cached", excluded["reason"])
        self.assertIn("real-prefill regime only", doc["regime_filter"])

    def test_cache_prompt_true_is_the_cached_regime_too(self):
        doc = reduce([receipt(1), receipt(2), receipt(3, cache_prompt=True)])
        self.assertEqual(doc["n_included"], 2)
        self.assertIn("cached-prefix regime", doc["excluded"][0]["reason"])

    def test_gate7_prefill_cached_excludes_even_with_cache_prompt_false(self):
        doc = reduce([receipt(1), receipt(2), receipt(3, prefill_cached=True)])
        self.assertEqual(doc["n_included"], 2)
        (excluded,) = doc["excluded"]
        self.assertIn("prefill_cached", excluded["reason"])
        self.assertIn("gate 7", excluded["reason"])

    def test_include_cached_reduces_block1_on_purpose(self):
        rows = [receipt(k, omit=("cache_prompt", "prefill_cached")) for k in (1, 2, 3)]
        self.assertEqual(reduce(rows)["n_included"], 0)
        doc = reduce(rows, include_cached=True)
        self.assertEqual(doc["n_included"], 3)
        self.assertEqual(doc["n_excluded"], 0)
        self.assertIn("included on request", doc["regime_filter"])

    def test_include_cached_never_admits_non_scored_or_over_admitted(self):
        rows = [receipt(1, omit=("cache_prompt",)),
                receipt(2, omit=("cache_prompt",), over_admitted=True),
                receipt(3, omit=("cache_prompt",), status="STOPPED_AFTER_CELL")]
        doc = reduce(rows, include_cached=True)
        self.assertEqual(doc["n_included"], 1)
        self.assertEqual(doc["n_excluded"], 2)

    def test_cli_include_cached_flag_reaches_the_reduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, (1, 2, 3), omit=("cache_prompt", "prefill_cached"))
            rc, out, _ = run_cli(["--cells-root", str(root), "--cell-prefix", "np2-p512-c2",
                                  "--resamples", "50"])
            self.assertEqual(rc, 2)          # every receipt excluded -> fewer than 2 repeats
            self.assertIn("repeats excluded: **3**", out)
            rc, out, _ = run_cli(["--cells-root", str(root), "--cell-prefix", "np2-p512-c2",
                                  "--resamples", "50", "--include-cached"])
            self.assertEqual(rc, 0)
            self.assertIn("repeats included: **3**", out)


# ----------------------------------------------------------------------- the statistics --
class TestSpreadAndCv(unittest.TestCase):
    def test_known_values(self):
        rows = [receipt(1, jobs_per_hour=90.0), receipt(2, jobs_per_hour=100.0),
                receipt(3, jobs_per_hour=110.0)]
        field = reduce(rows)["fields"]["jobs_per_hour"]
        self.assertEqual(field["n"], 3)
        self.assertEqual(field["min"], 90.0)
        self.assertEqual(field["max"], 110.0)
        self.assertEqual(field["mean"], 100.0)
        self.assertEqual(field["median"], 100.0)
        # (110 - 90) / 100 * 100
        self.assertEqual(field["spread_pct"], 20.0)
        # population stddev of {90,100,110} is sqrt(200/3) = 8.16497
        self.assertEqual(field["cv_pct"], 8.16)

    def test_a_zero_mean_gives_null_not_a_division_error(self):
        rows = [receipt(1, duty_a=0.0), receipt(2, duty_a=0.0)]
        field = reduce(rows)["fields"]["duty_cycle[%s]" % BDF_A]
        self.assertEqual(field["mean"], 0.0)
        self.assertIsNone(field["spread_pct"])
        self.assertIsNone(field["cv_pct"])

    def test_a_missing_value_is_counted_not_zeroed(self):
        rows = [receipt(1, jobs_per_hour=100.0), receipt(2, jobs_per_hour=None),
                receipt(3, jobs_per_hour=110.0)]
        field = reduce(rows)["fields"]["jobs_per_hour"]
        self.assertEqual(field["n"], 2)
        self.assertEqual(field["missing"], 1)
        self.assertEqual(field["mean"], 105.0)

    def test_a_structured_slot_busy_fraction_is_skipped_and_said_so(self):
        rows = [receipt(1), receipt(2, slot_busy={"any": 0.84, "all": 0.52})]
        field = reduce(rows)["fields"]["slot_busy_fraction_span"]
        self.assertTrue(field["skipped"])
        self.assertIn("np2-p512-c2-r2", field["skip_reason"])
        self.assertIn("dict", field["skip_reason"])
        self.assertIsNone(field["mean"])
        # a sibling scalar field is untouched by the skip
        self.assertEqual(reduce(rows)["fields"]["both_slots_busy_fraction_span"]["n"], 2)

    # -- the load-window pair is the gate-4 term; the span pair is a different measurement --
    def test_the_load_window_pair_is_reported_separately_from_the_span_pair(self):
        rows = [receipt(k, both_slots_busy_fraction_load_window=1.0,
                        slot_busy_fraction_load_window=1.0) for k in (1, 2)]
        fields = reduce(rows)["fields"]
        self.assertEqual(fields["both_slots_busy_fraction_load_window"]["mean"], 1.0)
        self.assertEqual(fields["slot_busy_fraction_load_window"]["mean"], 1.0)
        # the span figures are the diluted ones and are kept, not replaced
        self.assertEqual(fields["both_slots_busy_fraction_span"]["mean"], 0.5263)

    def test_a_receipt_without_load_window_fields_reports_them_as_missing(self):
        fields = reduce([receipt(1), receipt(2)])["fields"]
        lw = fields["both_slots_busy_fraction_load_window"]
        self.assertEqual(lw["n"], 0)
        self.assertEqual(lw["missing"], 2)
        self.assertIsNone(lw["mean"])
        # ...while the span pair still reduces, so the report is never blank
        self.assertEqual(fields["both_slots_busy_fraction_span"]["n"], 2)

    def test_per_card_fields_are_keyed_by_their_own_identifier(self):
        fields = reduce([receipt(1), receipt(2)])["fields"]
        self.assertIn("duty_cycle[%s]" % BDF_A, fields)
        self.assertIn("duty_cycle[%s]" % BDF_B, fields)
        self.assertIn("power.burst_p50_w[%s]" % ADAPTER_A, fields)
        self.assertIn("min_headroom_gb[%s]" % BDF_B, fields)
        self.assertEqual(fields["min_headroom_gb[%s]" % BDF_A]["mean"], 16.0)

    def test_symmetry_ratio_is_reached_through_its_nesting(self):
        field = reduce([receipt(1), receipt(2)])["fields"]["symmetry.ratio"]
        self.assertEqual(field["n"], 2)
        self.assertEqual(field["mean"], 0.996)


# ------------------------------------------------------------------------- the bootstrap --
class TestBootstrap(unittest.TestCase):
    def test_deterministic_for_a_seed(self):
        rows = [receipt(1, jobs_per_hour=2280.0), receipt(2, jobs_per_hour=2300.0),
                receipt(3, jobs_per_hour=2340.0)]
        a = nf.reduce_repeats(rows, seed=20260909, resamples=2000)["bootstrap"]["jobs_per_hour"]
        b = nf.reduce_repeats(rows, seed=20260909, resamples=2000)["bootstrap"]["jobs_per_hour"]
        self.assertEqual(a, b)

    def test_the_seed_actually_moves_the_ci(self):
        """At n=3 it CANNOT: P(all-min resample) = 1/27 > 2.5%, so both tails pin to the
        extremes for every seed. That is a property of a 3-repeat bootstrap, not
        determinism -- so seed sensitivity is asserted at n=6, where the tails are free."""
        three = [receipt(i, jobs_per_hour=jph) for i, jph in
                 enumerate((2280.0, 2300.0, 2340.0), start=1)]
        pinned = [nf.reduce_repeats(three, seed=s, resamples=500)["bootstrap"]["jobs_per_hour"]
                  for s in (1, 20260909)]
        self.assertEqual((pinned[0]["lo"], pinned[0]["hi"]), (2280.0, 2340.0))
        self.assertEqual((pinned[1]["lo"], pinned[1]["hi"]), (2280.0, 2340.0))

        six = [receipt(i, jobs_per_hour=jph) for i, jph in
               enumerate((2200.0, 2260.0, 2290.0, 2310.0, 2360.0, 2420.0), start=1)]
        free = [nf.reduce_repeats(six, seed=s, resamples=300)["bootstrap"]["jobs_per_hour"]
                for s in (1, 20260909)]
        self.assertNotEqual((free[0]["lo"], free[0]["hi"]), (free[1]["lo"], free[1]["hi"]))

    def test_the_ci_brackets_the_mean(self):
        rows = [receipt(1, jobs_per_hour=2280.0), receipt(2, jobs_per_hour=2300.0),
                receipt(3, jobs_per_hour=2340.0)]
        ci = nf.reduce_repeats(rows, seed=20260909, resamples=4000)["bootstrap"]["jobs_per_hour"]
        self.assertAlmostEqual(ci["mean"], (2280.0 + 2300.0 + 2340.0) / 3)
        self.assertLessEqual(ci["lo"], ci["mean"])
        self.assertGreaterEqual(ci["hi"], ci["mean"])
        self.assertGreaterEqual(ci["lo"], 2280.0)
        self.assertLessEqual(ci["hi"], 2340.0)

    def test_identical_repeats_give_a_degenerate_ci(self):
        rows = [receipt(1, jobs_per_hour=100.0), receipt(2, jobs_per_hour=100.0)]
        ci = reduce(rows)["bootstrap"]["jobs_per_hour"]
        self.assertEqual((ci["lo"], ci["hi"]), (100.0, 100.0))

    def test_one_repeat_gets_no_ci_and_says_why(self):
        ci = reduce([receipt(1)])["bootstrap"]["jobs_per_hour"]
        self.assertIsNone(ci["lo"])
        self.assertIn("fewer than 2", ci["reason"])


# ---------------------------------------------------------------------- guards, commits --
class TestGuardsAndProvenance(unittest.TestCase):
    def test_all_at_rate(self):
        doc = reduce([receipt(1), receipt(2)])
        self.assertTrue(doc["guards"]["all_at_rate"])
        self.assertEqual(len(doc["guards"]["rows"]), 2)

    def test_a_warn_after_breaks_all_at_rate(self):
        rows = [receipt(1), receipt(2, guard_after={"verdict": "degraded"})]
        doc = reduce(rows)
        self.assertFalse(doc["guards"]["all_at_rate"])
        self.assertEqual(doc["guards"]["rows"][1]["guard_after"], "degraded")

    def test_mixed_runner_commits_are_flagged(self):
        same = reduce([receipt(1), receipt(2)])
        self.assertEqual(same["runner_commits"], ["abc1234"])
        self.assertFalse(same["mixed_runner_commits"])
        mixed = reduce([receipt(1, runner_commit="d18ab09"),
                        receipt(2, runner_commit="b982a1e")])
        self.assertEqual(mixed["runner_commits"], ["d18ab09", "b982a1e"])
        self.assertTrue(mixed["mixed_runner_commits"])

    def test_regime_comes_from_the_first_included_receipt(self):
        rows = [receipt(1, status="REFUSED_GUARD"), receipt(2), receipt(3)]
        doc = reduce(rows)
        self.assertEqual(doc["regime"]["repeat"], 2)

    def test_prereg_repeat_count_is_reported(self):
        self.assertFalse(reduce([receipt(i) for i in (1, 2, 3)])["meets_prereg_repeats"])
        self.assertTrue(reduce([receipt(i) for i in range(1, 6)])["meets_prereg_repeats"])


# ------------------------------------------------------------------------------ P7 halves --
class TestP7RepeatSpread(unittest.TestCase):
    def test_within_the_floor(self):
        # 1% spread against the 1.5% pp512 floor
        rows = [receipt(1, jobs_per_hour=99.5), receipt(2, jobs_per_hour=100.5)]
        half = reduce(rows)["p7"]["repeat_spread"]
        self.assertEqual(half["observed_spread_pct"], 1.0)
        self.assertEqual(half["floor_pct"], 1.5)
        self.assertTrue(half["within_floor"])
        self.assertEqual(half["outcome"], "supported")

    def test_beyond_the_floor(self):
        rows = [receipt(1, jobs_per_hour=95.0), receipt(2, jobs_per_hour=105.0)]
        half = reduce(rows)["p7"]["repeat_spread"]
        self.assertEqual(half["observed_spread_pct"], 10.0)
        self.assertFalse(half["within_floor"])
        self.assertEqual(half["outcome"], "refuted")

    def test_the_floor_is_overridable(self):
        rows = [receipt(1, jobs_per_hour=95.0), receipt(2, jobs_per_hour=105.0)]
        half = reduce(rows, floors={"pp512": 12.0})["p7"]["repeat_spread"]
        self.assertTrue(half["within_floor"])
        self.assertEqual(half["floor_pct"], 12.0)

    def test_the_note_says_the_comparison_is_not_like_for_like(self):
        half = reduce([receipt(1), receipt(2)])["p7"]["repeat_spread"]
        self.assertIn("SINGLE-STREAM", half["note"])
        self.assertIn("CONCURRENT", half["note"])
        self.assertIn("6 request", half["note"])

    def test_latency_p95_spread_is_reported_alongside(self):
        rows = [receipt(1, latency_p95=3.0), receipt(2, latency_p95=3.3)]
        half = reduce(rows)["p7"]["repeat_spread"]
        self.assertAlmostEqual(half["latency_p95_spread_pct"], 9.52, places=2)

    def test_the_outcome_is_never_unclear(self):
        for jph in (100.0, 200.0):
            rows = [receipt(1, jobs_per_hour=100.0), receipt(2, jobs_per_hour=jph)]
            self.assertIn(reduce(rows)["p7"]["repeat_spread"]["outcome"],
                          ("supported", "refuted", "untested"))


class TestP7UnwarmedRep1(unittest.TestCase):
    def test_untested_when_every_rep1_was_already_warm(self):
        rows = [receipt(1, unwarmed=104.13, warm=104.68),
                receipt(2, unwarmed=105.95, warm=106.13),
                receipt(3, unwarmed=106.02, warm=106.0)]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertEqual(half["outcome"], "untested")
        self.assertIn("already warm at every repeat", half["reason"])
        self.assertIn("back-to-back", half["reason"])
        self.assertTrue(all(r["already_warm"] for r in half["ratios"]))

    def test_supported_for_a_ratio_of_080(self):
        rows = [receipt(1, unwarmed=80.0, warm=100.0), receipt(2, unwarmed=105.0, warm=105.0)]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertEqual(half["ratios"][0]["ratio"], 0.8)
        self.assertTrue(half["ratios"][0]["in_band"])
        self.assertFalse(half["ratios"][0]["already_warm"])
        self.assertEqual(half["outcome"], "supported")

    def test_refuted_for_a_ratio_of_050(self):
        rows = [receipt(1, unwarmed=50.0, warm=100.0), receipt(2, unwarmed=80.0, warm=100.0)]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertEqual(half["ratios"][0]["ratio"], 0.5)
        self.assertFalse(half["ratios"][0]["in_band"])
        self.assertEqual(half["outcome"], "refuted")

    def test_a_ratio_above_the_band_but_below_warm_also_refutes(self):
        # 0.93: cold enough to be scored, too fast to sit in ADR-0043's 65-90 band
        rows = [receipt(1, unwarmed=93.0, warm=100.0), receipt(2, unwarmed=105.0, warm=105.0)]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertEqual(half["outcome"], "refuted")

    def test_a_missing_warm_block_is_untested_not_a_crash(self):
        rows = [receipt(1, omit=("warm",)), receipt(2, omit=("warm",))]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertEqual(half["outcome"], "untested")
        self.assertIsNone(half["ratios"][0]["ratio"])

    def test_a_zero_warm_rate_does_not_divide(self):
        rows = [receipt(1, unwarmed=80.0, warm=0.0), receipt(2)]
        half = reduce(rows)["p7"]["unwarmed_rep1"]
        self.assertIsNone(half["ratios"][0]["ratio"])
        self.assertEqual(half["outcome"], "untested")


# -------------------------------------------------------------------------- discovery --
class TestDiscovery(unittest.TestCase):
    def test_r10_sorts_after_r2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, [1, 2, 3, 10])
            paths, skipped = nf.discover_receipts(root, "np2-p512-c2")
            self.assertEqual(skipped, [])
            names = [p.parent.name for p in paths]
            self.assertEqual(names, ["np2-p512-c2-r1", "np2-p512-c2-r2",
                                     "np2-p512-c2-r3", "np2-p512-c2-r10"])

    def test_a_directory_without_a_receipt_is_listed_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, [1, 2])
            (root / "np2-p512-c2-r4").mkdir()
            paths, skipped = nf.discover_receipts(root, "np2-p512-c2")
            self.assertEqual(len(paths), 2)
            self.assertEqual(len(skipped), 1)
            self.assertIn("np2-p512-c2-r4", skipped[0]["path"])
            self.assertIn("no receipt.json", skipped[0]["reason"])

    def test_another_prefix_is_not_swept_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, [1, 2])
            (root / "np2-p512-c16-r1").mkdir()
            paths, _ = nf.discover_receipts(root, "np2-p512-c2")
            self.assertEqual(len(paths), 2)

    def test_an_unreadable_receipt_is_listed_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, [1, 2])
            bad = root / "np2-p512-c2-r3"
            bad.mkdir()
            (bad / "receipt.json").write_text("{not json", encoding="utf-8")
            paths, _ = nf.discover_receipts(root, "np2-p512-c2")
            receipts, skipped = nf.load_receipts(paths)
            self.assertEqual(len(receipts), 2)
            self.assertEqual(len(skipped), 1)
            self.assertIn("unreadable", skipped[0]["reason"])

    def test_a_missing_root_is_reported(self):
        paths, skipped = nf.discover_receipts(Path(tempfile.gettempdir()) / "no-such-sat-root",
                                              "np2-p512-c2")
        self.assertEqual(paths, [])
        self.assertIn("not a directory", skipped[0]["reason"])


# --------------------------------------------------------------------------------- CLI --
class TestCli(unittest.TestCase):
    def test_a_full_run_exits_zero_and_prints_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cells(Path(tmp), [1, 2, 3])
            rc, out, _ = run_cli(["--cells-root", tmp, "--cell-prefix", "np2-p512-c2"])
            self.assertEqual(rc, 0)
            self.assertIn("# SAT-L1 noise floor -- np2-p512-c2", out)
            self.assertIn("repeats included: **3**", out)
            self.assertIn("| `jobs_per_hour` |", out)
            self.assertIn("95% CI", out)
            self.assertIn("half 1 -- repeat spread", out)
            self.assertIn("half 2 -- unwarmed rep-1", out)
            self.assertNotIn("%%", out)

    def test_a_single_receipt_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cells(Path(tmp), [1])
            rc, out, err = run_cli(["--cells-root", tmp, "--cell-prefix", "np2-p512-c2"])
            self.assertEqual(rc, 2)
            self.assertIn("repeats included: **1**", out)
            self.assertIn("FEWER THAN 2 REPEATS", err)

    def test_explicit_receipt_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cells(Path(tmp), [1, 2])
            args = []
            for rep in (1, 2):
                args += ["--receipt", str(Path(tmp) / ("np2-p512-c2-r%d" % rep) / "receipt.json")]
            rc, out, _ = run_cli(args)
            self.assertEqual(rc, 0)
            # the prefix is inferred from the receipts' own cell ids
            self.assertIn("# SAT-L1 noise floor -- np2-p512-c2", out)

    def test_no_selection_exits_two(self):
        rc, _, err = run_cli([])
        self.assertEqual(rc, 2)
        self.assertIn("--cells-root", err)

    def test_json_out_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cells"
            root.mkdir()
            write_cells(root, [1, 2, 3])
            out_path = Path(tmp) / "nested" / "noise-floor.json"
            rc, _, _ = run_cli(["--cells-root", str(root), "--cell-prefix", "np2-p512-c2",
                                "--json-out", str(out_path), "--seed", "7",
                                "--resamples", "500"])
            self.assertEqual(rc, 0)
            doc = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["probe"], nf.PROBE)
            self.assertEqual(doc["cell_prefix"], "np2-p512-c2")
            self.assertEqual(len(doc["receipt_paths"]), 3)
            reduction = doc["reduction"]
            self.assertEqual(reduction["n_included"], 3)
            self.assertEqual(reduction["bootstrap"]["jobs_per_hour"]["seed"], 7)
            self.assertEqual(reduction["bootstrap"]["jobs_per_hour"]["resamples"], 500)
            # the JSON is the same document the pure reducer produces
            paths, _ = nf.discover_receipts(root, "np2-p512-c2")
            receipts, _ = nf.load_receipts(paths)
            expected = nf.reduce_repeats(receipts, seed=7, resamples=500)
            self.assertEqual(reduction, expected)
            # and it re-renders to the same markdown
            self.assertTrue(nf.render_markdown(doc, cell_prefix="np2-p512-c2"))

    def test_floor_pct_override_reaches_the_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rep, jph in ((1, 95.0), (2, 105.0)):
                directory = root / ("np2-p512-c2-r%d" % rep)
                directory.mkdir()
                (directory / "receipt.json").write_text(
                    json.dumps(receipt(rep, jobs_per_hour=jph)), encoding="utf-8")
            rc, out, _ = run_cli(["--cells-root", tmp, "--cell-prefix", "np2-p512-c2",
                                  "--floor-pct", "20"])
            self.assertEqual(rc, 0)
            self.assertIn("half 1 -- repeat spread: **SUPPORTED**", out)

    def test_the_report_flags_a_mixed_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rep, commit in ((1, "d18ab09"), (2, "b982a1e")):
                directory = root / ("np2-p512-c2-r%d" % rep)
                directory.mkdir()
                (directory / "receipt.json").write_text(
                    json.dumps(receipt(rep, runner_commit=commit)), encoding="utf-8")
            _, out, _ = run_cli(["--cells-root", tmp, "--cell-prefix", "np2-p512-c2"])
            self.assertIn("MIXED", out)

    def test_the_run_writes_nothing_when_json_out_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_cells(root, [1, 2])
            before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            run_cli(["--cells-root", tmp, "--cell-prefix", "np2-p512-c2"])
            after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            self.assertEqual(before, after)


# ---------------------------------------------- the launch-skew instrument, surfaced --
def launch_skew(*skews_ms, threshold_ms=50.0):
    """A ``launch_skew`` block shaped like the runner's, one round per skew."""
    rounds = [{"index": i, "skew_ms": s, "slots": [0, 1], "tasks": [30000 + 2 * i,
                                                                   30001 + 2 * i],
               "t0_uptime_s": 59460.0 + 3.32 * i, "size": 2, "complete": True}
              for i, s in enumerate(skews_ms)]
    return {"rounds": rounds, "max_skew_ms": max(skews_ms), "median_skew_ms": sorted(skews_ms)[
        len(skews_ms) // 2], "delayed_rounds": sum(1 for s in skews_ms if s > threshold_ms),
        "threshold_ms": threshold_ms, "reason": None, "expected_concurrency": 2,
        "log_path": r"C:\work\commandcenter\hearth\var\arc-serve.log"}


class TestLaunchSkewFields(unittest.TestCase):
    """The prereg's bimodality has to be VISIBLE here, not buried in the receipts."""

    def test_the_three_scalars_are_in_the_per_field_table(self):
        names = [name for name, _, _ in nf.SCALAR_FIELDS]
        for wanted in ("launch_skew.max_skew_ms", "launch_skew.median_skew_ms",
                       "launch_skew.delayed_rounds"):
            self.assertIn(wanted, names)

    def test_the_scalars_spread_across_repeats(self):
        doc = reduce([receipt(1, jobs_per_hour=2097.4,
                              launch_skew=launch_skew(0.2, 0.2, 222.0)),
                      receipt(2, jobs_per_hour=2203.7,
                              launch_skew=launch_skew(0.2, 0.2, 0.1)),
                      receipt(3, jobs_per_hour=2125.2,
                              launch_skew=launch_skew(0.2, 0.2, 222.4))])
        field = doc["fields"]["launch_skew.max_skew_ms"]
        self.assertEqual(field["n"], 3)
        self.assertEqual(sorted(field["values"]), [0.2, 222.0, 222.4])
        delayed = doc["fields"]["launch_skew.delayed_rounds"]
        self.assertEqual(sorted(delayed["values"]), [0.0, 1.0, 1.0])
        # the median skew stays clean in every repeat -- the event is ONE round, not a drift
        self.assertEqual(doc["fields"]["launch_skew.median_skew_ms"]["max"], 0.2)

    def test_the_count_of_affected_repeats_is_reported(self):
        doc = reduce([receipt(1, launch_skew=launch_skew(0.2, 0.2, 222.0)),
                      receipt(2, launch_skew=launch_skew(0.2, 0.2, 0.1)),
                      receipt(3, launch_skew=launch_skew(0.2, 0.2, 222.4))])
        summary = doc["launch_skew"]
        self.assertEqual(summary["measured"], 3)
        self.assertEqual(summary["unmeasured"], 0)
        self.assertEqual(summary["repeats_with_delayed_round"], 2)

    def test_a_loud_null_counts_as_unmeasured_not_as_clean(self):
        # "the instrument did not run" and "it ran and saw nothing" are different facts.
        doc = reduce([receipt(1, launch_skew=launch_skew(0.2, 0.2, 222.0)),
                      receipt(2, launch_skew={"rounds": None,
                                              "reason": "anchor failed: ambiguous anchor"}),
                      receipt(3)])  # written before the instrument existed
        summary = doc["launch_skew"]
        self.assertEqual(summary["measured"], 1)
        self.assertEqual(summary["unmeasured"], 2)
        self.assertEqual(summary["repeats_with_delayed_round"], 1)
        reasons = [row["reason"] for row in summary["rows"] if not row["measured"]]
        self.assertIn("anchor failed: ambiguous anchor", reasons)
        self.assertTrue(any("no launch_skew field" in r for r in reasons))

    def test_the_markdown_states_how_many_repeats_were_affected(self):
        doc = reduce([receipt(1, launch_skew=launch_skew(0.2, 0.2, 222.0)),
                      receipt(2, launch_skew=launch_skew(0.2, 0.2, 0.1)),
                      receipt(3, launch_skew=launch_skew(0.2, 0.2, 222.4))])
        text = nf.render_markdown(doc, cell_prefix="np2-p512-c2")
        self.assertIn("launch skew: **2 of 3** included repeats had at least one delayed "
                      "round", text)
        self.assertIn("RECORDED OBSERVATION, not a failure", text)
        self.assertIn("`launch_skew.max_skew_ms`", text)

    def test_the_markdown_says_when_repeats_were_never_measured(self):
        doc = reduce([receipt(1, launch_skew=launch_skew(0.2, 0.2, 222.0)), receipt(2)])
        text = nf.render_markdown(doc, cell_prefix="np2-p512-c2")
        self.assertIn("launch skew: **1 of 1** included repeats", text)
        self.assertIn("**1** repeat(s) carry no launch-skew measurement", text)

    def test_a_delayed_round_never_excludes_a_repeat(self):
        doc = reduce([receipt(1, launch_skew=launch_skew(222.0, 222.0, 222.0)),
                      receipt(2, launch_skew=launch_skew(0.2, 0.2, 0.1))])
        self.assertEqual(doc["n_included"], 2)
        self.assertEqual(doc["n_excluded"], 0)


# ------------------------------------------------- gate 8: the thermal trend, surfaced --
def thermal(*, vram_a=66, vram_b=62, gpu_a=60, gpu_b=59, idle=60, outcome="pass"):
    """A ``thermal`` block shaped like ``sat_cell_runner.score_thermal``'s output."""
    def counters(vram_max, gpu_max):
        return {
            "vram.temperature_c": {"idle_c": float(idle), "busy_p50_c": float(vram_max) - 2,
                                   "busy_p95_c": float(vram_max), "max_c": float(vram_max),
                                   "delta_c": round(float(vram_max) - idle, 1),
                                   "samples": 9, "outcome": outcome, "reason": None,
                                   "warn_c": 88.0, "abort_c": 95.0},
            "gpu.temperature_c": {"idle_c": float(idle) - 4, "busy_p50_c": float(gpu_max) - 1,
                                  "busy_p95_c": float(gpu_max), "max_c": float(gpu_max),
                                  "delta_c": round(float(gpu_max) - (idle - 4), 1),
                                  "samples": 12, "outcome": outcome, "reason": None,
                                  "warn_c": 88.0, "abort_c": 95.0},
        }
    return {"outcome": outcome, "exceeded": outcome == "fail",
            "thresholds": {"vram.temperature_c": {"warn_c": 88.0, "abort_c": 95.0},
                           "delta_warn_c": 15.0,
                           "attribution": "abort 95 C is Derek's call, 2026-09-09"},
            "cards": {ADAPTER_A: {"adapter": ADAPTER_A, "bdf": BDF_A, "outcome": outcome,
                                  "counters": counters(vram_a, gpu_a)},
                      ADAPTER_B: {"adapter": ADAPTER_B, "bdf": BDF_B, "outcome": outcome,
                                  "counters": counters(vram_b, gpu_b)}}}


class TestThermalFields(unittest.TestCase):
    """A thermal drift across repeats has to be VISIBLE, not inferred from the receipts."""

    def test_the_per_card_thermal_families_are_declared(self):
        labels = [label for label, _m, _i, _u in nf.CARD_FAMILIES]
        for wanted in ("thermal.gpu.max_c[%s]", "thermal.gpu.delta_c[%s]",
                       "thermal.vram.max_c[%s]", "thermal.vram.delta_c[%s]"):
            self.assertIn(wanted, labels)

    def test_the_absolute_and_the_rise_both_spread_across_repeats(self):
        doc = reduce([receipt(1, thermal=thermal(vram_a=66, idle=60)),
                      receipt(2, thermal=thermal(vram_a=67, idle=61)),
                      receipt(3, thermal=thermal(vram_a=68, idle=62))])
        # The absolute climbed 2 C across the repeats...
        absolute = doc["fields"]["thermal.vram.max_c[%s]" % ADAPTER_A]
        self.assertEqual(absolute["n"], 3)
        self.assertEqual(absolute["values"], [66.0, 67.0, 68.0])
        # ...while the workload-attributable rise did not move at all. That difference is
        # the whole point of reporting both: this is ambient, not the load.
        rise = doc["fields"]["thermal.vram.delta_c[%s]" % ADAPTER_A]
        self.assertEqual(rise["values"], [6.0, 6.0, 6.0])
        self.assertEqual(rise["spread_pct"], 0.0)

    def test_each_card_is_reported_separately(self):
        doc = reduce([receipt(1, thermal=thermal(vram_a=66, vram_b=62)),
                      receipt(2, thermal=thermal(vram_a=66, vram_b=62))])
        fields = doc["fields"]
        self.assertEqual(fields["thermal.vram.max_c[%s]" % ADAPTER_A]["mean"], 66.0)
        self.assertEqual(fields["thermal.vram.max_c[%s]" % ADAPTER_B]["mean"], 62.0)
        self.assertIn("thermal.gpu.max_c[%s]" % ADAPTER_B, fields)

    def test_a_receipt_written_before_gate_8_reports_the_fields_as_missing(self):
        doc = reduce([receipt(1, thermal=thermal()), receipt(2)])  # r2 predates gate 8
        field = doc["fields"]["thermal.vram.max_c[%s]" % ADAPTER_A]
        self.assertEqual(field["n"], 1)
        self.assertEqual(field["missing"], 1)

    def test_the_thermal_columns_reach_the_markdown(self):
        doc = reduce([receipt(1, thermal=thermal()), receipt(2, thermal=thermal())])
        text = nf.render_markdown(doc, cell_prefix="np2-p512-c2")
        self.assertIn("`thermal.vram.max_c[%s]`" % ADAPTER_A, text)
        self.assertIn("`thermal.vram.delta_c[%s]`" % ADAPTER_A, text)

    def test_a_thermal_exceeded_repeat_is_excluded_and_listed_never_dropped(self):
        doc = reduce([receipt(1, thermal=thermal()),
                      receipt(2, thermal=thermal(vram_a=96, outcome="fail"),
                              thermal_exceeded=True),
                      receipt(3, thermal=thermal())])
        self.assertEqual(doc["n_included"], 2)
        self.assertEqual(doc["n_excluded"], 1)
        self.assertEqual(doc["excluded"][0]["repeat"], "np2-p512-c2-r2")
        self.assertIn("thermal_exceeded", doc["excluded"][0]["reason"])

    def test_a_warn_does_not_exclude_a_repeat(self):
        doc = reduce([receipt(1, thermal=thermal(vram_a=90, outcome="warn")),
                      receipt(2, thermal=thermal())])
        self.assertEqual(doc["n_included"], 2)
        self.assertEqual(doc["n_excluded"], 0)


class TestDutyReferenceDenominator(unittest.TestCase):
    """Averaging a duty cycle over repeats does not lengthen its denominator."""

    CAVEAT = ("the reference is a ~92 s prefill burst at 2 clients; the workloads it stands "
              "in for run for hours")

    def _with_caveat(self, rep: int, **kw) -> dict:
        row = receipt(rep, **kw)
        row["duty_cycle"]["reference_caveat"] = self.CAVEAT
        row["duty_cycle"]["reference_burst_window"] = {
            "declared_s": 90.0, "clients": 2, "prompt_tokens": 8192, "requests": 22,
            "measured_intervals_s": 92}
        row["duty_cycle"]["reference_source"] = r"E:\ref\receipt.json"
        return row

    def test_the_caveat_is_carried_up_from_the_receipts(self):
        doc = reduce([self._with_caveat(1), self._with_caveat(2)])
        ref = doc["duty_reference"]
        self.assertTrue(ref["from_receipts"])
        self.assertEqual(ref["caveat"], self.CAVEAT)
        self.assertEqual(ref["burst_window"]["declared_s"], 90.0)
        self.assertEqual(ref["source"], r"E:\ref\receipt.json")

    def test_receipts_that_predate_the_caveat_still_get_one(self):
        # Silence would read as "no caveat applies", which is the opposite of true.
        doc = reduce([receipt(1), receipt(2)])
        ref = doc["duty_reference"]
        self.assertFalse(ref["from_receipts"])
        self.assertIn("predate", ref["caveat"])
        self.assertIsNone(ref["burst_window"])

    def test_a_report_with_no_duty_field_carries_no_denominator_note(self):
        doc = reduce([receipt(1, omit=("duty_cycle",)), receipt(2, omit=("duty_cycle",))])
        self.assertFalse(any(n.startswith("duty_cycle[") for n in doc["fields"]))
        self.assertIsNone(doc["duty_reference"])

    def test_the_rendered_report_states_the_denominator_beside_the_table(self):
        doc = reduce([self._with_caveat(1), self._with_caveat(2)])
        text = nf.render_markdown(doc, cell_prefix="np2-p512-c2")
        self.assertIn("What `duty_cycle[...]` is a fraction of", text)
        self.assertIn("92 s prefill burst", text)
        self.assertIn("90.0 s", text)


if __name__ == "__main__":
    unittest.main()
