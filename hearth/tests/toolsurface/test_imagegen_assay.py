"""Scheduling assay for imagegen 250-request workload.

Tests the CP-SAT scheduler against FIFO and smart-baseline on a realistic
250-request image-generation workload with mixed models and deadlines.
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import skipUnless

# Check if ortools is available
try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False

from hearth.scheduler.experiments.imagegen_250 import run_experiment


class ImagegenAssayTests(unittest.TestCase):
    """Scheduling assay for imagegen-250 workload."""

    @classmethod
    def setUpClass(cls):
        """Load the fixture once for all tests."""
        fixture_path = (
            Path(__file__).parent.parent / "fixtures" / "imagegen_requests_250.json"
        )
        cls.requests = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert len(cls.requests) == 250, f"Expected 250 requests, got {len(cls.requests)}"

    @skipUnless(HAS_ORTOOLS, "ortools not available")
    def test_scheduler_beats_fifo_on_deadline_misses(self) -> None:
        """Scheduler should have zero deadline misses where FIFO has many."""
        result = run_experiment(self.requests)

        sched = next((a for a in result["arms"] if a["arm"] == "scheduler"), None)
        fifo = next((a for a in result["arms"] if a["arm"] == "fifo-baseline"), None)

        self.assertIsNotNone(sched, "scheduler arm not found")
        self.assertIsNotNone(fifo, "fifo-baseline arm not found")

        # Scheduler should have zero deadline misses
        self.assertEqual(sched["deadline_misses"], 0,
                         "Scheduler should meet all deadlines")

        # FIFO should have many misses
        self.assertGreater(fifo["deadline_misses"], 0,
                           "FIFO baseline should have deadline misses for comparison")

    @skipUnless(HAS_ORTOOLS, "ortools not available")
    def test_scheduler_arm_loads_equal_three(self) -> None:
        """Scheduler should coalesce loads to exactly 3 (one per model)."""
        result = run_experiment(self.requests)

        sched = next((a for a in result["arms"] if a["arm"] == "scheduler"), None)
        self.assertIsNotNone(sched)

        # Three distinct models in the workload -> 3 loads
        self.assertEqual(sched["total_loads"], 3,
                         "Should load exactly 3 distinct models")

    @skipUnless(HAS_ORTOOLS, "ortools not available")
    def test_smart_baseline_also_three_loads(self) -> None:
        """Smart baseline (sort-by-model) should also get 3 loads."""
        result = run_experiment(self.requests)

        smart = next((a for a in result["arms"] if a["arm"] == "smart-baseline"), None)
        self.assertIsNotNone(smart)

        self.assertEqual(smart["total_loads"], 3,
                         "Sort-by-model baseline should load 3 models")

    @skipUnless(HAS_ORTOOLS, "ortools not available")
    def test_fifo_has_many_loads(self) -> None:
        """FIFO on random order should repeatedly load/unload."""
        result = run_experiment(self.requests)

        fifo = next((a for a in result["arms"] if a["arm"] == "fifo-baseline"), None)
        self.assertIsNotNone(fifo)

        # Request order is randomized per model; should see many swaps
        self.assertGreater(fifo["total_loads"], 10,
                           "FIFO should have many model loads due to interleaved requests")

    @skipUnless(HAS_ORTOOLS, "ortools not available")
    def test_scheduler_feasible_status(self) -> None:
        """250-request workload should be solvable (OPTIMAL or rolling windows)."""
        result = run_experiment(self.requests)

        sched = next((a for a in result["arms"] if a["arm"] == "scheduler"), None)
        self.assertIsNotNone(sched)

        # Status may be OPTIMAL (single-shot) or rolling windows with mixed statuses
        self.assertIn("OPTIMAL", sched["solver_status"],
                      f"Scheduler should include OPTIMAL in status, got: {sched['solver_status']}")

    def test_fixture_integrity(self) -> None:
        """Fixture must have 250 requests with required fields."""
        self.assertEqual(len(self.requests), 250)

        required_fields = [
            "request_id", "model", "steps", "width", "height", "batch_size"
        ]
        for i, req in enumerate(self.requests):
            for field in required_fields:
                self.assertIn(field, req,
                              f"Request {i} missing field '{field}'")

        # Models must be one of the three
        models = set(r["model"] for r in self.requests)
        expected_models = {"sd3.5_large", "sdxl-base", "flux-schnell"}
        self.assertEqual(models, expected_models,
                         f"Expected {expected_models}, got {models}")


if __name__ == "__main__":
    unittest.main()
