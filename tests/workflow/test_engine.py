from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.engine import process_inbox


ROOT = Path(__file__).resolve().parents[2]


class EngineTests(TestCase):
    def test_process_inbox_materializes_and_archives(self) -> None:
        temp_dir = Path(mkdtemp())
        try:
            inbox_dir = temp_dir / "inbox"
            runs_dir = temp_dir / "runs"
            inbox_dir.mkdir()
            runs_dir.mkdir()

            work_item = inbox_dir / "engine-smoke.md"
            work_item.write_text((ROOT / "fixtures" / "workflow" / "sample-work-item.md").read_text(encoding="utf-8"), encoding="utf-8")

            results = process_inbox(inbox_dir, runs_dir, archive=True, scenario="happy")

            self.assertEqual(len(results), 1)
            run_dir = runs_dir / "engine-smoke"
            self.assertTrue((run_dir / "events.jsonl").exists())
            self.assertTrue((run_dir / "state.json").exists())
            self.assertTrue((run_dir / "board.json").exists())
            self.assertTrue((run_dir / "otel-events.jsonl").exists())
            self.assertTrue((run_dir / "artifacts" / "inbox" / "engine-smoke.md").exists())
            self.assertTrue((run_dir / "artifacts" / "decisions" / "dec_assign_001.json").exists())
            self.assertTrue((run_dir / "artifacts" / "observations" / "obs_001.json").exists())
            self.assertTrue((runs_dir / "knowledge" / "capacity_estimates.json").exists())
            self.assertTrue((runs_dir / "knowledge" / "known_good_models.json").exists())
            self.assertTrue((runs_dir / "knowledge" / "known_bad_models.json").exists())
            self.assertTrue((runs_dir / "knowledge" / "prediction_accuracy.json").exists())
            self.assertFalse(work_item.exists())
            self.assertEqual(results[0]["status"], "approved")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
