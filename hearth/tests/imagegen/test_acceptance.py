from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hearth.imagegen.acceptance import load_acceptance, workflow_available


class ImageAcceptanceTest(unittest.TestCase):
    def test_missing_record_is_one_lane_and_dual_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            acceptance = load_acceptance(Path(temporary) / "missing.json")
        self.assertEqual(1, acceptance.accepted_lane_count)
        self.assertFalse(acceptance.dual_cfg_enabled)
        self.assertFalse(acceptance.dual_layers_enabled)

    def test_gang_workflow_requires_both_capacity_and_strategy_acceptance(self) -> None:
        workflow = {"cards_required": 2, "allowed_strategies": ["dual_layers"]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            path.write_text(json.dumps({
                "schema": "imagegen.acceptance.v1", "accepted_lane_count": 2,
                "dual_cfg_enabled": False, "dual_layers_enabled": True,
                "stale": False, "detail": "qualified",
            }), encoding="utf-8")
            acceptance = load_acceptance(path)
        self.assertTrue(workflow_available(workflow, "dual_layers", acceptance))
        self.assertFalse(workflow_available(
            {**workflow, "allowed_strategies": ["dual_cfg"]}, "dual_cfg", acceptance
        ))


if __name__ == "__main__":
    unittest.main()
