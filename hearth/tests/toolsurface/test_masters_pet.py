from __future__ import annotations

import json
import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.masters_pet import AUTO_HEAL_KINDS, masters_pet


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather(records, scanned=None, truncated=0):
    return json.dumps({"records": records,
                       "scanned": scanned if scanned is not None else len(records),
                       "truncated": truncated})


# One phantom (auto-healable), one crashed + one stale-checkout (flag-only).
_RECORDS = [
    {"plan_id": "hearth-old", "age_s": 999999, "has_result": False},
    {"plan_id": "hearth-crash", "age_s": 10, "has_result": True, "stub": True,
     "status": "errored", "error": "errored (isolated): x"},
    {"plan_id": "hearth-stale", "age_s": 10, "has_result": True, "winner": "w",
     "promoted": False, "n_questions": 1,
     "questions_text": "the hearth directory does not exist"},
]


class MastersPetTests(TestCase):
    def test_dry_run_partitions_but_does_not_heal(self):
        with patch("subprocess.run", return_value=_completed(stdout=_gather(_RECORDS))) as run:
            out = masters_pet(apply=False)
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
            out = masters_pet(apply=True)
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
            out = masters_pet(apply=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["healable"], [])
        self.assertNotIn("healed", out)
        self.assertEqual(run.call_count, 1)

    def test_ssh_failure_is_clean(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = masters_pet(apply=True)
        self.assertFalse(out["ok"])
        self.assertIn("TimeoutExpired", out["error"])

    def test_reports_its_own_coverage(self):
        # "healable: []" must be readable as "nothing to heal", not "nothing I
        # could see" — so the sweep reports what it looked at (ADR-0033).
        with patch("subprocess.run",
                   return_value=_completed(stdout=_gather(_RECORDS, scanned=187, truncated=0))):
            out = masters_pet(apply=False)
        self.assertEqual(out["scanned"], 187)
        self.assertEqual(out["considered"], len(_RECORDS))
        self.assertEqual(out["truncated"], 0)
        self.assertNotIn("truncation_note", out)

    def test_truncated_sweep_is_named_in_the_output(self):
        with patch("subprocess.run",
                   return_value=_completed(stdout=_gather(_RECORDS, scanned=187, truncated=62))):
            out = masters_pet(apply=False)
        self.assertEqual(out["truncated"], 62)
        self.assertIn("truncation_note", out)
        self.assertIn("62", out["truncation_note"])
        # The note must say what truncation can and cannot hide.
        self.assertIn("no healable phantom is hidden", out["truncation_note"])

    def test_undispatched_aged_run_is_healable(self):
        # The 51-day regression: runs/spine-hello had no nodes.json, so it was
        # invisible to the sweep and masters_pet answered healable: [] while
        # queue_status read running=2.
        rec = [{"plan_id": "spine-hello", "age_s": 4406400,
                "dispatched": False, "has_result": False}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather(rec))):
            out = masters_pet(apply=False)
        self.assertEqual([g["kind"] for g in out["healable"]], ["phantom_in_flight"])
        self.assertIn("never dispatched", out["healable"][0]["detail"])

