from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase

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


def _fake_remediator(apply):
    """Stand-in for hearth.toolsurface.remediate.remediate — no SSH."""
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
            remediator=_fake_remediator)
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
            remediator=_fake_remediator)
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
        out = wd.run_watchfire(dry_run=False, write_ledger=False, remediator=rem)
        self.assertTrue(seen["apply"])
        self.assertTrue(out["ok"])

    def test_dry_run_does_not_apply(self) -> None:
        seen = {}
        def rem(apply):
            seen["apply"] = apply
            return {"ok": True, "dry_run": not apply, "healable": [], "flagged": [], "healed": []}
        wd.run_watchfire(dry_run=True, write_ledger=False, remediator=rem)
        self.assertFalse(seen["apply"])

    def test_remediator_error_never_crashes_patrol(self) -> None:
        def boom(apply):
            raise RuntimeError("conductor down")
        out = wd.run_watchfire(dry_run=False, write_ledger=False, remediator=boom)
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
                              remediator=_fake_remediator)
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
