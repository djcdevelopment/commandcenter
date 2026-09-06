from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import mkdtemp
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

    def test_expectations_block_is_reported_even_with_no_sidecar(self):
        with patch("subprocess.run", return_value=_completed(stdout=_gather(_RECORDS))):
            out = masters_pet(apply=False)
        self.assertIn("expectations", out)
        self.assertEqual(out["expectations"]["loaded"], 0)
        self.assertEqual(out["spared"], [])

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


# The four-quadrant fixture. (a) and (b) carry a 6-hour expectation in HEARTH's
# sidecar; (c) and (d) carry none and differ only in whether their run dir is
# still being written to.
SIX_HOURS = 21600
QUADRANT_RECORDS = [
    # (a) long-lived, still inside its declared lifetime  -> spared
    {"plan_id": "hearth-a-inside-lifetime", "age_s": 3 * 3600,
     "dispatched": True, "has_result": False},
    # (b) past its declared lifetime, nothing writing     -> healed
    {"plan_id": "hearth-b-overrun", "age_s": 7 * 3600,
     "dispatched": True, "has_result": False},
    # (c) no expectation, but the run dir changed a minute ago -> spared
    {"plan_id": "hearth-c-busy", "age_s": 2 * 3600,
     "dispatched": True, "has_result": False, "last_activity_s": 60},
    # (d) no expectation, nothing written for two hours   -> healed (unchanged)
    {"plan_id": "hearth-d-dead", "age_s": 2 * 3600,
     "dispatched": True, "has_result": False, "last_activity_s": 7200},
]
QUADRANT_SIDECAR = {
    "hearth-a-inside-lifetime": {"max_age_s": SIX_HOURS, "task_class": "build",
                                 "submitted_at": "2026-09-06T00:00:00Z"},
    "hearth-b-overrun": {"max_age_s": SIX_HOURS, "task_class": "build",
                         "submitted_at": "2026-09-06T00:00:00Z"},
}


class LongRunWatchdogSafetyTests(TestCase):
    """The point of the whole work item: `masters_pet(apply=True)` must heal the
    dead runs and leave the live long ones alone — visibly."""

    def setUp(self):
        self._tmp = mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.enterContext(patch.dict(os.environ, {"HEARTH_ROOT": self._tmp}))
        self.sidecar = Path(self._tmp) / "var" / "task_lane" / "expectations.json"

    def _write_sidecar(self, doc):
        self.sidecar.parent.mkdir(parents=True, exist_ok=True)
        self.sidecar.write_text(doc if isinstance(doc, str) else json.dumps(doc),
                                encoding="utf-8")

    def _apply(self, records=None):
        """masters_pet(apply=True) over the fixture; returns (out, heal_plan_ids)."""
        records = QUADRANT_RECORDS if records is None else records
        captured: list[str] = []

        def runner(args, **kw):
            captured.append(args[-1])
            if len(captured) == 1:
                return _completed(stdout=_gather(records))
            return _completed(stdout=json.dumps({"healed": []}))

        with patch("subprocess.run", side_effect=runner):
            out = masters_pet(apply=True)
        return out, captured

    def test_apply_heals_only_the_dead_runs_and_spares_the_live_ones(self):
        self._write_sidecar(QUADRANT_SIDECAR)
        out, captured = self._apply()
        self.assertTrue(out["ok"])
        self.assertEqual([g["plan_id"] for g in out["healable"]],
                         ["hearth-b-overrun", "hearth-d-dead"])
        self.assertEqual([(s["plan_id"], s["rule"]) for s in out["spared"]],
                         [("hearth-a-inside-lifetime", "max_age_s"),
                          ("hearth-c-busy", "activity")])
        # The heal script is asked for exactly the two dead runs, nobody else.
        import base64
        self.assertEqual(len(captured), 2)
        heal_src = base64.b64decode(
            captured[1].split("echo ", 1)[1].split(" | base64", 1)[0]).decode("utf-8")
        self.assertIn("hearth-b-overrun", heal_src)
        self.assertIn("hearth-d-dead", heal_src)
        self.assertNotIn("hearth-a-inside-lifetime", heal_src)
        self.assertNotIn("hearth-c-busy", heal_src)

    def test_the_phantom_detail_names_the_threshold_that_was_exceeded(self):
        self._write_sidecar(QUADRANT_SIDECAR)
        out, _ = self._apply()
        by_id = {g["plan_id"]: g for g in out["healable"]}
        self.assertIn("over the 360 min threshold", by_id["hearth-b-overrun"]["detail"])
        self.assertIn("over the 30 min threshold", by_id["hearth-d-dead"]["detail"])

    def test_expectations_block_reports_loaded_and_applied(self):
        self._write_sidecar(QUADRANT_SIDECAR)
        out, _ = self._apply()
        self.assertEqual(out["expectations"]["loaded"], 2)
        self.assertEqual(out["expectations"]["applied"], 2)
        self.assertEqual(out["expectations"]["pruned"], 0)
        self.assertNotIn("warning", out["expectations"])

    def test_without_the_sidecar_the_long_run_is_stubbed_the_old_way(self):
        # The mutation check: remove HEARTH's memory and (a) becomes a phantom
        # again — so the sparing above is genuinely doing the work.
        out, _ = self._apply()
        self.assertEqual([g["plan_id"] for g in out["healable"]],
                         ["hearth-a-inside-lifetime", "hearth-b-overrun", "hearth-d-dead"])
        self.assertEqual([(s["plan_id"], s["rule"]) for s in out["spared"]],
                         [("hearth-c-busy", "activity")])

    def test_dry_run_still_makes_no_heal_call(self):
        self._write_sidecar(QUADRANT_SIDECAR)
        with patch("subprocess.run",
                   return_value=_completed(stdout=_gather(QUADRANT_RECORDS))) as run:
            out = masters_pet(apply=False)
        self.assertTrue(out["dry_run"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual([g["plan_id"] for g in out["healable"]],
                         ["hearth-b-overrun", "hearth-d-dead"])
        self.assertNotIn("healed", out)

    def test_a_corrupt_sidecar_warns_and_masters_pet_still_runs(self):
        self._write_sidecar("}}} not json {{{")
        out, _ = self._apply()
        self.assertTrue(out["ok"])
        self.assertIn("not valid JSON", out["expectations"]["warning"])
        # Degrades to today's behaviour — nothing is spared by a memory we
        # cannot read, and the operator is told why.
        self.assertIn("hearth-a-inside-lifetime", [g["plan_id"] for g in out["healable"]])

    def test_a_finished_runs_entry_is_pruned_from_the_sidecar(self):
        self._write_sidecar(QUADRANT_SIDECAR)
        records = list(QUADRANT_RECORDS)
        records[1] = {"plan_id": "hearth-b-overrun", "age_s": 7 * 3600, "has_result": True,
                      "status": "ok", "winner": "w", "promoted": True,
                      "winner_grade": "A", "winner_files": 12, "n_questions": 0}
        out, _ = self._apply(records)
        self.assertEqual(out["expectations"]["pruned"], 1)
        remaining = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(list(remaining), ["hearth-a-inside-lifetime"])

