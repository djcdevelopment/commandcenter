"""Condition 5 (WI-G2b): a damaged tail is a nonzero, named condition.

The WI-G2a candidate reported an interrupted final write correctly everywhere
except where a script reads the answer: `operator history --verify` printed a
WARNING and exited 0. A cold agent that checks the exit code — which is what the
CLI contract tells it to do — saw "verified" on a history that cannot be
appended to until it is reconciled.

Derek's Gate 2 decision keeps the recovery rule (replay projects through the
last complete event when the only damage is one interrupted final record) and
requires: a nonzero exit for a truncated tail; the condition named as degraded
or recovery-required; append still blocked; malformed COMPLETE records still
hard failures; and documented exit codes that distinguish clean, recoverable
tail, and corrupt.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from hearth.operator import (catalog as catalog_mod, cli as cli_mod, core, history, paths)
from hearth.tests.operator.support import (FakeDoor, OperatorTestCase, ORCHESTRATOR_KEY,
                                           fake_cli_runner)

RUN_ID = "run-truncated-tail"

CLEAN = 0
CORRUPT = 1
RECOVERY_REQUIRED = 4


class TruncatedTailTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(), catalog=self.catalog)
        self.envelope = self.make_envelope()
        from hearth.operator import envelope as envelope_mod

        envelope_mod.store_envelope(self.envelope, RUN_ID)
        history.append("route.proposed", {"proposal_id": "a" * 64}, run_id=RUN_ID)

    # --- harness ----------------------------------------------------------
    def cli(self, *argv: str, key: str | None = None) -> subprocess.CompletedProcess:
        env = self.child_env()
        if key is not None:
            env[paths.API_KEY_ENV] = key
        return subprocess.run([sys.executable, "-m", "hearth.operator", *argv],
                              input="", capture_output=True, text=True, encoding="utf-8",
                              env=env, cwd=str(paths.REPO_ROOT), timeout=180)

    def truncate_tail(self) -> int:
        """Interrupt the final write: a partial last line with no newline."""
        target = paths.run_history_path(RUN_ID)
        text = target.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.strip()]
        partial = rows[-1][:len(rows[-1]) // 2]
        target.write_text("".join(row + "\n" for row in rows) + partial,
                          encoding="utf-8", newline="")
        return len(rows)

    def corrupt_a_complete_record(self) -> None:
        target = paths.run_history_path(RUN_ID)
        rows = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        row = json.loads(rows[0])
        row["payload"]["envelope_id"] = "9" * 64
        rows[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        target.write_text("".join(line + "\n" for line in rows), encoding="utf-8", newline="")

    # --- the three conditions ---------------------------------------------
    def test_history_verify_exits_zero_on_clean_history(self) -> None:
        result = self.cli("history", RUN_ID, "--verify")
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn("verified", result.stdout)

    def test_history_verify_exits_nonzero_and_names_a_truncated_tail(self) -> None:
        kept = self.truncate_tail()
        result = self.cli("history", RUN_ID, "--verify")
        self.assertEqual(
            result.returncode, RECOVERY_REQUIRED,
            "a history that cannot be appended to until it is reconciled reported "
            f"success (exit {result.returncode})")
        output = (result.stdout + result.stderr).lower()
        self.assertTrue("recovery-required" in output or "degraded" in output,
                        f"the condition is not named: {result.stdout}{result.stderr}")
        self.assertIn(str(kept), result.stdout + result.stderr)
        self.assertIn("--reconcile-tail", result.stdout + result.stderr)

    def test_history_verify_exits_one_on_a_malformed_complete_record(self) -> None:
        self.corrupt_a_complete_record()
        result = self.cli("history", RUN_ID, "--verify")
        self.assertEqual(result.returncode, CORRUPT, result.stdout + result.stderr)
        self.assertIn("event_id", result.stderr)

    def test_the_three_conditions_have_distinct_exit_codes(self) -> None:
        clean = self.cli("history", RUN_ID, "--verify").returncode
        self.truncate_tail()
        tail = self.cli("history", RUN_ID, "--verify").returncode
        self.corrupt_a_complete_record()
        corrupt = self.cli("history", RUN_ID, "--verify").returncode
        self.assertEqual([clean, tail, corrupt], [CLEAN, RECOVERY_REQUIRED, CORRUPT])
        self.assertEqual(len({clean, tail, corrupt}), 3)

    def test_the_exit_codes_are_documented_in_the_cli_contract(self) -> None:
        doc = cli_mod.__doc__ or ""
        self.assertIn(str(RECOVERY_REQUIRED), doc,
                      "the recoverable-tail exit code is not in the CLI's documented "
                      "exit-code table, so a cold agent cannot script against it")
        lowered = doc.lower()
        self.assertTrue("recovery-required" in lowered or "degraded" in lowered)
        self.assertIn("--reconcile-tail", doc)

    # --- the recovery rule is preserved ------------------------------------
    def test_replay_still_projects_through_the_last_complete_event(self) -> None:
        kept = self.truncate_tail()
        result = self.cli("replay", RUN_ID, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["events_replayed"], kept)
        self.assertTrue(state["truncated_tail"])

    def test_append_stays_blocked_until_the_tail_is_reconciled(self) -> None:
        self.truncate_tail()
        with self.assertRaises(history.HistoryError) as ctx:
            history.append("route.proposed", {"proposal_id": "b" * 64}, run_id=RUN_ID)
        self.assertIn("reconcile", str(ctx.exception).lower())

    def test_reconcile_restores_appendability_without_rewriting_accepted_history(self) -> None:
        target = paths.run_history_path(RUN_ID)
        accepted = "".join(line + "\n" for line in
                           target.read_text(encoding="utf-8").splitlines() if line.strip())
        self.truncate_tail()
        reconciled = self.cli("history", RUN_ID, "--reconcile-tail")
        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), accepted,
                         "reconciliation rewrote accepted history")
        self.assertEqual(self.cli("history", RUN_ID, "--verify").returncode, CLEAN)
        appended = history.append("route.proposed", {"proposal_id": "c" * 64}, run_id=RUN_ID)
        self.assertEqual(appended["sequence"],
                         len([line for line in accepted.splitlines() if line.strip()]) + 1)

    def test_reconcile_on_a_healthy_file_is_refused(self) -> None:
        result = self.cli("history", RUN_ID, "--reconcile-tail")
        self.assertEqual(result.returncode, CORRUPT)
        self.assertIn("nothing to reconcile", result.stderr)

    def test_a_damaged_row_inside_accepted_history_is_never_reconciled_away(self) -> None:
        self.corrupt_a_complete_record()
        result = self.cli("history", RUN_ID, "--reconcile-tail")
        self.assertEqual(result.returncode, CORRUPT)
        self.assertIn("event_id", result.stderr)

    def test_the_plain_listing_reports_the_same_condition(self) -> None:
        self.truncate_tail()
        result = self.cli("history", RUN_ID)
        self.assertEqual(result.returncode, RECOVERY_REQUIRED,
                         result.stdout + result.stderr)
        self.assertIn("--reconcile-tail", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
