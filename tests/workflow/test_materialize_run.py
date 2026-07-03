from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.materialize_run import materialize_run


ROOT = Path(__file__).resolve().parents[2]


class MaterializeRunTests(TestCase):
    def test_materialize_run_writes_state_json(self) -> None:
        temp_dir = Path(mkdtemp())
        try:
            run_dir = temp_dir / "run_002"
            run_dir.mkdir()
            shutil.copyfile(ROOT / "fixtures" / "workflow" / "promotion-held.events.jsonl", run_dir / "events.jsonl")

            state = materialize_run(run_dir)

            self.assertTrue((run_dir / "state.json").exists())
            self.assertTrue((run_dir / "board.json").exists())
            self.assertTrue((run_dir / "otel-events.jsonl").exists())
            self.assertEqual(state["status"], "held")
            self.assertEqual(len(state["decisions"]), 3)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
