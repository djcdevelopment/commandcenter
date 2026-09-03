"""mechnet_watchdog rung-state wiring (P7b, ADR-0044): report["rung_state"],
ledgered as `mechnet_watchdog.rung_state` with outcome=verdict, never flipping
`healthy`; `--no-rung-state` drops it; a raising reader is {ok: False} and the
pass still returns.
"""
from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fleet import mechnet_watchdog as wd

# One node, one check, and the fake prober says it's up -> liveness healthy=True.
_INV_ALL_UP = textwrap.dedent("""
    [meta]
    updated = "test"

    [[node]]
    name = "omen"
    expect = "up"
    address = "127.0.0.1"
    checks = [ { service = "hearth-gateway", port = 8710 } ]
""")

# Same node plus an alert-only conductor that is down -> liveness healthy=False.
_INV_CONDUCTOR_DOWN = _INV_ALL_UP + textwrap.dedent("""
    [[node]]
    name = "cc-conductor"
    expect = "up"
    address = "10.0.0.9"
    checks = [ { service = "ssh", port = 22 } ]
""")

_UP_PORTS = {8710}


def _fake_prober(up_ports):
    def prober(host, port, timeout):
        return (port in up_ports, 1.0 if port in up_ports else None, None)
    return prober


def _fake_masters_pet(apply):
    return {"ok": True, "dry_run": not apply, "healable": [], "flagged": [], "healed": []}


def _fake_hindsight(limit):
    return {"ok": True, "report": {"n_runs": 0, "regret": {}}}


def _rung(verdict, **over):
    st = {"rung": "omen-arc", "port": 8082, "verdict": verdict,
          "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
          "envelope": {"fail_below": 0.8, "warn_below": 0.9},
          "observed_tok_s": 107.5, "observed_at": "2026-09-03T02:28:20-07:00",
          "observed_age_s": 100.0, "frac_of_baseline": 1.0142,
          "prefill_stall_recent": False, "last_ping_ok": True, "deep_samples": 3,
          "excluded_windows": [], "note": "envelope is of THIS baseline epoch, not of capacity"}
    st.update(over)
    return st


_DEGRADED = _rung("degraded", observed_tok_s=65.0, frac_of_baseline=0.6132)
_UNREACHABLE = _rung("unreachable", last_ping_ok=False, observed_age_s=8900.0)


def _write(tmp: Path, text: str) -> Path:
    path = tmp / "inventory.toml"
    path.write_text(text, encoding="utf-8")
    return path


