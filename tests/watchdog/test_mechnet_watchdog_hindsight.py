"""Tests for the mechnet-watchdog regret-accrual leg (ADR-0008 wiring).

ADR-0008 designed the accrual as "the regret summary rides the patrol's own
ledger event": every 15-min watchdog pass replays the last HINDSIGHT_LIMIT
completed runs through the shadow scheduler and ledgers the regret summary.
These tests pin the two properties the H1 promotion gate depends on:

  * the summary is RECOVERABLE from the ledger — it rides the event's `args`
    (the ledger stores result digests only; args keeps a 400-char canonical-JSON
    preview), and the compact summary always fits that preview un-truncated;
  * the leg is best-effort — a hindsight failure is captured, ledgered as
    ok=False (a visible hole in the trend series), and never raises out of the
    patrol pass.

`hindsight_fn` is injectable throughout so no test touches SSH or OR-Tools.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from fleet.mechnet_watchdog import (
    HINDSIGHT_LIMIT,
    run_hindsight_accrual,
    run_pass,
)
from hearth.kernel.ledger import Ledger

REPO_ROOT = Path(__file__).resolve().parents[2]

_REPORT = {
    "n_runs": 20,
    "actual": {"span_s": 8811.0, "metered_tokens": 16000},
    "proposed": {"span_s": 10200.0, "metered_tokens": 0, "solver_status": "FEASIBLE"},
    "regret": {"tokens_saved": 16000, "span_delta_s": -1389.0},
    "per_run": [{"plan_id": f"run-{i}"} for i in range(20)],  # must NOT ride the ledger
}


def _ok_hindsight(limit: int) -> dict:
    return {"ok": True, "report": _REPORT, "table": "..."}


class RunHindsightAccrualTests(TestCase):
    def setUp(self) -> None:
        self.ledger_dir = Path(mkdtemp()).resolve()
        self.ledger = Ledger(ledger_dir=self.ledger_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.ledger_dir, ignore_errors=True)

    def test_replays_twenty_runs_per_adr_0008(self) -> None:
        seen: dict = {}

        def spy(limit: int) -> dict:
            seen["limit"] = limit
            return _ok_hindsight(limit)

        run_hindsight_accrual(write_ledger=False, hindsight_fn=spy)
        self.assertEqual(seen["limit"], HINDSIGHT_LIMIT)
        self.assertEqual(HINDSIGHT_LIMIT, 20)

    def test_regret_summary_is_recoverable_from_the_ledger_event(self) -> None:
        outcome = run_hindsight_accrual(write_ledger=True, hindsight_fn=_ok_hindsight,
                                        ledger=self.ledger)
        self.assertTrue(outcome["ok"])
        self.assertIsNotNone(outcome["ledger_event_id"])

        events = self.ledger.query(tool="mechnet_watchdog.hindsight")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertTrue(event["ok"])
        # The whole point of the accrual: the numbers live IN the event. The
        # args preview is canonical JSON capped at 400 chars — the summary must
        # parse back whole, not arrive truncated.
        summary = json.loads(event["args_preview"])
        self.assertEqual(summary["limit"], HINDSIGHT_LIMIT)
        self.assertEqual(summary["n_runs"], 20)
        self.assertEqual(summary["regret"]["tokens_saved"], 16000)
        self.assertEqual(summary["regret"]["span_delta_s"], -1389.0)
        self.assertEqual(summary["actual"]["metered_tokens"], 16000)
        self.assertEqual(summary["proposed"]["solver_status"], "FEASIBLE")
        # The per-run table stays out of the event (it would blow the preview).
        self.assertNotIn("per_run", summary)

    def test_hindsight_failure_is_ledgered_not_raised(self) -> None:
        def boom(limit: int) -> dict:
            raise RuntimeError("conductor unreachable")

        outcome = run_hindsight_accrual(write_ledger=True, hindsight_fn=boom,
                                        ledger=self.ledger)
        self.assertFalse(outcome["ok"])
        self.assertIn("conductor unreachable", outcome["error"])
        events = self.ledger.query(tool="mechnet_watchdog.hindsight")
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["ok"])
        self.assertIn("conductor unreachable", events[0]["error"])

    def test_hindsight_not_ok_result_is_ledgered_as_failure(self) -> None:
        def not_ok(limit: int) -> dict:
            return {"ok": False, "error": "non-JSON gather output"}

        outcome = run_hindsight_accrual(write_ledger=True, hindsight_fn=not_ok,
                                        ledger=self.ledger)
        self.assertFalse(outcome["ok"])
        events = self.ledger.query(tool="mechnet_watchdog.hindsight")
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["ok"])
        self.assertEqual(events[0]["error"], "non-JSON gather output")

    def test_no_ledger_flag_skips_the_write(self) -> None:
        outcome = run_hindsight_accrual(write_ledger=False, hindsight_fn=_ok_hindsight,
                                        ledger=self.ledger)
        self.assertTrue(outcome["ok"])
        self.assertNotIn("ledger_event_id", outcome)
        self.assertEqual(self.ledger.query(tool="mechnet_watchdog.hindsight"), [])


class RunPassWiringTests(TestCase):
    """The 15-min pass (what the ADR-0015 in-gateway watchdog timer runs)
    carries the accrual leg; --no-hindsight opts out."""

    def setUp(self) -> None:
        self.tmp = Path(mkdtemp()).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kwargs) -> dict:
        return run_pass(
            REPO_ROOT / "fleet" / "inventory.toml", timeout=0.1, dry_run=True,
            write_ledger=False,
            prober=lambda host, port, timeout: (True, 1.0, None),
            include_watchfire=False,
            include_patrol_trend=False,
            snapshot_path=self.tmp / "snapshots.json",
            **kwargs,
        )

    def test_pass_includes_the_hindsight_leg(self) -> None:
        report = self._run(hindsight_fn=_ok_hindsight)
        self.assertTrue(report["hindsight"]["ok"])
        self.assertEqual(report["hindsight"]["summary"]["n_runs"], 20)

    def test_hindsight_failure_never_flips_the_health_verdict(self) -> None:
        def boom(limit: int) -> dict:
            raise RuntimeError("ortools import failed")

        report = self._run(hindsight_fn=boom)
        self.assertFalse(report["hindsight"]["ok"])
        self.assertTrue(report["healthy"])  # liveness alone owns the verdict

    def test_no_hindsight_omits_the_leg(self) -> None:
        report = self._run(include_hindsight=False, hindsight_fn=_ok_hindsight)
        self.assertNotIn("hindsight", report)
