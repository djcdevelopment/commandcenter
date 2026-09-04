from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from hearth.health import rungstate
from hearth.health.rungstate import (NOTE, parse_keepalive_bytes, read_keepalive,
                                     rung_state, summarize_for_notes)

T0 = datetime(2026, 9, 3, 2, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
NOW = T0.timestamp() + 1800.0  # 30 min after T0

BASE = {"tok_s": 106.0, "epoch": "2026-08-29T18:22 incumbent epoch",
        "fail_below": 0.8, "warn_below": 0.9, "note": NOTE}


def _ts(offset_s):
    return (T0 + timedelta(seconds=offset_s)).isoformat()


def ping(offset_s, ok=True, stall=False, port=8082):
    return {"ts": _ts(offset_s), "probe": "ARC-KEEPALIVE", "port": port, "ok": ok,
            "wall_ms": 50.0, "prompt_n": 1, "prompt_ms": 10.0, "predicted_n": 1,
            "predicted_ms": 0.0, "prefill_stall": stall}


def deep(offset_s, tok_s, port=8082):
    row = ping(offset_s, port=port)
    row.update({"predicted_n": 32, "predicted_ms": 32000.0 / tok_s,
                "decode_tok_s": tok_s, "decode_degraded": tok_s < 85})
    return row


def recent_pings(n=6, last=1790):
    return [ping(last - 30 * i) for i in range(n)]


class VerdictTests(TestCase):
    def test_correct_but_degraded(self):
        rows = recent_pings() + [deep(1700, 65.0)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "degraded")
        self.assertTrue(st["last_ping_ok"])
        self.assertEqual(st["observed_tok_s"], 65.0)
        self.assertAlmostEqual(st["frac_of_baseline"], 65 / 106, places=3)
        self.assertEqual(st["note"], NOTE)

    def test_liveness_is_not_health(self):
        # pings fine, but the last deep row is 20 minutes old -> stale, never at_rate
        rows = recent_pings() + [deep(1800 - 1200, 106.0)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "stale")
        self.assertNotEqual(st["verdict"], "at_rate")
        self.assertEqual(st["deep_samples"], 1)
        # no deep row at all -> also stale
        self.assertEqual(rung_state(recent_pings(), BASE, NOW)["verdict"], "stale")

    def test_latest_ping_failed_is_unreachable(self):
        rows = recent_pings() + [deep(1700, 106.0), ping(1795, ok=False)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "unreachable")
        self.assertFalse(st["last_ping_ok"])

    def test_no_rows_for_port_is_unreachable(self):
        rows = [ping(1790, port=8083), deep(1700, 106.0, port=8083)]
        self.assertEqual(rung_state(rows, BASE, NOW)["verdict"], "unreachable")

    def test_no_baseline(self):
        self.assertEqual(rung_state(recent_pings(), None, NOW)["verdict"], "no_baseline")

    def test_prefill_stall_is_stalled(self):
        rows = recent_pings() + [deep(1700, 106.0), ping(1795, stall=True)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "stalled")
        self.assertTrue(st["prefill_stall_recent"])

    def test_warn_band(self):
        # 92/106 = 0.868: inside [fail_below, warn_below). (98/106 = 0.925 is above warn_below -> at_rate.)
        rows = recent_pings() + [deep(1700, 92.0)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "warn")

    def test_at_rate(self):
        rows = recent_pings() + [deep(1700, 106.27)]
        st = rung_state(rows, BASE, NOW)
        self.assertEqual(st["verdict"], "at_rate")
        self.assertEqual(st["envelope"], {"fail_below": 0.8, "warn_below": 0.9})
        self.assertEqual(st["baseline_epoch"], BASE["epoch"])

    def test_window_exclusion_drops_degraded_row_and_names_window(self):
        rows = recent_pings() + [deep(1500, 106.0), deep(1700, 40.0)]
        win_start = T0.timestamp() + 1650
        win_end = T0.timestamp() + 1750
        st = rung_state(rows, BASE, NOW, windows=[(win_start, win_end, "arc-maintenance")])
        self.assertEqual(st["excluded_windows"], ["arc-maintenance"])
        self.assertEqual(st["observed_tok_s"], 106.0)
        self.assertEqual(st["verdict"], "at_rate")
        # without the window the same rows are degraded
        self.assertEqual(rung_state(rows, BASE, NOW)["verdict"], "degraded")

    def test_never_names_a_regime(self):
        st = rung_state(recent_pings() + [deep(1700, 27.5)], BASE, NOW)
        blob = json.dumps(st).lower()
        for word in ("plateau", "collapse", "regime", "level"):
            self.assertNotIn(word, blob.replace("no regime names", ""))


class ReadKeepaliveTests(TestCase):
    def test_tolerates_bom_and_truncated_first_line(self):
        good = [json.dumps(ping(i)) for i in range(5)]
        raw = "﻿" + "\n".join(good) + "\n"
        rows = parse_keepalive_bytes(raw.encode("utf-8"))
        self.assertEqual(len(rows), 5)
        # simulate a tail read that starts mid-line
        cut = raw.encode("utf-8")[40:]
        rows = parse_keepalive_bytes(cut, truncated_head=True)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all("ts" in r for r in rows))

    def test_read_keepalive_from_file_tail(self):
        good = [json.dumps(ping(i)) for i in range(50)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ka.jsonl")
            with open(p, "wb") as fh:
                fh.write(b"\xef\xbb\xbf" + "\n".join(good).encode("utf-8") + b"\nnot json\n")
            rows = read_keepalive(p, tail_bytes=600)
            self.assertGreater(len(rows), 0)
            self.assertLess(len(rows), 50)
            self.assertEqual(read_keepalive(os.path.join(d, "missing.jsonl")), [])


class LiveAndSummaryTests(TestCase):
    def test_live_rung_state_never_raises_on_missing_root(self):
        with tempfile.TemporaryDirectory() as d:
            st = rungstate.live_rung_state(root=d)
            self.assertEqual(st["verdict"], "no_baseline")
            self.assertEqual(st["note"], NOTE)

    def test_summarize_for_notes_length_and_no_semicolon(self):
        st = rung_state(recent_pings() + [deep(1700, 65.0)], BASE, NOW,
                        windows=[(0, 1, "a-very-long-window-name-" * 6)])
        s = summarize_for_notes(st)
        self.assertLessEqual(len(s), 96)
        self.assertNotIn(";", s)
        self.assertIn("degraded", s)


class LiveWindowExclusionTests(TestCase):
    def test_live_rung_state_excludes_ledgered_rotation_windows(self):
        """2026-09-03: live_rung_state never passed the rotation windows, so a proof's own probes
        (71-74 tok/s while a side model decoded) read as production's regime."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            var = os.path.join(d, "hearth", "var")
            os.makedirs(var)
            os.makedirs(os.path.join(d, "campaign", "ff-probes"))
            rows = recent_pings() + [deep(900, 72.0), deep(1500, 108.0)]
            with open(os.path.join(var, "arc-keepalive.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
            win = [{"ts": _ts(800), "event": "window.open", "name": "rot-side-test", "status": "open"},
                   {"ts": _ts(1000), "event": "window.close", "name": "rot-side-test", "status": "passed"}]
            with open(os.path.join(var, "rotation-windows.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(w) for w in win) + "\n")
            with mock.patch.object(rungstate, "load_baseline", return_value=dict(BASE)):
                st = rungstate.live_rung_state(root=d, now=NOW)
        self.assertEqual(st["excluded_windows"], ["rot-side-test"])
        self.assertEqual(st["verdict"], "at_rate", st)
        self.assertAlmostEqual(st["observed_tok_s"], 108.0)

