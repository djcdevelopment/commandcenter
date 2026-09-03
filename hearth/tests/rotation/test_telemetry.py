"""b70tools parser + snapshot (P4): BDF identity, null != 0, never invoked for real here."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hearth.rotation.telemetry import (HostTelemetry, b70_snapshot, commit_free_gb, parse_b70_events)

GB = 1024 ** 3


def _events():
    rows = [
        {"k": "ai", "a": "adapter_00016b87", "bdf": "0000:00:02.0", "desc": "Intel(R) Graphics", "dvm": 0},
        {"k": "ai", "a": "adapter_00016f14", "bdf": "0000:04:00.0", "desc": "Intel(R) Arc(TM) Pro B70 Graphics", "dvm": 32558 * 1024 * 1024},
        {"k": "ai", "a": "adapter_00017310", "bdf": "0000:09:00.0", "desc": "Intel(R) Arc(TM) Pro B70 Graphics", "dvm": 32558 * 1024 * 1024},
        {"k": "ms", "a": "adapter_00016f14", "n": "gpu.adapter.vram.local.bytes_committed", "v": 14.52 * GB},
        {"k": "ms", "a": "adapter_00017310", "n": "gpu.adapter.vram.local.bytes_committed", "v": 15.44 * GB},
        {"k": "ms", "a": "adapter_00016f14", "n": "vram.temperature_c", "v": 52},
        {"k": "ms", "a": "adapter_00017310", "n": "gpu.temperature_c", "v": 50},
        {"k": "ms", "a": "adapter_00016f14", "n": "gpu.adapter.vram.local.bytes_committed", "v": 14.6 * GB},  # last wins
    ]
    return "\ufeff" + "\n".join(json.dumps(r) for r in rows) + "\nnot json\n"


class ParseTests(unittest.TestCase):
    def test_b70s_keyed_by_bdf_last_value_wins_igpu_excluded(self) -> None:
        cards = parse_b70_events(_events())
        self.assertEqual([c.bdf for c in cards], ["0000:04:00.0", "0000:09:00.0"])
        a, b = cards
        self.assertAlmostEqual(a.local_committed_gb, 14.6, places=2)
        self.assertAlmostEqual(b.local_committed_gb, 15.44, places=2)
        self.assertEqual(a.vram_temp_c, 52.0)
        self.assertIsNone(a.gpu_temp_c)
        self.assertEqual(b.gpu_temp_c, 50.0)
        self.assertIsNone(b.vram_temp_c)
        self.assertAlmostEqual(a.dedicated_vram_gb, 31.795, places=2)

    def test_missing_metric_is_none_not_zero(self) -> None:
        rows = [{"k": "ai", "a": "x", "bdf": "0000:04:00.0", "desc": "Intel(R) Arc(TM) Pro B70 Graphics"}]
        (card,) = parse_b70_events("\n".join(json.dumps(r) for r in rows))
        self.assertIsNone(card.local_committed_gb)
        self.assertIsNone(card.gpu_temp_c)
        self.assertIsNone(card.dedicated_vram_gb)

    def test_adapter_without_bdf_is_ignored(self) -> None:
        rows = [{"k": "ai", "a": "x", "desc": "Intel(R) Arc(TM) Pro B70 Graphics"}]
        self.assertEqual(parse_b70_events("\n".join(json.dumps(r) for r in rows)), ())


class SnapshotTests(unittest.TestCase):
    def test_snapshot_runs_the_documented_invocation_and_parses_the_output(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_exe = Path(tmp.name) / "b70tools.exe"
        fake_exe.write_bytes(b"")
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            out = Path(cmd[cmd.index("--out") + 1])
            (out / "events.jsonl").write_text(_events(), encoding="utf-8")
            return None

        snap = b70_snapshot(runner=runner, b70tools=str(fake_exe), scratch=Path(tmp.name) / "scratch",
                            commit_reader=lambda: 39.2)
        self.assertIsInstance(snap, HostTelemetry)
        self.assertEqual(snap.commit_free_gb, 39.2)
        self.assertEqual([c.bdf for c in snap.cards], ["0000:04:00.0", "0000:09:00.0"])
        self.assertIsNone(snap.note)
        self.assertEqual(seen["cmd"][1:8], ["--run", "--ticks", "1", "--cadence-ms", "250", "--no-sleep", "--flush-every-tick"])
        self.assertEqual(snap.local_committed_by_bdf()["0000:09:00.0"], 15.44)

    def test_missing_binary_yields_no_cards_and_a_note(self) -> None:
        snap = b70_snapshot(runner=lambda *a, **k: None, b70tools=r"C:\nope\b70tools.exe", commit_reader=lambda: 10.0)
        self.assertEqual(snap.cards, ())
        self.assertIn("not found", snap.note)
        self.assertEqual(snap.commit_free_gb, 10.0)

    def test_commit_reader_failure_is_none(self) -> None:
        def boom():
            raise OSError("x")

        snap = b70_snapshot(runner=lambda *a, **k: None, b70tools=r"C:\nope\b70tools.exe", commit_reader=boom)
        self.assertIsNone(snap.commit_free_gb)

    def test_commit_free_gb_reads_a_number_on_windows(self) -> None:
        value = commit_free_gb()
        import os
        if os.name == "nt":
            self.assertIsInstance(value, float)
            self.assertGreater(value, 0.0)
        else:
            self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