class RunRungStateTests(TestCase):
    def test_verdict_and_summary_shape(self) -> None:
        out = wd.run_rung_state(write_ledger=False, rung_state_fn=lambda: _DEGRADED)
        self.assertTrue(out["ok"])
        self.assertEqual(out["verdict"], "degraded")
        self.assertEqual(out["observed_tok_s"], 65.0)
        self.assertEqual(out["summary"]["verdict"], "degraded")
        self.assertEqual(out["summary"]["frac_of_baseline"], 0.6132)
        self.assertEqual(out["summary"]["baseline_tok_s"], 106.0)
        self.assertNotIn("ledger_event_id", out)
        # The summary must always fit the ledger's 400-char args preview.
        self.assertLessEqual(len(json.dumps(out["summary"], sort_keys=True)), 400)

    def test_unreachable_is_a_successful_read_with_a_bad_outcome(self) -> None:
        # The live state on 2026-09-03: production down under another lane.
        out = wd.run_rung_state(write_ledger=False, rung_state_fn=lambda: _UNREACHABLE)
        self.assertTrue(out["ok"])
        self.assertEqual(out["verdict"], "unreachable")
        self.assertFalse(out["last_ping_ok"])

    def test_reader_raising_is_ok_false(self) -> None:
        def boom():
            raise RuntimeError("keep-alive tail unreadable")
        out = wd.run_rung_state(write_ledger=False, rung_state_fn=boom)
        self.assertFalse(out["ok"])
        self.assertIn("keep-alive tail unreadable", out["error"])
        self.assertEqual(out["verdict"], "unknown")

    def test_reader_error_shape_is_ok_false(self) -> None:
        out = wd.run_rung_state(write_ledger=False,
                                rung_state_fn=lambda: {"rung": "omen-arc", "verdict": "unknown",
                                                       "error": "ValueError: bad json"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "ValueError: bad json")

    def test_reader_returning_non_dict_is_ok_false(self) -> None:
        out = wd.run_rung_state(write_ledger=False, rung_state_fn=lambda: "degraded")
        self.assertFalse(out["ok"])
        self.assertIn("TypeError", out["error"])


class RunPassRungStateTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snap = self.tmp / "snap.json"

    def _pass(self, inv_text, rung_state_fn, **kw):
        inv = _write(self.tmp, inv_text)
        return wd.run_pass(inv, 1.0, dry_run=True, write_ledger=False,
                           prober=_fake_prober(_UP_PORTS),
                           runner=lambda cmd: {"exit_code": 0, "timed_out": False},
                           masters_pet_fn=_fake_masters_pet, hindsight_fn=_fake_hindsight,
                           snapshot_path=self.snap, rung_state_fn=rung_state_fn, **kw)

    def test_run_pass_includes_rung_state_and_can_disable(self) -> None:
        with_rs = self._pass(_INV_ALL_UP, lambda: _DEGRADED)
        self.assertIn("rung_state", with_rs)
        self.assertEqual(with_rs["rung_state"]["verdict"], "degraded")
        without = self._pass(_INV_ALL_UP, lambda: _DEGRADED, include_rung_state=False)
        self.assertNotIn("rung_state", without)

    def test_degraded_rung_never_flips_healthy_down(self) -> None:
        # Liveness says every declared service answers -> healthy, full stop.
        # A rung decoding at 61% of its epoch is DEGRADED here and UP there;
        # the gap between the two is the finding, not a verdict override.
        report = self._pass(_INV_ALL_UP, lambda: _DEGRADED)
        self.assertTrue(report["healthy"])
        self.assertEqual(report["rung_state"]["verdict"], "degraded")

    def test_at_rate_rung_never_flips_healthy_up(self) -> None:
        report = self._pass(_INV_CONDUCTOR_DOWN, lambda: _rung("at_rate"))
        self.assertFalse(report["healthy"])
        self.assertEqual(report["rung_state"]["verdict"], "at_rate")

    def test_reader_raising_gives_ok_false_and_the_pass_still_returns(self) -> None:
        def boom():
            raise OSError("tail unreadable")
        report = self._pass(_INV_ALL_UP, boom)
        self.assertFalse(report["rung_state"]["ok"])
        self.assertIn("tail unreadable", report["rung_state"]["error"])
        self.assertTrue(report["healthy"])
        # The rest of the pass is intact.
        self.assertIn("watchfire", report)
        self.assertIn("patrol_trend", report)
        self.assertIn("hindsight", report)

    def test_report_is_json_serializable(self) -> None:
        report = self._pass(_INV_ALL_UP, lambda: _UNREACHABLE)
        json.dumps(report)


class RungStateLedgerTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_event_recorded_with_outcome_verdict(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        out = wd.run_rung_state(write_ledger=True, rung_state_fn=lambda: _DEGRADED, ledger=led)
        self.assertIsNotNone(out["ledger_event_id"])
        events = led.query(tool="mechnet_watchdog.rung_state")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertTrue(ev["ok"])                    # the READ succeeded
        self.assertEqual(ev["outcome"], "degraded")  # the verdict is the outcome
        self.assertEqual(ev["caller"]["id"], "mechnet-watchdog")
        # The rate series is recoverable from the args preview, not just a digest.
        preview = json.loads(ev["args_preview"])
        self.assertEqual(preview["verdict"], "degraded")
        self.assertEqual(preview["observed_tok_s"], 65.0)
        self.assertEqual(preview["frac_of_baseline"], 0.6132)

    def test_unreachable_event_is_ok_true_outcome_unreachable(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")
        wd.run_rung_state(write_ledger=True, rung_state_fn=lambda: _UNREACHABLE, ledger=led)
        ev = led.query(tool="mechnet_watchdog.rung_state")[0]
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["outcome"], "unreachable")

    def test_failed_read_is_ok_false_with_a_named_error(self) -> None:
        from hearth.kernel.ledger import Ledger
        led = Ledger(self.tmp / "ledger")

        def boom():
            raise RuntimeError("no baselines")
        out = wd.run_rung_state(write_ledger=True, rung_state_fn=boom, ledger=led)
        self.assertFalse(out["ok"])
        # A failed read is returned without a ledger line: {ok: False} short-circuits
        # before the ledger (there is no verdict to series), and the pass still returns.
        self.assertNotIn("ledger_event_id", out)
        self.assertEqual(led.query(tool="mechnet_watchdog.rung_state"), [])

    def test_write_ledger_false_skips_the_record(self) -> None:
        with patch.object(wd, "_record_rung_state") as rec:
            wd.run_rung_state(write_ledger=False, rung_state_fn=lambda: _DEGRADED)
        rec.assert_not_called()

    def test_ledger_failure_returns_none_not_raise(self) -> None:
        class _Broken:
            def append(self, event):
                raise OSError("ledger locked by the gateway")
        out = wd.run_rung_state(write_ledger=True, rung_state_fn=lambda: _DEGRADED,
                                ledger=_Broken())
        self.assertTrue(out["ok"])
        self.assertIsNone(out["ledger_event_id"])


_STUB_REPORT = {"checked": 1, "down": 0, "revivable": 0, "alert_only": [],
                "revivals": [], "healthy": True}


class CLIRungStateTests(TestCase):
    def test_no_rung_state_flag_disables_the_read(self) -> None:
        with patch.object(wd, "run_pass", return_value=dict(_STUB_REPORT)) as rp:
            code = wd.main(["--dry-run", "--no-ledger", "--no-rung-state", "--json"])
        self.assertEqual(code, 0)
        self.assertFalse(rp.call_args.kwargs["include_rung_state"])

    def test_rung_state_is_on_by_default(self) -> None:
        with patch.object(wd, "run_pass", return_value=dict(_STUB_REPORT)) as rp:
            wd.main(["--dry-run", "--no-ledger", "--json"])
        self.assertTrue(rp.call_args.kwargs["include_rung_state"])

    def test_json_output_carries_rung_state_and_exit_code_is_liveness(self) -> None:
        report = dict(_STUB_REPORT, rung_state=wd.run_rung_state(
            write_ledger=False, rung_state_fn=lambda: _DEGRADED))
        with patch.object(wd, "run_pass", return_value=report), \
             patch("builtins.print") as mock_print:
            code = wd.main(["--dry-run", "--no-ledger", "--json"])
        self.assertEqual(code, 0)  # degraded rung, healthy liveness -> 0
        printed = json.loads(mock_print.call_args[0][0])
        self.assertEqual(printed["rung_state"]["verdict"], "degraded")
        self.assertTrue(printed["healthy"])

    def test_text_output_prints_a_rung_state_line_that_does_not_gate(self) -> None:
        report = dict(_STUB_REPORT, rung_state=wd.run_rung_state(
            write_ledger=False, rung_state_fn=lambda: _DEGRADED))
        with patch.object(wd, "run_pass", return_value=report), \
             patch("builtins.print") as mock_print:
            code = wd.main(["--dry-run", "--no-ledger"])
        lines = [c.args[0] for c in mock_print.call_args_list if c.args]
        rung_lines = [l for l in lines if l.startswith("rung-state:")]
        self.assertEqual(len(rung_lines), 1)
        self.assertIn("omen-arc degraded 65.0/106.0 tok/s (61% of epoch)", rung_lines[0])
        self.assertIn("does not gate", rung_lines[0])
        self.assertEqual(lines[-1], "verdict: HEALTHY")
        self.assertEqual(code, 0)

    def test_text_output_names_a_failed_read(self) -> None:
        report = dict(_STUB_REPORT, rung_state={"ok": False, "error": "OSError: tail", "verdict": "unknown"})
        with patch.object(wd, "run_pass", return_value=report), \
             patch("builtins.print") as mock_print:
            wd.main(["--dry-run", "--no-ledger"])
        lines = [c.args[0] for c in mock_print.call_args_list if c.args]
        self.assertTrue(any(l.startswith("rung-state: read failed") for l in lines), lines)
