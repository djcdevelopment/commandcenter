from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fleet import mechnet_watchdog as wd

# A tiny inventory: omen has an up service (11434) and a down one (8710) with a
# revive; cc-conductor's ssh is down with NO revive (alert-only); i5 is optional.
_INV = textwrap.dedent("""
    [meta]
    updated = "test"

    [[node]]
    name = "omen"
    expect = "up"
    address = "127.0.0.1"
    checks = [
      { service = "ollama", port = 11434 },
      { service = "hearth-gateway", port = 8710, revive = "echo revived" },
    ]

    [[node]]
    name = "cc-conductor"
    expect = "up"
    address = "10.0.0.9"
    checks = [ { service = "ssh", port = 22 } ]

    [[node]]
    name = "i5-laptop"
    expect = "optional"
    address = "10.0.0.5"
    checks = [ { service = "ssh", port = 22 } ]
""")

# Ports considered "up" by the fake prober. Everything else is down.
_UP_PORTS = {11434}


def _fake_prober(up_ports):
    def prober(host, port, timeout):
        return (port in up_ports, 1.0 if port in up_ports else None, None)
    return prober


def _fake_masters_pet(apply):
    """Stand-in for hearth.toolsurface.masters_pet.masters_pet — no SSH."""
    return {"ok": True, "dry_run": not apply,
            "healable": [{"kind": "phantom_in_flight", "plan_id": "x"}],
            "flagged": [{"kind": "false_success", "plan_id": "y"}],
            "healed": ([{"plan_id": "x", "action": "stubbed"}] if apply else [])}


def _write_inv(tmp: Path) -> Path:
    path = tmp / "inventory.toml"
    path.write_text(_INV, encoding="utf-8")
    return path


class CollectAndPlanTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inv = wd.load_inventory(_write_inv(self.tmp))

    def test_collect_checks_probes_every_check(self) -> None:
        rows = wd.collect_checks(self.inv["nodes"], 1.0, prober=_fake_prober(_UP_PORTS))
        self.assertEqual(len(rows), 4)  # 2 omen + 1 conductor + 1 i5
        gw = next(r for r in rows if r["service"] == "hearth-gateway")
        self.assertFalse(gw["reachable"])
        self.assertEqual(gw["revive"], "echo revived")

    def test_plan_splits_revivable_and_alert_only(self) -> None:
        rows = wd.collect_checks(self.inv["nodes"], 1.0, prober=_fake_prober(_UP_PORTS))
        revivable, alert_only = wd.plan_revivals(rows)
        self.assertEqual([r["service"] for r in revivable], ["hearth-gateway"])
        self.assertEqual([r["node"] for r in alert_only], ["cc-conductor"])

    def test_optional_down_node_never_planned(self) -> None:
        rows = wd.collect_checks(self.inv["nodes"], 1.0, prober=_fake_prober(_UP_PORTS))
        revivable, alert_only = wd.plan_revivals(rows)
        names = {r["node"] for r in revivable + alert_only}
        self.assertNotIn("i5-laptop", names)


class ReviveOneTests(TestCase):
    def _row(self) -> dict:
        return {"node": "omen", "service": "hearth-gateway", "host": "127.0.0.1",
                "port": 8710, "revive": "echo x"}

    def test_recovers_when_reprobe_up(self) -> None:
        calls = []
        outcome = wd.revive_one(
            self._row(), 1.0,
            runner=lambda cmd: calls.append(cmd) or {"exit_code": 0, "timed_out": False},
            prober=_fake_prober({8710}))  # up on re-probe
        self.assertEqual(calls, ["echo x"])
        self.assertTrue(outcome["recovered"])
        self.assertEqual(outcome["revive_result"]["exit_code"], 0)

    def test_still_down_when_reprobe_fails(self) -> None:
        outcome = wd.revive_one(
            self._row(), 1.0,
            runner=lambda cmd: {"exit_code": 1, "timed_out": False},
            prober=_fake_prober(set()))  # still down
        self.assertFalse(outcome["recovered"])


class RunPassTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inv_path = _write_inv(self.tmp)

    def test_dry_run_runs_nothing(self) -> None:
        called = []
        report = wd.run_pass(
            self.inv_path, 1.0, dry_run=True, write_ledger=False,
            prober=_fake_prober(_UP_PORTS),
            runner=lambda cmd: called.append(cmd) or {"exit_code": 0, "timed_out": False},
            masters_pet_fn=_fake_masters_pet)
        self.assertEqual(called, [])  # nothing executed
        self.assertEqual(report["revivable"], 1)
        self.assertTrue(report["revivals"][0]["dry_run"])
        self.assertFalse(report["healthy"])  # conductor still down (alert-only)

    def test_live_pass_revives_and_reports(self) -> None:
        # Stateful: 8710 is down on the initial sweep, up after the revive runs.
        probed_8710 = {"count": 0}

        def prober(host, port, timeout):
            if port == 8710:
                probed_8710["count"] += 1
                up = probed_8710["count"] > 1  # first probe down, re-probe up
                return (up, 1.0 if up else None, None)
            return (port in _UP_PORTS, 1.0 if port in _UP_PORTS else None, None)

        report = wd.run_pass(
            self.inv_path, 1.0, dry_run=False, write_ledger=False,
            prober=prober,
            runner=lambda cmd: {"exit_code": 0, "timed_out": False},
            masters_pet_fn=_fake_masters_pet)
        gw = next(o for o in report["revivals"] if o["service"] == "hearth-gateway")
        self.assertTrue(gw["recovered"])
        # conductor has no revive -> still alert-only -> not healthy
        self.assertEqual([a["node"] for a in report["alert_only"]], ["cc-conductor"])
        self.assertFalse(report["healthy"])


class WatchfireTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_applies_when_not_dry_run(self) -> None:
        seen = {}
        def rem(apply):
            seen["apply"] = apply
            return {"ok": True, "dry_run": not apply, "healable": [], "flagged": [], "healed": []}
        out = wd.run_watchfire(dry_run=False, write_ledger=False, masters_pet_fn=rem)
        self.assertTrue(seen["apply"])
        self.assertTrue(out["ok"])

    def test_dry_run_does_not_apply(self) -> None:
        seen = {}
        def rem(apply):
            seen["apply"] = apply
            return {"ok": True, "dry_run": not apply, "healable": [], "flagged": [], "healed": []}
        wd.run_watchfire(dry_run=True, write_ledger=False, masters_pet_fn=rem)
        self.assertFalse(seen["apply"])

    def test_masters_pet_error_never_crashes_patrol(self) -> None:
        def boom(apply):
            raise RuntimeError("conductor down")
        out = wd.run_watchfire(dry_run=False, write_ledger=False, masters_pet_fn=boom)
        self.assertFalse(out["ok"])
        self.assertIn("conductor down", out["error"])

    def test_watchfire_ledger_event_recorded(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        result = {"ok": True, "dry_run": False, "healable": [1], "flagged": [1, 2], "healed": [1]}
        eid = wd._record_watchfire(result, ledger=led)
        self.assertIsNotNone(eid)
        events = led.query(tool="mechnet_watchdog.watchfire")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["caller"]["id"], "mechnet-watchdog")
        self.assertIsNotNone(events[0]["result_digest"])  # summary captured as a digest

    def test_run_pass_includes_watchfire_and_can_disable(self) -> None:
        inv = _write_inv(self.tmp)
        runner = lambda cmd: {"exit_code": 0, "timed_out": False}
        with_wf = wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                              prober=_fake_prober(_UP_PORTS), runner=runner,
                              masters_pet_fn=_fake_masters_pet)
        self.assertIn("watchfire", with_wf)
        self.assertTrue(with_wf["watchfire"]["ok"])
        without = wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                              prober=_fake_prober(_UP_PORTS), runner=runner,
                              include_watchfire=False)
        self.assertNotIn("watchfire", without)


class LedgerRecordTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_record_appends_hearth_event(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        outcome = {"node": "omen", "service": "hearth-gateway",
                   "revive": "doorcheck --revive", "recovered": True,
                   "revive_result": {"exit_code": 0, "timed_out": False}}
        event_id = wd._record(outcome, ledger=led)
        self.assertIsNotNone(event_id)
        events = led.query(tool="mechnet_watchdog.revive")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["caller"]["id"], "mechnet-watchdog")


class SnapshotStateTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "snapshots.json"

    def test_missing_file_returns_empty_doc(self) -> None:
        doc = wd.load_snapshots(self.path)
        self.assertEqual(doc["entries"], [])

    def test_corrupt_file_returns_empty_doc(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        doc = wd.load_snapshots(self.path)
        self.assertEqual(doc["entries"], [])

    def test_save_then_load_round_trips(self) -> None:
        wd.save_snapshots({"entries": [{"ts": "x"}]}, self.path)
        doc = wd.load_snapshots(self.path)
        self.assertEqual(doc["entries"], [{"ts": "x"}])

    def test_append_snapshot_caps_at_limit(self) -> None:
        for i in range(5):
            wd.append_snapshot({"ts": f"t{i}"}, path=self.path, cap=3)
        entries = wd.load_snapshots(self.path)["entries"]
        self.assertEqual([e["ts"] for e in entries], ["t2", "t3", "t4"])

    def test_append_snapshot_preserves_order(self) -> None:
        wd.append_snapshot({"ts": "first"}, path=self.path, cap=10)
        wd.append_snapshot({"ts": "second"}, path=self.path, cap=10)
        entries = wd.load_snapshots(self.path)["entries"]
        self.assertEqual([e["ts"] for e in entries], ["first", "second"])


class TakeSnapshotTests(TestCase):
    def test_take_snapshot_shapes_patrol_result(self) -> None:
        def fake_patrol(refresh):
            return {"ok": True, "scanned": 10, "considered": 3,
                    "gaps": [{"kind": "phantom_in_flight", "severity": "warn",
                             "plan_id": "p1", "detail": "d"}]}
        entry = wd.take_snapshot(patrol_fn=fake_patrol)
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["gap_keys"], [["phantom_in_flight", "p1"]])
        self.assertEqual(entry["gaps"][0]["detail"], "d")
        self.assertIn("ts", entry)

    def test_take_snapshot_calls_patrol_with_refresh_false(self) -> None:
        seen = {}
        def fake_patrol(refresh):
            seen["refresh"] = refresh
            return {"ok": True, "gaps": []}
        wd.take_snapshot(patrol_fn=fake_patrol)
        self.assertFalse(seen["refresh"])

    def test_patrol_exception_never_crashes_take_snapshot(self) -> None:
        def boom(refresh):
            raise RuntimeError("ssh down")
        entry = wd.take_snapshot(patrol_fn=boom)
        self.assertFalse(entry["ok"])
        self.assertIn("ssh down", entry["error"])
        self.assertEqual(entry["gaps"], [])

    def test_patrol_ok_false_result_captured(self) -> None:
        def fake_patrol(refresh):
            return {"ok": False, "error": "TimeoutExpired"}
        entry = wd.take_snapshot(patrol_fn=fake_patrol)
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["gaps"], [])
        self.assertEqual(entry["gap_keys"], [])


