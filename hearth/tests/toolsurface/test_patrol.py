from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import MagicMock, patch

from hearth.toolsurface.patrol import FINISHED_RECORD_CAP, _GATHER_SRC, patrol


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather_payload(records, scanned=None, truncated=0, undispatched=0):
    return json.dumps({"records": records,
                       "scanned": scanned if scanned is not None else len(records),
                       "truncated": truncated, "undispatched": undispatched})


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
        self.assertEqual(out["truncated"], 0)
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


class GatherSourceTests(TestCase):
    """Execute the real remote-gather source against a temp runs/ dir.

    The bug this pins (2026-08-20): the gather filtered on nodes.json, so 62 of
    the conductor's 187 run dirs could never be seen — including two that held
    queue_status at running=2 for 51 days with masters_pet returning
    ``healable: []``. ONE definition of a run now: every runs/<id>/ dir.
    """

    def _run_gather_source(self, tmp) -> dict:
        import contextlib
        import io as _io
        cwd = os.getcwd()
        buf = _io.StringIO()
        os.chdir(tmp)
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(_GATHER_SRC, "<gather>", "exec"), {})
        finally:
            os.chdir(cwd)
        return json.loads(buf.getvalue())

    def _make_run(self, tmp, name, nodes=True, result=None):
        d = Path(tmp) / "runs" / name
        d.mkdir(parents=True)
        if nodes:
            (d / "nodes.json").write_text("{}", encoding="utf-8")
        if result is not None:
            (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return d

    def test_run_dir_without_nodes_json_is_reported_not_skipped(self) -> None:
        tmp = mkdtemp()
        try:
            self._make_run(tmp, "spine-hello", nodes=False)          # the 51-day phantom
            self._make_run(tmp, "dispatched-live", nodes=True)
            payload = self._run_gather_source(tmp)
            by_id = {r["plan_id"]: r for r in payload["records"]}
            self.assertEqual(set(by_id), {"spine-hello", "dispatched-live"})
            self.assertFalse(by_id["spine-hello"]["dispatched"])
            self.assertFalse(by_id["spine-hello"]["has_result"])
            self.assertTrue(by_id["dispatched-live"]["dispatched"])
            self.assertEqual(payload["scanned"], 2)
            self.assertEqual(payload["undispatched"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_undispatched_phantom_is_healable_end_to_end(self) -> None:
        # The whole point: an undispatched aged dir must reach the healer.
        from hearth.health.gaps import PHANTOM_AGE_S, scan_runs
        tmp = mkdtemp()
        try:
            d = self._make_run(tmp, "spine-hello", nodes=False)
            old = time.time() - (PHANTOM_AGE_S + 600)
            os.utime(d, (old, old))
            payload = self._run_gather_source(tmp)
            gaps = scan_runs(payload["records"])
            self.assertEqual([g.kind for g in gaps], ["phantom_in_flight"])
            self.assertIn("never dispatched", gaps[0].detail)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unfinished_runs_are_never_truncated(self) -> None:
        tmp = mkdtemp()
        try:
            for i in range(FINISHED_RECORD_CAP + 20):
                self._make_run(tmp, f"done-{i:03d}", result={"status": "ok"})
            for i in range(5):
                self._make_run(tmp, f"pending-{i}", nodes=False)
            payload = self._run_gather_source(tmp)
            pending = [r for r in payload["records"] if not r["has_result"]]
            finished = [r for r in payload["records"] if r["has_result"]]
            self.assertEqual(len(pending), 5, "every unfinished run must survive the cap")
            self.assertEqual(len(finished), FINISHED_RECORD_CAP)
            self.assertEqual(payload["scanned"], FINISHED_RECORD_CAP + 25)
            self.assertEqual(payload["truncated"], 20)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stray_file_in_runs_is_ignored(self) -> None:
        tmp = mkdtemp()
        try:
            (Path(tmp) / "runs").mkdir()
            (Path(tmp) / "runs" / "README.md").write_text("not a run", encoding="utf-8")
            self._make_run(tmp, "real", nodes=True, result={"status": "ok"})
            payload = self._run_gather_source(tmp)
            self.assertEqual([r["plan_id"] for r in payload["records"]], ["real"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_runs_dir_is_empty_not_an_error(self) -> None:
        tmp = mkdtemp()
        try:
            payload = self._run_gather_source(tmp)
            self.assertEqual(payload["records"], [])
            self.assertEqual(payload["scanned"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PatrolCoverageTests(TestCase):
    def test_truncation_and_undispatched_are_surfaced(self) -> None:
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]
        payload = _gather_payload(records, scanned=187, truncated=62, undispatched=3)
        with patch("subprocess.run", return_value=_completed(stdout=payload)):
            out = patrol(refresh=False)
        self.assertEqual(out["scanned"], 187)
        self.assertEqual(out["considered"], 1)
        self.assertEqual(out["truncated"], 62)
        self.assertEqual(out["undispatched"], 3)

    def test_older_payload_without_new_keys_defaults_to_zero(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout=json.dumps({"records": [], "scanned": 0}))):
            out = patrol(refresh=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["truncated"], 0)
        self.assertEqual(out["undispatched"], 0)

