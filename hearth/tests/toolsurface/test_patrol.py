from __future__ import annotations

import json
import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.patrol import patrol


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather_payload(records, scanned=None):
    return json.dumps({"records": records, "scanned": scanned if scanned is not None else len(records)})


class PatrolTests(TestCase):
    def test_reports_gaps_from_gathered_records(self):
        records = [
            {"plan_id": "hearth-old", "age_s": 5000, "has_result": False},
            {"plan_id": "hearth-crash", "age_s": 10, "has_result": True,
             "stub": True, "status": "errored", "error": "errored (isolated): x"},
            {"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
             "status": "ok", "winner": "am4-worker-1", "promoted": True,
             "winner_grade": "B", "winner_files": 205, "n_questions": 0},
        ]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records, scanned=143))):
            out = patrol()
        self.assertTrue(out["ok"])
        self.assertEqual(out["scanned"], 143)
        self.assertEqual(out["considered"], 3)
        kinds = sorted(g["kind"] for g in out["gaps"])
        self.assertEqual(kinds, ["crashed_isolated", "phantom_in_flight"])
        self.assertEqual(out["summary"]["total"], 2)

    def test_clean_fleet_reports_no_gaps(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))):
            out = patrol()
        self.assertTrue(out["ok"])
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["summary"]["total"], 0)

    def test_ssh_failure_is_a_clean_result(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = patrol()
        self.assertFalse(out["ok"])
        self.assertIn("TimeoutExpired", out["error"])

    def test_non_json_gather_output_reported(self):
        with patch("subprocess.run", return_value=_completed(stdout="not json")):
            out = patrol()
        self.assertFalse(out["ok"])
        self.assertIn("non-JSON", out["error"])