class ComputeTrendTests(TestCase):
    def test_insufficient_history_below_window(self) -> None:
        trend = wd.compute_trend([{"gap_keys": []}], window=3)
        self.assertTrue(trend["insufficient_history"])
        self.assertEqual(trend["sample_count"], 1)
        self.assertEqual(trend["persistent"], [])
        self.assertEqual(trend["new"], [])
        self.assertEqual(trend["resolved"], [])

    def test_insufficient_history_exactly_two(self) -> None:
        trend = wd.compute_trend([{"gap_keys": []}, {"gap_keys": []}], window=3)
        self.assertTrue(trend["insufficient_history"])
        self.assertEqual(trend["sample_count"], 2)

    def test_persistent_gap_present_in_all_three(self) -> None:
        entries = [{"gap_keys": [["stale_checkout", "p1"]]}] * 3
        trend = wd.compute_trend(entries, window=3)
        self.assertFalse(trend["insufficient_history"])
        self.assertEqual(trend["persistent"], [{"kind": "stale_checkout", "plan_id": "p1"}])
        self.assertEqual(trend["new"], [])
        self.assertEqual(trend["resolved"], [])

    def test_new_gap_only_in_latest(self) -> None:
        entries = [{"gap_keys": []}, {"gap_keys": []},
                  {"gap_keys": [["crashed_isolated", "p2"]]}]
        trend = wd.compute_trend(entries, window=3)
        self.assertEqual(trend["new"], [{"kind": "crashed_isolated", "plan_id": "p2"}])
        self.assertEqual(trend["persistent"], [])
        self.assertEqual(trend["resolved"], [])

    def test_resolved_gap_absent_from_latest(self) -> None:
        entries = [{"gap_keys": [["false_success", "p3"]]},
                  {"gap_keys": [["false_success", "p3"]]},
                  {"gap_keys": []}]
        trend = wd.compute_trend(entries, window=3)
        self.assertEqual(trend["resolved"], [{"kind": "false_success", "plan_id": "p3"}])

    def test_mixed_persistent_new_resolved_together(self) -> None:
        entries = [
            {"gap_keys": [["a", "1"], ["b", "2"]]},
            {"gap_keys": [["a", "1"], ["b", "2"]]},
            {"gap_keys": [["a", "1"], ["c", "3"]]},
        ]
        trend = wd.compute_trend(entries, window=3)
        self.assertEqual(trend["persistent"], [{"kind": "a", "plan_id": "1"}])
        self.assertEqual(trend["new"], [{"kind": "c", "plan_id": "3"}])
        self.assertEqual(trend["resolved"], [{"kind": "b", "plan_id": "2"}])

    def test_window_only_considers_last_n_entries(self) -> None:
        entries = [
            {"gap_keys": [["stale_only_in_old_history", "px"]]},
            {"gap_keys": []},
            {"gap_keys": []},
            {"gap_keys": []},
        ]
        trend = wd.compute_trend(entries, window=3)
        self.assertEqual(trend["resolved"], [])  # the stale key is outside the window

    def test_gap_keys_missing_or_malformed_entry_treated_as_empty(self) -> None:
        entries = [{}, {"gap_keys": []}, {"gap_keys": [["x", "1"]]}]
        trend = wd.compute_trend(entries, window=3)
        self.assertEqual(trend["new"], [{"kind": "x", "plan_id": "1"}])


class RunPatrolSnapshotTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snap_path = self.tmp / "snap.json"

    def _clean_patrol(self, refresh):
        return {"ok": True, "scanned": 1, "considered": 1, "gaps": []}

    def test_run_patrol_snapshot_appends_and_ledgers(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        result = wd.run_patrol_snapshot(write_ledger=True, snapshot_path=self.snap_path,
                                        patrol_fn=self._clean_patrol, ledger=led)
        self.assertEqual(result["snapshot_count"], 1)
        self.assertIsNotNone(result["ledger_event_id"])
        events = led.query(tool="mechnet_watchdog.patrol_snapshot")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["caller"]["id"], "mechnet-watchdog")

    def test_run_patrol_snapshot_write_ledger_false_skips_ledger(self) -> None:
        result = wd.run_patrol_snapshot(write_ledger=False, snapshot_path=self.snap_path,
                                        patrol_fn=self._clean_patrol)
        self.assertIsNone(result["ledger_event_id"])

    def test_run_patrol_snapshot_ledger_failure_returns_none_not_raise(self) -> None:
        class BoomLedger:
            def append(self, event):
                raise RuntimeError("ledger locked")
        result = wd.run_patrol_snapshot(write_ledger=True, snapshot_path=self.snap_path,
                                        patrol_fn=self._clean_patrol, ledger=BoomLedger())
        self.assertIsNone(result["ledger_event_id"])
        self.assertEqual(result["snapshot_count"], 1)  # the snapshot itself still landed


class RunPatrolTrendTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snap_path = self.tmp / "snap.json"

    def test_run_patrol_trend_reads_snapshot_file_and_ledgers(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        wd.save_snapshots({"entries": [{"gap_keys": [["x", "1"]]}] * 3}, self.snap_path)
        trend = wd.run_patrol_trend(write_ledger=True, snapshot_path=self.snap_path, ledger=led)
        self.assertFalse(trend["insufficient_history"])
        self.assertEqual(trend["persistent"], [{"kind": "x", "plan_id": "1"}])
        events = led.query(tool="mechnet_watchdog.patrol_trend")
        self.assertEqual(len(events), 1)

    def test_run_patrol_trend_missing_snapshot_file_is_insufficient_history_not_error(self) -> None:
        trend = wd.run_patrol_trend(write_ledger=False, snapshot_path=self.snap_path)
        self.assertTrue(trend["ok"])
        self.assertTrue(trend["insufficient_history"])
        self.assertEqual(trend["sample_count"], 0)

    def test_run_patrol_trend_corrupt_snapshot_file_never_crashes(self) -> None:
        self.snap_path.write_text("{not json", encoding="utf-8")
        trend = wd.run_patrol_trend(write_ledger=False, snapshot_path=self.snap_path)
        self.assertTrue(trend["ok"])  # load_snapshots' own fallback absorbs this
        self.assertTrue(trend["insufficient_history"])

    def test_run_pass_includes_patrol_trend_and_can_disable(self) -> None:
        inv = _write_inv(self.tmp)
        runner = lambda cmd: {"exit_code": 0, "timed_out": False}
        with_trend = wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                                 prober=_fake_prober(_UP_PORTS), runner=runner,
                                 masters_pet_fn=_fake_masters_pet, snapshot_path=self.snap_path)
        self.assertIn("patrol_trend", with_trend)
        without = wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                              prober=_fake_prober(_UP_PORTS), runner=runner,
                              masters_pet_fn=_fake_masters_pet, include_patrol_trend=False,
                              snapshot_path=self.snap_path)
        self.assertNotIn("patrol_trend", without)

    def test_patrol_trend_never_flips_healthy_verdict(self) -> None:
        inv = _write_inv(self.tmp)
        runner = lambda cmd: {"exit_code": 0, "timed_out": False}
        with patch.object(wd, "compute_trend", side_effect=RuntimeError("boom")):
            report = wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                                 prober=_fake_prober(_UP_PORTS), runner=runner,
                                 masters_pet_fn=_fake_masters_pet, snapshot_path=self.snap_path)
        self.assertFalse(report["patrol_trend"]["ok"])
        self.assertIn("boom", report["patrol_trend"]["error"])
        # conductor is alert-only in the shared _INV fixture -> not healthy,
        # regardless of the trend check blowing up -- liveness is the only gate.
        self.assertFalse(report["healthy"])


class CLIPatrolOnlyTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snap_path = self.tmp / "snap.json"

    def test_patrol_only_short_circuits_before_inventory_load(self) -> None:
        with patch.object(wd, "load_inventory", side_effect=AssertionError("should not be called")), \
             patch("hearth.toolsurface.patrol.patrol", return_value={"ok": True, "gaps": []}):
            code = wd.main(["--patrol-only", "--no-ledger",
                           "--snapshot-path", str(self.snap_path)])
        self.assertEqual(code, 0)

    def test_patrol_only_json_output_shape(self) -> None:
        with patch("hearth.toolsurface.patrol.patrol",
                  return_value={"ok": True, "scanned": 1, "considered": 1, "gaps": []}):
            with patch("builtins.print") as mock_print:
                wd.main(["--patrol-only", "--json", "--no-ledger",
                        "--snapshot-path", str(self.snap_path)])
        printed = json.loads(mock_print.call_args[0][0])
        self.assertIn("entry", printed)
        self.assertIn("snapshot_count", printed)
        self.assertIn("ledger_event_id", printed)

    def test_patrol_only_exit_code_reflects_patrol_ok_not_gap_count(self) -> None:
        gap = {"kind": "x", "severity": "warn", "plan_id": "p", "detail": "d"}
        with patch("hearth.toolsurface.patrol.patrol",
                  return_value={"ok": True, "scanned": 1, "considered": 1, "gaps": [gap]}):
            code = wd.main(["--patrol-only", "--no-ledger",
                           "--snapshot-path", str(self.snap_path)])
        self.assertEqual(code, 0)

    def test_patrol_only_exit_code_1_when_patrol_fails(self) -> None:
        with patch("hearth.toolsurface.patrol.patrol",
                  return_value={"ok": False, "error": "boom"}):
            code = wd.main(["--patrol-only", "--no-ledger",
                           "--snapshot-path", str(self.snap_path)])
        self.assertEqual(code, 1)

    def test_patrol_only_no_ledger_flag_skips_ledger_write(self) -> None:
        with patch("hearth.toolsurface.patrol.patrol", return_value={"ok": True, "gaps": []}):
            with patch.object(wd, "_record_patrol_snapshot") as mock_record:
                wd.main(["--patrol-only", "--no-ledger",
                        "--snapshot-path", str(self.snap_path)])
        mock_record.assert_not_called()
