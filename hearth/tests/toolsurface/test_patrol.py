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


def _rung(verdict, **over):
    """A hearth.health.rungstate.rung_state-shaped dict pinned to one verdict."""
    st = {"rung": "omen-arc", "port": 8082, "verdict": verdict,
          "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
          "envelope": {"fail_below": 0.8, "warn_below": 0.9},
          "observed_tok_s": 107.5, "observed_at": "2026-09-03T02:28:20-07:00",
          "observed_age_s": 100.0, "frac_of_baseline": 1.0142,
          "prefill_stall_recent": False, "last_ping_ok": True, "deep_samples": 3,
          "excluded_windows": [], "note": "envelope is of THIS baseline epoch, not of capacity"}
    st.update(over)
    return st


_AT_RATE = _rung("at_rate")
_DEGRADED = _rung("degraded", observed_tok_s=65.0, frac_of_baseline=0.6132)

# The rung-state spell rides every patrol and reads the LIVE keep-alive tail by
# default — an environment fact, not these tests' subject (same lesson as the
# scan_knowledge patch below). Every gap-asserting test pins at_rate.
_PIN_AT_RATE = ("hearth.toolsurface.patrol._live_rung_state", )


class PatrolTests(TestCase):
    def setUp(self):
        self.enterContext(patch(*_PIN_AT_RATE, return_value=_AT_RATE))

    def test_reports_gaps_from_gathered_records(self):
        records = [
            {"plan_id": "hearth-old", "age_s": 5000, "has_result": False},
            {"plan_id": "hearth-crash", "age_s": 10, "has_result": True,
             "stub": True, "status": "errored", "error": "errored (isolated): x"},
            {"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
             "status": "ok", "winner": "am4-worker-1", "promoted": True,
             "winner_grade": "B", "winner_files": 205, "n_questions": 0},
        ]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records, scanned=143))),              patch("hearth.toolsurface.patrol.scan_knowledge", return_value=[]):
            out = patrol(refresh=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["scanned"], 143)
        self.assertEqual(out["considered"], 3)
        self.assertEqual(out["truncated"], 0)
        kinds = sorted(g["kind"] for g in out["gaps"])
        self.assertEqual(kinds, ["crashed_isolated", "phantom_in_flight"])
        self.assertEqual(out["summary"]["total"], 2)

    def test_clean_fleet_reports_no_gaps(self):
        # scan_knowledge is patched out: it reads the REAL knowledge/capacity.json,
        # whose age is an environment fact, not this test's subject (it bit on
        # 2026-08-21 when the projection crossed 24h during the fleet hold).
        records = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))),              patch("hearth.toolsurface.patrol.scan_knowledge", return_value=[]):
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


    # --- last_activity_s: the newest mtime under runs/<id>/, so a long build
    # that is still writing is distinguishable from a dead one that stopped.

    def _age(self, path, seconds_ago) -> None:
        when = time.time() - seconds_ago
        os.utime(path, (when, when))

    def test_last_activity_reflects_the_newest_file_under_the_run_dir(self) -> None:
        tmp = mkdtemp()
        try:
            d = self._make_run(tmp, "long-build", nodes=True)
            (d / "build.log").write_text("working", encoding="utf-8")
            self._age(d / "nodes.json", 9000)
            self._age(d / "build.log", 42)
            self._age(d, 9000)
            payload = self._run_gather_source(tmp)
            rec = payload["records"][0]
            self.assertAlmostEqual(rec["last_activity_s"], 42, delta=5)
            # age_s still comes from nodes.json — activity is a separate signal.
            self.assertAlmostEqual(rec["age_s"], 9000, delta=5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_file_in_a_nested_subdirectory_counts_as_activity(self) -> None:
        tmp = mkdtemp()
        try:
            d = self._make_run(tmp, "nested", nodes=True)
            sub = d / "builds" / "cc-builder-2"
            sub.mkdir(parents=True)
            (sub / "out.txt").write_text("x", encoding="utf-8")
            self._age(d / "nodes.json", 9000)
            self._age(sub / "out.txt", 30)
            self._age(sub, 9000)
            self._age(d / "builds", 9000)
            self._age(d, 9000)
            payload = self._run_gather_source(tmp)
            self.assertAlmostEqual(payload["records"][0]["last_activity_s"], 30, delta=5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_run_dir_falls_back_to_the_dir_mtime(self) -> None:
        tmp = mkdtemp()
        try:
            d = self._make_run(tmp, "empty", nodes=False)
            self._age(d, 1234)
            payload = self._run_gather_source(tmp)
            rec = payload["records"][0]
            self.assertAlmostEqual(rec["last_activity_s"], 1234, delta=5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_finished_runs_carry_no_activity_field(self) -> None:
        tmp = mkdtemp()
        try:
            self._make_run(tmp, "done", nodes=True, result={"status": "ok"})
            self._make_run(tmp, "pending", nodes=True)
            payload = self._run_gather_source(tmp)
            by_id = {r["plan_id"]: r for r in payload["records"]}
            self.assertNotIn("last_activity_s", by_id["done"])
            self.assertIn("last_activity_s", by_id["pending"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_file_vanishing_mid_walk_does_not_lose_the_record(self) -> None:
        # A builder rotating a log can delete a file between os.walk listing it
        # and getmtime reading it. That race must cost the one file, not the run.
        tmp = mkdtemp()
        try:
            d = self._make_run(tmp, "racy", nodes=True)
            (d / "doomed.log").write_text("gone by the time we look", encoding="utf-8")
            (d / "survivor.log").write_text("still here", encoding="utf-8")
            self._age(d / "nodes.json", 9000)
            self._age(d / "survivor.log", 77)
            self._age(d, 9000)
            real_getmtime = os.path.getmtime

            def flaky(path):
                if str(path).endswith("doomed.log"):
                    raise OSError(2, "No such file or directory")
                return real_getmtime(path)

            with patch("os.path.getmtime", side_effect=flaky):
                payload = self._run_gather_source(tmp)
            rec = payload["records"][0]
            self.assertEqual(rec["plan_id"], "racy")
            self.assertAlmostEqual(rec["last_activity_s"], 77, delta=5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_gather_stdout_stays_one_json_document(self) -> None:
        # The gather source is shipped to the conductor and its stdout is parsed
        # as JSON; the heartbeat walk must not print a thing.
        import contextlib
        import io as _io
        tmp = mkdtemp()
        try:
            self._make_run(tmp, "a", nodes=True)
            cwd = os.getcwd()
            buf = _io.StringIO()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(_GATHER_SRC, "<gather>", "exec"), {})
            finally:
                os.chdir(cwd)
            raw = buf.getvalue()
            self.assertEqual(raw.count("\n"), 1, raw)
            json.loads(raw)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PatrolExpectationsTests(TestCase):
    """patrol loads HEARTH's expectation sidecar, applies it, prunes it, and
    reports both the sparing and the state of that memory."""

    def setUp(self):
        self.enterContext(patch(*_PIN_AT_RATE, return_value=_AT_RATE))
        self.enterContext(patch("hearth.toolsurface.patrol.scan_knowledge", return_value=[]))
        self._tmp = mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.enterContext(patch.dict(os.environ, {"HEARTH_ROOT": self._tmp}))
        self.sidecar = Path(self._tmp) / "var" / "task_lane" / "expectations.json"

    def _write_sidecar(self, doc) -> None:
        self.sidecar.parent.mkdir(parents=True, exist_ok=True)
        self.sidecar.write_text(doc if isinstance(doc, str) else json.dumps(doc),
                                encoding="utf-8")

    def _patrol(self, records):
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))):
            return patrol(refresh=False)

    def test_no_sidecar_means_an_empty_block_and_no_directory_created(self) -> None:
        out = self._patrol([{"plan_id": "young", "age_s": 60, "has_result": False}])
        self.assertEqual(out["expectations"], {"loaded": 0, "applied": 0, "pruned": 0})
        self.assertEqual(out["spared"], [])
        self.assertFalse((Path(self._tmp) / "var").exists())

    def test_a_recorded_expectation_spares_a_long_run_from_the_phantom_spell(self) -> None:
        self._write_sidecar({"hearth-long": {"max_age_s": 21600,
                                             "submitted_at": "2026-09-06T00:00:00Z"}})
        out = self._patrol([{"plan_id": "hearth-long", "age_s": 3 * 3600,
                             "dispatched": True, "has_result": False}])
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["spared"], [{"plan_id": "hearth-long", "rule": "max_age_s",
                                          "detail": out["spared"][0]["detail"]}])
        self.assertIn("360 min", out["spared"][0]["detail"])
        self.assertEqual(out["expectations"]["loaded"], 1)
        self.assertEqual(out["expectations"]["applied"], 1)

    def test_a_run_past_its_recorded_lifetime_is_still_a_phantom(self) -> None:
        self._write_sidecar({"hearth-long": {"max_age_s": 21600,
                                             "submitted_at": "2026-09-06T00:00:00Z"}})
        out = self._patrol([{"plan_id": "hearth-long", "age_s": 7 * 3600,
                             "dispatched": True, "has_result": False}])
        self.assertEqual([g["kind"] for g in out["gaps"]], ["phantom_in_flight"])
        self.assertEqual(out["spared"], [])

    def test_a_finished_runs_entry_is_pruned_and_the_file_rewritten(self) -> None:
        self._write_sidecar({
            "hearth-done": {"max_age_s": 600, "submitted_at": "2026-09-06T00:00:00Z"},
            "hearth-live": {"max_age_s": 21600, "submitted_at": "2026-09-06T00:00:00Z"},
        })
        out = self._patrol([
            {"plan_id": "hearth-done", "age_s": 60, "has_result": True, "status": "ok",
             "winner": "w", "promoted": True, "winner_grade": "A", "winner_files": 9,
             "n_questions": 0},
            {"plan_id": "hearth-live", "age_s": 3 * 3600, "has_result": False},
        ])
        self.assertEqual(out["expectations"]["pruned"], 1)
        remaining = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(list(remaining), ["hearth-live"])

    def test_a_corrupt_sidecar_is_a_warning_not_a_failed_patrol(self) -> None:
        self._write_sidecar("{{{ not json")
        out = self._patrol([{"plan_id": "x", "age_s": 60, "has_result": False}])
        self.assertTrue(out["ok"])
        self.assertEqual(out["expectations"]["loaded"], 0)
        self.assertIn("not valid JSON", out["expectations"]["warning"])

    def test_activity_alone_spares_a_run_with_no_recorded_expectation(self) -> None:
        out = self._patrol([{"plan_id": "busy", "age_s": 2 * 3600, "has_result": False,
                             "last_activity_s": 30}])
        self.assertEqual(out["gaps"], [])
        self.assertEqual([s["rule"] for s in out["spared"]], ["activity"])
        self.assertEqual(out["expectations"]["applied"], 0)


class PatrolRungStateTests(TestCase):
    """The rung-state spell (ADR-0044) rides every patrol, guarded and lazy."""

    _OK_RECORDS = [{"plan_id": "pour-ok", "age_s": 9000, "has_result": True,
                    "status": "ok", "winner": "x", "promoted": True,
                    "winner_grade": "A", "winner_files": 100, "n_questions": 0}]

    def _patrol(self, rung_reader):
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(self._OK_RECORDS))),              patch("hearth.toolsurface.patrol.scan_knowledge", return_value=[]),              patch("hearth.toolsurface.patrol._live_rung_state", rung_reader):
            return patrol(refresh=False)

    def test_degraded_rung_rides_as_a_high_gap(self) -> None:
        out = self._patrol(lambda: _DEGRADED)
        self.assertTrue(out["ok"])
        self.assertEqual([g["kind"] for g in out["gaps"]], ["rung_degraded"])
        gap = out["gaps"][0]
        self.assertEqual(gap["severity"], "high")
        self.assertEqual(gap["plan_id"], "omen-arc")
        self.assertIn("65.0/106.0 tok/s", gap["detail"])
        self.assertEqual(out["summary"]["by_kind"], {"rung_degraded": 1})
        self.assertEqual(out["rung_state"]["verdict"], "degraded")
        self.assertTrue(out["rung_state"]["ok"])
        self.assertEqual(out["rung_state"]["observed_tok_s"], 65.0)

    def test_stale_rung_is_a_warn_gap_never_at_rate(self) -> None:
        out = self._patrol(lambda: _rung("stale", observed_age_s=1200.0))
        self.assertEqual([(g["kind"], g["severity"]) for g in out["gaps"]], [("rung_stale", "warn")])

    def test_at_rate_rung_adds_no_gap_but_is_surfaced(self) -> None:
        out = self._patrol(lambda: _AT_RATE)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["rung_state"]["verdict"], "at_rate")
        self.assertTrue(out["rung_state"]["ok"])

    def test_unreachable_rung_is_liveness_not_a_gap(self) -> None:
        # Production down (the 2026-09-03 imagegen tenancy window): the verdict is
        # surfaced, but "down" belongs to the watchdog's inventory probe.
        out = self._patrol(lambda: _rung("unreachable", last_ping_ok=False))
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["rung_state"]["verdict"], "unreachable")

    def test_reader_raising_never_fails_the_patrol(self) -> None:
        def boom():
            raise OSError("keep-alive tail unreadable")
        out = self._patrol(boom)
        self.assertTrue(out["ok"])
        self.assertEqual(out["gaps"], [])
        self.assertFalse(out["rung_state"]["ok"])
        self.assertIn("OSError", out["rung_state"]["error"])

    def test_reader_error_shape_is_ok_false_not_a_gap(self) -> None:
        # live_rung_state's own never-raise shape: verdict unknown + error.
        out = self._patrol(lambda: {"rung": "omen-arc", "port": None, "verdict": "unknown",
                                    "error": "ValueError: bad json"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["gaps"], [])
        self.assertFalse(out["rung_state"]["ok"])
        self.assertEqual(out["rung_state"]["verdict"], "unknown")

    def test_reader_returning_non_dict_is_ok_false(self) -> None:
        out = self._patrol(lambda: "degraded")
        self.assertTrue(out["ok"])
        self.assertFalse(out["rung_state"]["ok"])
        self.assertIn("TypeError", out["rung_state"]["error"])

    def test_reader_is_lazily_bound_on_first_use(self) -> None:
        from hearth.toolsurface import patrol as mod
        with patch.object(mod, "_live_rung_state", None):
            mod._ensure_rung_state_import()
            from hearth.health.rungstate import live_rung_state
            self.assertIs(mod._live_rung_state, live_rung_state)

    def test_rung_gap_rides_beside_run_gaps(self) -> None:
        records = [{"plan_id": "hearth-old", "age_s": 5000, "has_result": False}]
        with patch("subprocess.run", return_value=_completed(stdout=_gather_payload(records))),              patch("hearth.toolsurface.patrol.scan_knowledge", return_value=[]),              patch("hearth.toolsurface.patrol._live_rung_state", lambda: _DEGRADED):
            out = patrol(refresh=False)
        self.assertEqual(sorted(g["kind"] for g in out["gaps"]),
                         ["phantom_in_flight", "rung_degraded"])
        self.assertEqual(out["summary"]["total"], 2)


class PatrolCoverageTests(TestCase):
    def setUp(self):
        self.enterContext(patch(*_PIN_AT_RATE, return_value=_AT_RATE))

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

