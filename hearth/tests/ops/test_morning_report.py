"""The morning report's rows for deliveries (T2, lap 21 wave 6), pinned to what real reports printed.

Observations: ~/work/delivery-plan/evidence/rehearsal/morning-report-0738Z.md (rows for the staged items work in
flight, the failed unified_diff, the carried deliveries and the finished items work) and rehearsal/RESULT.md. The script
has no .py suffix: it is loaded with SourceFileLoader under a temp HEARTH_ROOT, its paths are patched to a temp tree and
its `_reader` to a recorder, so no service is built, no lane probed and the live runs directory is never read.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "hearth-morning-report"

CARRY_ID = "work_aa6b13cd9cc749c0800024b44836f909"      # accepted, carry, 62 of 69 quotes
ITEMS_DONE_ID = "work_96772bb25d3adb56b7a793fe1337755f"  # items, 81 agreed, 18 settled, 2 NOT VERIFIED
ITEMS_LIVE_ID = "work_bfc8e86f90194030ba215e702ccdfcda"  # items in flight at 07:37Z
WHOLE_ID = "work_4f36d04f5ec9b6fe40827dd8e834cf1b"      # whole_file candidate
DIFF_ID = "work_2e663a07fd705ae3826f66347db79430"       # unified_diff, failed at git apply
PLAIN_ID = "work_" + "c" * 32


def load_script():
    loader = importlib.machinery.SourceFileLoader("hearth_morning_report_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    # compiled from the source bytes, never from a cached .pyc: a cache keyed on mtime and size can serve stale code
    exec(loader.source_to_code(loader.get_data(str(SCRIPT)), str(SCRIPT)), mod.__dict__)
    return mod


def manifest(work_id: str, status: str, **over) -> dict:
    d = {"schema": "local-work-manifest.v1", "work_id": work_id, "status": status, "repo": "/repo", "artifact_kind": "markdown",
         "route": {"selected_lane": "deep", "model": "qwen3.8-27b"}, "events": []}
    d.update(over)
    return d


class MorningReportTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.hearth_root = self.tmp / "hearth-production"
        (self.tmp / "repo" / "runs" / "operator").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HEARTH_ROOT": str(self.hearth_root), "HEARTH_BACKLOG_ROOT": str(self.hearth_root / "var" / "backlog")})
        env.start()
        self.addCleanup(env.stop)
        saved_path = list(sys.path)   # build() puts REPO first on sys.path
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        self.mod = load_script()
        self.reader_calls: list[str] = []
        self.reconciled: dict = {}
        for patcher in (mock.patch.object(self.mod, "REPO", self.tmp / "repo"),
                        mock.patch.object(self.mod, "HEARTH_ROOT", self.hearth_root),
                        mock.patch.object(self.mod, "BACKLOG", self.hearth_root / "var" / "backlog"),
                        mock.patch.object(self.mod, "LEDGER", self.hearth_root / "var" / "ledger" / "events.ndjson"),
                        mock.patch.object(self.mod, "_reader", return_value=mock.Mock(reconcile=self.reconcile)),
                        mock.patch.object(self.mod, "_health", return_value="200"),
                        mock.patch("fleet.presence_linux.report", return_value={"away": False, "present_reasons": ["test"], "idle_ms": 0, "mode": "x"}),
                        mock.patch("fleet.environment.stamp", return_value={"name": "dev", "set_by": "t", "set_at": "t", "until": None, "expired": False})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reconcile(self, work_id: str) -> dict:
        self.reader_calls.append(work_id)
        return self.reconciled[work_id]

    def put(self, d: dict) -> Path:
        p = self.tmp / "repo" / "runs" / "operator" / d["work_id"] / "work-manifest.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(d), encoding="utf-8")
        return p

    def row(self, work_id: str) -> list[str]:
        """The report's cells for one work: [status, work, lane / model, repo, kind, apply check, manifest]."""
        text = self.mod.build(1)
        lines = [ln for ln in text.splitlines() if f"`{work_id}`" in ln]
        self.assertEqual(len(lines), 1, text)
        return [c.strip() for c in lines[0].strip().strip("|").split(" | ")]

    def test_a_carried_work_in_flight_is_not_reconciled_and_prints_its_stage(self) -> None:
        """Delivery docs: the report "never reconciles a staged work ... shows the stored file, with the stage and batch of
        one in flight, because reconciling there could dispatch the next stage from the wrong process"."""
        self.put(manifest(CARRY_ID, "running", delivery=True, carry={"stage": "attach", "batch": 2, "batches": 3}, route={"selected_lane": "deep", "model": "qwen3.8-27b", "procedure": "carry"}))
        cells = self.row(CARRY_ID)
        self.assertEqual(cells[0], "**running**")
        self.assertEqual(cells[5], "carry, in flight: stage attach batch 2 of 3")
        self.assertEqual(self.reader_calls, [])

    def test_a_carried_work_before_its_first_batch_prints_the_stage_alone(self) -> None:
        self.put(manifest(CARRY_ID, "queued", delivery=True, carry={"stage": "work"}))
        self.assertEqual(self.row(CARRY_ID)[5], "carry, in flight: stage work")
        self.assertEqual(self.reader_calls, [])

    def test_an_items_work_in_flight_prints_stage_jobs_and_items(self) -> None:
        """rehearsal/morning-report-0738Z.md: `work_bfc8e86f` read "items, in flight: stage read, 48 jobs for 91 items"."""
        self.put(manifest(ITEMS_LIVE_ID, "running", delivery=True, route={"selected_lane": "fast", "model": "qwen3-30b-a3b", "procedure": "items"},
                          items={"kind": "env_reads", "stage": "read", "count": 91, "jobs": [{"job_id": f"job_{i}"} for i in range(48)]}))
        cells = self.row(ITEMS_LIVE_ID)
        self.assertEqual(cells[0:3], ["**running**", f"`{ITEMS_LIVE_ID}`", "fast / qwen3-30b-a3b"])
        self.assertEqual(cells[5], "items, in flight: stage read, 48 jobs for 91 items")
        self.assertEqual(self.reader_calls, [])

    def test_an_ordinary_work_in_flight_is_still_reconciled(self) -> None:
        """The control for the two tests above: only a manifest with a `carry` or `items` block is held back. rehearsal
        finding 1 (`work_2e663a07` stayed `queued` until something asked for it) is why the report asks the reader."""
        self.put(manifest(PLAIN_ID, "queued"))
        self.reconciled[PLAIN_ID] = manifest(PLAIN_ID, "failed", failure="x")
        self.assertEqual(self.row(PLAIN_ID)[0], "**failed**")
        self.assertEqual(self.reader_calls, [PLAIN_ID])

    def test_a_finished_carried_delivery_row(self) -> None:
        """rehearsal/morning-report-0738Z.md: `work_aa6b13cd` read "carry, deterministic fail, quotes 62/69 resolved, report
        `.../candidate.md`" and was accepted; the manifest's route.procedure is "carry"."""
        m = self.put(manifest(CARRY_ID, "accepted", delivery=True, route={"selected_lane": "deep", "model": "qwen3.8-27b", "procedure": "carry"},
                              carry={"stage": "render", "batch": 3, "batches": 3},
                              delivery_summary={"claims": 69, "missing": 7, "deterministic": "fail", "unsupported": 7, "words": 273}))
        cells = self.row(CARRY_ID)
        self.assertEqual(cells[0:3], ["**accepted**", f"`{CARRY_ID}`", "deep / qwen3.8-27b"])
        self.assertEqual(cells[5], f"carry, deterministic fail, quotes 62/69 resolved, report `{m.parent / 'candidate.md'}`")
        self.assertEqual(self.reader_calls, [])

    def test_a_finished_items_delivery_row_counts_agreed_settled_and_unverified(self) -> None:
        """rehearsal/RESULT.md: `work_96772bb2` "81 agreed, 18 settled, 2 NOT VERIFIED; 101 of 101 quotes"."""
        m = self.put(manifest(ITEMS_DONE_ID, "rejected", delivery=True, route={"selected_lane": "fast", "model": "qwen3-30b-a3b", "procedure": "items"},
                              items={"count": 101, "stage": "done", "report": {"agreed": 81, "settled": 18, "unverified": 2, "items": 101}},
                              delivery_summary={"claims": 101, "missing": 0, "deterministic": "pass", "unsupported": 0, "words": 705}))
        self.assertEqual(self.row(ITEMS_DONE_ID)[5],
                         f"items, 81 agreed, 18 settled, 2 NOT VERIFIED, deterministic pass, quotes 101/101 resolved, report `{m.parent / 'candidate.md'}`")

    def test_a_delivery_without_a_summary_names_its_failure(self) -> None:
        """A delivery that failed before a summary existed prints its procedure and the first 80 characters of the failure."""
        self.put(manifest(CARRY_ID, "failed", delivery=True, failure="carry stage check: " + "x" * 100, route={"selected_lane": "deep", "model": "m", "procedure": "carry"}))
        self.assertEqual(self.row(CARRY_ID)[5], "carry, no delivery summary: " + ("carry stage check: " + "x" * 100)[:80])

    def test_a_whole_file_candidate_prints_its_hunks(self) -> None:
        """laps/21 row M1: the row for a whole_file candidate reads `whole_file, 3 hunks: 53, 84-85, 326 (+3/-4)` (the change
        at line 326 of `work_4f36d04f` is the one the grader rejected for turning an unrelated local-time read into UTC)."""
        self.put(manifest(WHOLE_ID, "awaiting_review", artifact_kind="whole_file", target_path="fleet/experiment_linux.py",
                          changes={"hunks": [{"old": [53, 53], "new": [53, 53]}, {"old": [84, 85], "new": [84, 85]}, {"old": [326, 326], "new": [326, 326]}],
                                   "added": 3, "removed": 4}))
        self.assertEqual(self.row(WHOLE_ID)[5], "whole_file, 3 hunks: 53, 84-85, 326 (+3/-4)")

    def test_a_pure_insertion_names_where_it_lands_and_one_hunk_is_singular(self) -> None:
        self.assertEqual(self.mod._changes_cell("whole_file", {"hunks": [{"old": [10, 9], "old_empty": True, "new": [10, 12]}], "added": 3, "removed": 0}),
                         "whole_file, 1 hunk: after 10 (+3/-0)")

    def test_more_than_eight_hunks_are_elided(self) -> None:
        hunks = [{"old": [i * 10, i * 10], "new": [i * 10, i * 10]} for i in range(1, 11)]
        self.assertEqual(self.mod._changes_cell("whole_file", {"hunks": hunks, "added": 10, "removed": 10}),
                         "whole_file, 10 hunks: 10, 20, 30, 40, 50, 60, 70, 80, ... (+10/-10)")

    def test_a_whole_file_candidate_without_changes_reads_as_before(self) -> None:
        """laps/21 row M1 "(absent key: as today)": the stored `work_4f36d04f` manifest has no `changes` key and its row
        reads `n/a (whole_file)`."""
        self.put(manifest(WHOLE_ID, "awaiting_review", artifact_kind="whole_file", target_path="fleet/experiment_linux.py"))
        self.assertEqual(self.row(WHOLE_ID)[5], "n/a (whole_file)")

    def test_a_failed_diff_prints_the_apply_failure(self) -> None:
        """rehearsal/morning-report-0738Z.md: `work_2e663a07` failed row reads "git apply --check failed: error: patch
        fragment without header at line 15: @@ -6" (the failure text cut to 80 characters)."""
        failure = "git apply --check failed: error: patch fragment without header at line 15: @@ -6,7 +6,7 @@ def x():"
        self.put(manifest(DIFF_ID, "failed", artifact_kind="unified_diff", failure=failure))
        cells = self.row(DIFF_ID)
        self.assertEqual((cells[0], cells[4]), ("**failed**", "unified_diff"))
        self.assertEqual(cells[5], failure[:80])
        self.assertEqual(cells[5], "git apply --check failed: error: patch fragment without header at line 15: @@ -6")

    def test_the_report_reads_nothing_outside_the_temp_tree(self) -> None:
        """Isolation guard for this file: the temp work is found, the live runs directory is not, and the reader was never
        asked for a finished work."""
        self.put(manifest(CARRY_ID, "accepted"))
        text = self.mod.build(1)
        self.assertIn(CARRY_ID, text)
        self.assertNotIn("work_96772bb2", text)
        self.assertEqual(self.reader_calls, [])
        self.assertTrue(str(self.mod.REPO).startswith(str(self.tmp)))


if __name__ == "__main__":
    unittest.main()
