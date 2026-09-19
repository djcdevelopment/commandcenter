"""Run-record importers (ADR-0047, amended) on synthetic files in the real shapes."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hearth.seats.receipts import SeatReceiptError, SeatReceiptsLedger, validate_run_record
from hearth.seats.runrecords import harvest

NOW = datetime(2026, 9, 19, 9, 0, 0, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sources(root: Path) -> list[tuple[str, str]]:
    lab = root / "lab"
    lab.mkdir()
    (lab / "20260702-054619-cuda-model.json").write_text(json.dumps({
        "Timestamp": "2026-07-02T05:46:19", "Profile": "cuda", "Port": 11438, "Model": "secret-model",
        "RepeatCount": 10, "WallSeconds": 5.59, "PromptTokens": 957, "OutputTokens": 120,
    }), encoding="utf-8")
    (lab / "20260702-091704-cross.json").write_text(json.dumps([
        {"Timestamp": "2026-07-02T09:09:26", "Model": "secret-model", "WallSeconds": 46.58, "PromptTokens": 128, "OutputTokens": 120},
        {"Timestamp": "2026-07-02T09:10:26", "Model": "secret-model", "WallSeconds": 40.0, "PromptTokens": 128, "OutputTokens": 98},
    ]), encoding="utf-8")
    (lab / "20260702-091704-cross.csv").write_text("Timestamp,PromptTokens\n2026-07-02T09:09:26,128\n", encoding="utf-8")

    experiments = root / "experiments"
    (experiments / "matrix-20260706T052819Z-pilot").mkdir(parents=True)
    (experiments / "matrix-20260706T052819Z-pilot" / "dataset.json").write_text(json.dumps({
        "run_id": "20260706T052819Z", "summary": {}, "rows": [
            {"cell_id": "a_L1", "planner": {"model": "secret-planner"}, "critic": {"model": "secret-critic"},
             "ok": True, "cost": {"author_calls": 2, "critic_calls": 1, "tokens_out": 3633, "duration_ms": 78186}},
            {"cell_id": "b_L1", "planner": {"model": "secret-planner"}, "critic": {"model": "secret-critic"},
             "ok": False, "error": "timeout", "cost": {"author_calls": 1, "critic_calls": 0, "tokens_out": 0, "duration_ms": 1000}},
        ]}), encoding="utf-8")
    (experiments / "study-20260707T073708Z").mkdir()
    (experiments / "study-20260707T073708Z" / "rows.jsonl").write_text(
        json.dumps({"cell_id": "c_L2", "planner": {"model": "secret-planner"}, "critic": {"model": "secret-critic"},
                    "ok": True, "cost": {"author_calls": 3, "critic_calls": 2, "tokens_out": 5000, "duration_ms": 90000}}) + "\n",
        encoding="utf-8")
    (experiments / "study-20260707T073708Z" / "panel_rejudge.jsonl").write_text(
        json.dumps({"cell_id": "c_L2", "judge_model": "secret-judge", "score": 92}) + "\n", encoding="utf-8")
    (experiments / "doc-adr-bench-20260721T161359Z-sweep").mkdir()
    (experiments / "doc-adr-bench-20260721T161359Z-sweep" / "rows.jsonl").write_text(
        json.dumps({"task_id": "adr-x", "backend": "gcp-gemini", "model": "gemini", "ok": True, "tokens_in": 7929,
                    "tokens_out": 885, "duration_ms": 24188, "score": {"judges": [{"model": "secret-judge", "score": 92}]}}) + "\n",
        encoding="utf-8")
    (experiments / "unrelated-dir").mkdir()

    gambit = root / "raw-2026-07-18-gambit"
    gambit.mkdir()
    (gambit / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"tag": "A.c1.r0", "t_start": 1784360841.2, "prompt_target": 800, "max_tokens": 128, "ok": True, "ttft_s": 1.75, "total_s": 6.0, "chunks": 125},
        {"tag": "A.c1.r1", "t_start": 1784360850.0, "prompt_target": 800, "max_tokens": 128, "ok": False, "error": "refused", "total_s": 0.1},
    ]) + "\n", encoding="utf-8")
    (gambit / "samples.jsonl").write_text(json.dumps({"tag": "A", "mean": 1}) + "\n", encoding="utf-8")

    bench = root / "backfills"
    bench.mkdir()
    (bench / "llama-bench-a.bench-row.v1.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"row_id": "row-1", "run_id": "llama-bench-a", "timestamp": "2026-08-27T23:39:58Z", "model": "secret-model",
         "n_prompt": 512, "n_gen": 0, "n_runs": 3, "source": {"adapter": "llama-bench.json.v1", "path": "corpus/x"}},
        {"row_id": "row-2", "run_id": "llama-bench-a", "timestamp": "2026-08-27T23:40:58Z", "model": "secret-model",
         "n_prompt": 0, "n_gen": 128, "n_runs": 3, "source": {"adapter": "llama-bench.json.v1", "path": "corpus/x"}},
    ]) + "\n", encoding="utf-8")
    (bench / "qwen38-summary.bench-row.v1.jsonl").write_text(json.dumps(
        {"row_id": "row-9", "run_id": "qwen38", "timestamp": "2026-08-27T10:00:00Z", "model": "secret-model",
         "n_prompt": 100000, "n_gen": 100, "n_runs": 1, "source": {"adapter": "qwen38-summary.json.v1"}}) + "\n", encoding="utf-8")
    (bench / "vulkancliff-y.bench-row.v1.jsonl").write_text(json.dumps(
        {"row_id": "row-8", "run_id": "vulkancliff", "timestamp": None, "model": "secret-model",
         "n_prompt": 512, "n_gen": 128, "n_runs": 1, "source": {"adapter": "llama-batched-bench.text.v1"}}) + "\n", encoding="utf-8")

    return [("ollama-backend-lab", str(lab)), ("planning-matrix", str(experiments)),
            ("am4-load-test", str(gambit)), ("bench-rows", str(bench))]


class RunRecordImportTests(unittest.TestCase):
    def test_each_adapter_yields_one_attempt_per_recorded_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = _write_sources(root)
            ledger = root / "run-records.ndjson"
            report = harvest(ledger, sources, now=NOW)
            by = {row["adapter"]: row for row in report["sources"]}
            # lab: three JSON records (one single, one array of two); the CSV twin is not read
            self.assertEqual((by["ollama-backend-lab"]["attempts"], by["ollama-backend-lab"]["tokens_in"]), (3, 957 + 128 + 128))
            # matrix: two cells + one study row + one doc-adr row; re-judge file not counted
            self.assertEqual(by["planning-matrix"]["attempts"], 4)
            self.assertEqual(by["planning-matrix"]["failed"], 1)
            self.assertEqual(by["planning-matrix"]["tokens_out"], 3633 + 0 + 5000)
            self.assertEqual(by["planning-matrix"]["unknown"], 1)
            # load test: two request rows, one failed, no tokens
            self.assertEqual((by["am4-load-test"]["attempts"], by["am4-load-test"]["failed"], by["am4-load-test"]["unknown"]), (2, 1, 2))
            # bench rows: two llama-bench rows with tokens times runs; summary and undated rows excluded
            self.assertEqual((by["bench-rows"]["attempts"], by["bench-rows"]["tokens_in"], by["bench-rows"]["tokens_out"]), (2, 1536, 384))
            self.assertEqual(report["totals"]["attempts"], 11)
            self.assertEqual(report["totals"]["new_receipts"], 11)
            rows = list(SeatReceiptsLedger(ledger).rows())
            self.assertEqual(len(rows), 11)
            for row in rows:
                validate_run_record(row)
            text = ledger.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"[A-Za-z]:\\")
            self.assertNotIn(str(root), text)

    def test_reimport_is_a_noop_and_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = _write_sources(root)
            ledger = root / "run-records.ndjson"
            dry = harvest(ledger, sources, dry_run=True, now=NOW)
            self.assertEqual(dry["totals"]["new_receipts"], 11)
            self.assertFalse(ledger.exists())
            harvest(ledger, sources, now=NOW)
            digest = _sha(ledger)
            again = harvest(ledger, sources, now=NOW.replace(hour=10))
            self.assertEqual(again["totals"]["new_receipts"], 0)
            self.assertEqual(_sha(ledger), digest)

    def test_timestamps_and_derivations_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = _write_sources(root)
            ledger = root / "run-records.ndjson"
            harvest(ledger, sources, now=NOW)
            rows = {r["source"]["adapter"]: r for r in SeatReceiptsLedger(ledger).rows()}
            self.assertEqual(rows["ollama-backend-lab"]["timestamp_derivation"], {"method": "record_timestamp_naive_local", "error_bound_s": 86400})
            self.assertTrue(rows["ollama-backend-lab"]["started_at"].startswith("2026-07-02T"))
            self.assertEqual(rows["planning-matrix"]["timestamp_derivation"]["method"], "run_directory_stamp")
            self.assertEqual(rows["am4-load-test"]["timestamp_derivation"], {"method": "record_timestamp", "error_bound_s": 0})
            self.assertTrue(rows["am4-load-test"]["started_at"].startswith("2026-07-18T"))
            self.assertIn(rows["bench-rows"]["started_at"], {"2026-08-27T23:39:58Z", "2026-08-27T23:40:58Z"})

    def test_a_missing_source_root_is_an_absence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = harvest(root / "ledger.ndjson", [("bench-rows", str(root / "absent"))], now=NOW)
            self.assertEqual(report["totals"]["attempts"], 0)
            self.assertFalse(report["sources"][0]["present"])

    def test_validator_rejects_paths_and_empty_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = _write_sources(root)
            ledger = root / "run-records.ndjson"
            harvest(ledger, sources, now=NOW)
            good = next(SeatReceiptsLedger(ledger).rows())
            bad = json.loads(json.dumps(good))
            bad["provider"]["model_name"] = r"C:\models\secret.gguf"
            with self.assertRaisesRegex(SeatReceiptError, "filesystem path"):
                validate_run_record(bad)
            bad = json.loads(json.dumps(good))
            bad["usage"] = {"tokens_in": None, "tokens_out": None}
            bad["usage_unknown_reason"] = None
            with self.assertRaisesRegex(SeatReceiptError, "no fields"):
                validate_run_record(bad)


if __name__ == "__main__":
    unittest.main()
