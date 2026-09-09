r"""Prove restart_incumbent's refusal paths without touching production.

Two behaviours matter and neither can be checked by reading the code:
  1. it refuses BEFORE running anything when the shared sentinel exists;
  2. it aborts BEFORE booting when the sentinel appears during the stop.
Both are simulated by monkeypatching os.path.exists and subprocess.run, so no scheduled
task is ever invoked and the live server is untouched.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_cell  # noqa: E402


class RestartIncumbentTests(unittest.TestCase):
    def test_refuses_outright_while_the_shared_sentinel_is_held(self):
        with mock.patch.object(ff_cell.os.path, "exists", return_value=True), \
             mock.patch.object(ff_cell.subprocess, "run") as run:
            rec = ff_cell.restart_incumbent()
        self.assertFalse(rec["ok"])
        run.assert_not_called()                      # nothing was fired at all
        self.assertEqual(rec["steps"][0]["step"], "sentinel")
        self.assertIn("shared maintenance lock", rec["steps"][0]["detail"])
        self.assertFalse(rec["production_may_be_down"])

    def test_aborts_before_booting_if_the_sentinel_appears_during_the_stop(self):
        seen = {"n": 0}

        def exists(_path):
            seen["n"] += 1
            return seen["n"] > 1                     # clear first, held afterwards

        with mock.patch.object(ff_cell.os.path, "exists", side_effect=exists), \
             mock.patch.object(ff_cell, "port_listening", return_value=False), \
             mock.patch.object(ff_cell.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rec = ff_cell.restart_incumbent()

        fired = [c.args[0][3] for c in run.call_args_list]
        self.assertEqual(fired, [ff_cell.ARCSERVE_STOP_TASK])   # stopped, never booted
        self.assertNotIn(ff_cell.ARCSERVE_BOOT_TASK, fired)
        self.assertTrue(rec["production_may_be_down"])
        self.assertEqual(rec["recovery_command"], "schtasks /Run /TN ArcServeBoot")
        self.assertEqual(rec["steps"][-1]["step"], "sentinel_after_stop")

    def test_the_happy_path_stops_then_boots_in_that_order(self):
        with mock.patch.object(ff_cell.os.path, "exists", return_value=False), \
             mock.patch.object(ff_cell, "port_listening", return_value=False), \
             mock.patch.object(ff_cell, "wait_for_ready", return_value=True), \
             mock.patch.object(ff_cell.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rec = ff_cell.restart_incumbent()

        fired = [c.args[0][3] for c in run.call_args_list]
        self.assertEqual(fired, [ff_cell.ARCSERVE_STOP_TASK, ff_cell.ARCSERVE_BOOT_TASK])
        self.assertTrue(rec["ok"])
        self.assertFalse(rec["production_may_be_down"])

    def test_a_ready_marker_timeout_says_production_may_be_down(self):
        with mock.patch.object(ff_cell.os.path, "exists", return_value=False), \
             mock.patch.object(ff_cell, "port_listening", return_value=False), \
             mock.patch.object(ff_cell, "wait_for_ready", return_value=False), \
             mock.patch.object(ff_cell.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rec = ff_cell.restart_incumbent()
        self.assertFalse(rec["ok"])
        self.assertTrue(rec["production_may_be_down"])
        self.assertIn("ArcServeBoot", rec["recovery_command"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
