from __future__ import annotations

import json
import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.masters_pet import AUTO_HEAL_KINDS, remediate


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather(records):
    return json.dumps({"records": records, "scanned": len(records)})


# One phantom (auto-healable), one crashed + one stale-checkout (flag-only).
_RECORDS = [
    {"plan_id": "hearth-old", "age_s": 999999, "has_result": False},
    {"plan_id": "hearth-crash", "age_s": 10, "has_result": True, "stub": True,
     "status": "errored", "error": "errored (isolated): x"},
    {"plan_id": "hearth-stale", "age_s": 10, "has_result": True, "winner": "w",
     "promoted": False, "n_questions": 1,
     "questions_text": "the hearth directory does not exist"},
]


class RemediateTests(TestCase):
    def test_dry_run_partitions_but_does_not_heal(self):
        with patch("subprocess.run", return_value=_completed(stdout=_gather(_RECORDS))) as run:
            out = remediate(apply=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertEqual([g["kind"] for g in out["healable"]], ["phantom_in_flight"])
        flagged_kinds = sorted(g["kind"] for g in out["flagged"])
        self.assertEqual(flagged_kinds, ["crashed_isolated", "stale_checkout"])
        self.assertNotIn("healed", out)
        # only the gather SSH ran; no heal call
        self.assertEqual(run.call_count, 1)

    def test_apply_heals_only_phantom_and_reports_actions(self):
        heal_out = json.dumps({"healed": [
            {"plan_id": "hearth-old", "action": "stubbed", "result_path": "runs/hearth-old/result.json"}]})
        # first subprocess.run = gather, second = heal
        with patch("subprocess.run", side_effect=[
                _completed(stdout=_gather(_RECORDS)),
                _completed(stdout=heal_out)]):
            out = remediate(apply=True)
        self.assertTrue(out["ok"])
        self.assertFalse(out["dry_run"])
        self.assertIn("healed", out)
        self.assertEqual(out["healed"][0]["plan_id"], "hearth-old")
        self.assertEqual(out["healed"][0]["action"], "stubbed")

    def test_flag_only_kinds_never_auto_healed(self):
        self.assertEqual(AUTO_HEAL_KINDS, {"phantom_in_flight"})
        self.assertNotIn("false_success", AUTO_HEAL_KINDS)
        self.assertNotIn("stale_checkout", AUTO_HEAL_KINDS)
        self.assertNotIn("crashed_isolated", AUTO_HEAL_KINDS)

    def test_apply_with_no_healable_gaps_makes_no_heal_call(self):
        clean = [{"plan_id": "ok", "age_s": 9000, "has_result": True, "status": "ok",
                  "winner": "w", "promoted": True, "winner_grade": "A", "winner_files": 50,
                  "n_questions": 0}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather(clean))) as run:
            out = remediate(apply=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["healable"], [])
        self.assertNotIn("healed", out)
        self.assertEqual(run.call_count, 1)

    def test_ssh_failure_is_clean(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = remediate(apply=True)
        self.assertFalse(out["ok"])
        self.assertIn("TimeoutExpired", out["error"])
