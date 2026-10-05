"""Real recorded r1 fixture plus constructed repeat/contamination/error cases."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.ops import bench27_summarize as summary

FIXTURE = Path(__file__).parent / "fixtures" / "bench27-parity-r1.json"


class Bench27SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "evidence"
        self.root.mkdir()
        self.fixture = json.loads(FIXTURE.read_text())
        self.add_repeat("r1")
        self.cards = Path(self.temp.name) / "cards.jsonl"
        self.cards.write_text("".join(json.dumps(row) + "\n" for row in self.fixture["thermal"]))

    def add_repeat(self, name, *, delta=0):
        for seat, item in self.fixture["seats"].items():
            directory = self.root / name / seat
            artifacts = directory / "conversation-1" / "artifacts"
            artifacts.mkdir(parents=True)
            run = copy.deepcopy(item["run"])
            run["seconds_total"] += delta
            (directory / "run.json").write_text(json.dumps(run))
            for stage in ("work", "final"):
                (artifacts / f"{stage}.output.txt").write_text(item["texts"][stage])
                (artifacts / f"{stage}.wire_request.txt").write_text(json.dumps(item["wire"][stage]))

    def test_real_r1_durations_tokens_divergence_thermal_and_provisional_threshold(self):
        result = summary.summarize(self.root, [self.cards])
        zero, one = result["runs"]
        self.assertEqual((zero["seconds_total"], one["seconds_total"]), (194.5, 227.9))
        self.assertEqual(zero["conversations"][0]["work"]["observed"]["tokens_out"], 8629)
        self.assertAlmostEqual(zero["decode_tokens_per_s"], 9003/161.489)
        self.assertEqual(one["conversations"][0]["work"]["divergence"]["first_character"], 16)
        self.assertEqual(zero["conversations"][0]["final"]["divergence"]["state"], "equal")
        self.assertEqual((zero["thermal"]["vram_c"]["max"], one["thermal"]["vram_c"]["max"]), (100, 94))
        metric = result["calibration"]["metrics"]["seconds_total"]
        self.assertAlmostEqual(metric["provisional_estimate"], 33.4/211.2)
        self.assertFalse(metric["ready"])
        self.assertIsNone(metric["threshold"])
        self.assertEqual(result["calibration"]["recipe_comparability"], "incomplete_per_run_snapshots")

    def test_three_repeats_use_max_of_within_range_and_between_medians(self):
        self.add_repeat("r2", delta=60)
        self.add_repeat("r3", delta=120)
        metric = summary.summarize(self.root)["calibration"]["metrics"]["seconds_total"]
        self.assertTrue(metric["ready"])
        self.assertAlmostEqual(metric["threshold"], 120 / 254.5)
        self.assertEqual(metric["seats"]["seat-0"]["n"], 3)

    def test_contamination_and_missing_output_are_kept_but_excluded(self):
        path = self.root / "r1/seat-1/run.json"
        run = json.loads(path.read_text())
        run["foreign_requests_possible"] = 1
        path.write_text(json.dumps(run))
        (path.parent / "conversation-1/artifacts/final.output.txt").unlink()
        result = summary.summarize(self.root)
        self.assertEqual(len(result["runs"]), 2)
        self.assertIn("foreign_requests_possible=1", result["runs"][1]["timing_exclusions"])
        self.assertEqual(result["runs"][1]["conversations"][0]["final"]["divergence"]["state"], "missing_artifact")
        self.assertEqual(result["calibration"]["metrics"]["seconds_total"]["seats"]["seat-1"]["n"], 0)
        self.assertEqual(result["runs"][0]["thermal"]["state"], "unknown")

    def test_changed_regime_never_calibrates_as_aa(self):
        path = self.root / "r1/seat-1/conversation-1/artifacts/work.wire_request.txt"
        wire = json.loads(path.read_text())
        wire["temperature"] = 1
        path.write_text(json.dumps(wire))
        result = summary.summarize(self.root)
        self.assertTrue(result["calibration"]["reasons"])
        self.assertIsNone(result["calibration"]["metrics"]["seconds_total"]["provisional_estimate"])

    def test_new_output_only_and_external_provenance_is_linked_not_assumed(self):
        provenance = Path(self.temp.name) / "entry-argv.json"
        provenance.write_text('["serve", "model"]')
        result = summary.summarize(self.root, provenance=[provenance])
        output = Path(self.temp.name) / "summary"
        summary.write_summary(result, output)
        self.assertEqual(json.loads((output / "summary.json").read_text())["schema"], summary.SCHEMA)
        self.assertIn("entry-argv.json", (output / "SUMMARY.md").read_text())
        self.assertIn("Stage measurements and regime", (output / "SUMMARY.md").read_text())
        with self.assertRaises(FileExistsError):
            summary.write_summary(result, output)

    def test_explicit_cold_repeat_exclusion_keeps_rows_and_leaves_two_warm_samples(self):
        self.add_repeat("r2", delta=-30)
        self.add_repeat("r3", delta=-31)
        result = summary.summarize(self.root, exclude_repeats={"r1": "first requests after restart; cold-cache baseline"})
        self.assertEqual(len(result["runs"]), 6)
        excluded = [r for r in result["runs"] if r["repeat"] == "r1"]
        self.assertEqual(len(excluded), 2)
        self.assertTrue(all("caller excluded repeat r1" in r["timing_exclusions"][-1] for r in excluded))
        metric = result["calibration"]["metrics"]["seconds_total"]
        self.assertFalse(metric["ready"])
        self.assertIsNone(metric["threshold"])
        self.assertEqual([s["n"] for s in metric["seats"].values()], [2, 2])
        self.assertEqual(excluded[0]["prefill_seconds"], 30.381)
        self.assertEqual(excluded[0]["work_first_reasoning_ms"], 15120)
        self.assertEqual(result["calibration"]["diagnostic_spread"]["prefill_seconds"]["seat-0"]["n"], 2)
        with self.assertRaisesRegex(summary.SummaryError, "not found"):
            summary.summarize(self.root, exclude_repeats={"misspelled-repeat": "cold"})

    def test_all_sampling_controls_distinguish_regimes(self):
        path = self.root / "r1/seat-1/conversation-1/artifacts/work.wire_request.txt"
        wire = json.loads(path.read_text())
        wire["min_p"] = .05
        path.write_text(json.dumps(wire))
        result = summary.summarize(self.root)
        self.assertTrue(result["calibration"]["reasons"])
        self.assertEqual(result["runs"][1]["conversations"][0]["work"]["controls"]["min_p"], .05)

    def test_truncated_and_null_artifact_records_do_not_count(self):
        path = self.root / "r1/seat-1/run.json"
        run = json.loads(path.read_text())
        run["conversations"][0]["work"]["observed"]["finish_reason"] = "length"
        run["conversations"][0]["final"]["artifacts"] = None
        path.write_text(json.dumps(run))
        result = summary.summarize(self.root)
        self.assertIn("incomplete, failed, truncated, or wire-mismatched conversation", result["runs"][1]["timing_exclusions"])
        self.assertIn("missing or mismatched visible output artifact", result["runs"][1]["timing_exclusions"])

    def test_inside_root_output_refuses_and_missing_reference_conversation_is_named(self):
        path = self.root / "r1/seat-1/run.json"
        run = json.loads(path.read_text())
        second = copy.deepcopy(run["conversations"][0])
        second["conversation"] = 2
        run["conversations"].append(second)
        path.write_text(json.dumps(run))
        result = summary.summarize(self.root, card_map={"seat-0": "card2", "seat-1": "card3"})
        self.assertEqual(result["card_map"]["seat-1"], "card3")
        self.assertEqual(result["runs"][1]["conversations"][1]["work"]["divergence"]["state"], "missing_reference_conversation")
        self.assertEqual(result["runs"][0]["thermal"]["reason"], "no thermal records supplied")
        with self.assertRaisesRegex(summary.SummaryError, "outside"):
            summary.write_summary(result, self.root / "new-summary")
        self.assertFalse((self.root / "new-summary").exists())

    def test_prefix_divergence_and_bad_thermal_are_explicit(self):
        self.assertEqual(summary.divergence("abc", "abcd")["first_character"], 3)
        self.cards.write_text('{"utc":"missing-timezone"}\n')
        with self.assertRaises(summary.SummaryError):
            summary.summarize(self.root, [self.cards])


if __name__ == "__main__":
    unittest.main()
