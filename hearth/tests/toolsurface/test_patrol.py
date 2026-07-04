from __future__ import annotations

import json
import subprocess
from unittest import TestCase
from unittest.mock import MagicMock, patch

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
            out = patrol(refresh=False)
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
            out = patrol(refresh=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["summary"]["total"], 0)

    def test_ssh_failure_is_a_clean_result(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = patrol(refresh=False)
        self.assertFalse(out["ok"])
        self.assertIn("TimeoutExpired", out["error"])

    def test_non_json_gather_output_reported(self):
        with patch("subprocess.run", return_value=_completed(stdout="not json")):
            out = patrol(refresh=False)
        self.assertFalse(out["ok"])
        self.assertIn("non-JSON", out["error"])

    def test_refresh_false_excludes_refresh_key(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))):
            out = patrol(refresh=False)
        self.assertTrue(out["ok"])
        self.assertNotIn("refresh", out)

    def test_refresh_true_includes_refresh_section(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]

        mock_capacity_result = {"path": "/tmp/capacity.json", "bucket_count": 5}
        mock_am4_result = {"models": {"m1": {}, "m2": {}}, "cards": []}
        mock_hindsight_result = {
            "ok": True,
            "report": {"n_runs": 10, "regret": {"mean_regret": 0.05, "max_regret": 0.15}},
            "table": "table output"
        }

        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))), \
             patch("hearth.toolsurface.patrol._project_capacity_knowledge", return_value=mock_capacity_result), \
             patch("hearth.toolsurface.patrol._gather_am4_catalog", return_value=mock_am4_result), \
             patch("hearth.toolsurface.patrol._schedule_hindsight", return_value=mock_hindsight_result):
            out = patrol(refresh=True)

        self.assertTrue(out["ok"])
        self.assertIn("refresh", out)
        self.assertIn("capacity", out["refresh"])
        self.assertIn("am4_catalog", out["refresh"])
        self.assertIn("hindsight", out["refresh"])

        # Verify structure of each refresh result
        self.assertTrue(out["refresh"]["capacity"]["ok"])
        self.assertEqual(out["refresh"]["capacity"]["bucket_count"], 5)

        self.assertTrue(out["refresh"]["am4_catalog"]["ok"])
        self.assertEqual(out["refresh"]["am4_catalog"]["model_count"], 2)

        self.assertTrue(out["refresh"]["hindsight"]["ok"])
        self.assertEqual(out["refresh"]["hindsight"]["regret"]["n_runs"], 10)
        self.assertEqual(out["refresh"]["hindsight"]["regret"]["mean_regret"], 0.05)

    def test_refresh_capacity_failure_does_not_break_patrol(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]

        mock_hindsight_result = {
            "ok": True,
            "report": {"n_runs": 0, "regret": {}},
            "table": ""
        }

        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))), \
             patch("hearth.toolsurface.patrol._project_capacity_knowledge", side_effect=ValueError("bad capacity")), \
             patch("hearth.toolsurface.patrol._gather_am4_catalog", return_value={"models": {}}), \
             patch("hearth.toolsurface.patrol._schedule_hindsight", return_value=mock_hindsight_result):
            out = patrol(refresh=True)

        self.assertTrue(out["ok"])
        self.assertIn("refresh", out)
        self.assertFalse(out["refresh"]["capacity"]["ok"])
        self.assertIn("ValueError", out["refresh"]["capacity"]["error"])
        # Other callees should still be present
        self.assertIn("am4_catalog", out["refresh"])
        self.assertIn("hindsight", out["refresh"])

    def test_refresh_all_three_callees_can_fail_independently(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]

        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))), \
             patch("hearth.toolsurface.patrol._project_capacity_knowledge", side_effect=RuntimeError("cap error")), \
             patch("hearth.toolsurface.patrol._gather_am4_catalog", side_effect=RuntimeError("am4 error")), \
             patch("hearth.toolsurface.patrol._schedule_hindsight", side_effect=RuntimeError("hindsight error")):
            out = patrol(refresh=True)

        self.assertTrue(out["ok"])
        self.assertFalse(out["refresh"]["capacity"]["ok"])
        self.assertFalse(out["refresh"]["am4_catalog"]["ok"])
        self.assertFalse(out["refresh"]["hindsight"]["ok"])
        self.assertIn("RuntimeError", out["refresh"]["capacity"]["error"])
        self.assertIn("RuntimeError", out["refresh"]["am4_catalog"]["error"])
        self.assertIn("RuntimeError", out["refresh"]["hindsight"]["error"])

    def test_refresh_hindsight_ok_false_returns_error_in_refresh(self):
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]

        mock_hindsight_failed = {"ok": False, "error": "ssh unreachable"}

        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))), \
             patch("hearth.toolsurface.patrol._project_capacity_knowledge", return_value={"bucket_count": 0}), \
             patch("hearth.toolsurface.patrol._gather_am4_catalog", return_value={"models": {}}), \
             patch("hearth.toolsurface.patrol._schedule_hindsight", return_value=mock_hindsight_failed):
            out = patrol(refresh=True)

        self.assertTrue(out["ok"])
        self.assertFalse(out["refresh"]["hindsight"]["ok"])
        self.assertEqual(out["refresh"]["hindsight"]["error"], "ssh unreachable")
