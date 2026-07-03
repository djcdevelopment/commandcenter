from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.reference_runner import run_reference_workflow
from tools.workflow.validate_events import validate_file


ROOT = Path(__file__).resolve().parents[2]


class ReferenceRunnerTests(TestCase):
    def test_reference_runner_happy_path_creates_run_dir(self) -> None:
        temp_dir = Path(mkdtemp())
        try:
            work_item = ROOT / "fixtures" / "workflow" / "sample-work-item.md"
            state = run_reference_workflow(work_item, temp_dir, "happy")

            run_dir = temp_dir / "sample-work-item"
            self.assertTrue((run_dir / "events.jsonl").exists())
            self.assertTrue((run_dir / "state.json").exists())
            self.assertTrue((run_dir / "board.json").exists())
            self.assertTrue((run_dir / "otel-events.jsonl").exists())
            self.assertEqual(validate_file(run_dir / "events.jsonl"), [])
            self.assertEqual(state["status"], "approved")
            self.assertEqual(state["workflow_id"], "wf_sample-work-item")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
