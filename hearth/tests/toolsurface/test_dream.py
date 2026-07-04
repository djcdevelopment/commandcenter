from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.dream import dream


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class DreamTests(TestCase):
    def test_successful_dream_returns_local_path_and_ledgers_as_watchdog(self):
        gen = _completed(stdout='noise\nRESULT {"ok": true, "filename": "dream_00001_.png", "peak_temp_c": 72}\n')
        fetch = _completed(stdout=base64.b64encode(b"\x89PNG-fake-bytes").decode())
        with tempfile.TemporaryDirectory() as td:
            with patch("hearth.toolsurface.dream.DREAMS_DIR", Path(td)), \
                 patch("hearth.toolsurface.dream._ledger_dream", return_value="evt-1") as led, \
                 patch("subprocess.run", side_effect=[gen, fetch]):
                out = dream("a recon drone dreaming of a phantom mech", seed=7)
            self.assertTrue(out["ok"])
            self.assertEqual(out["dreamer"], "mechnet-watchdog")
            self.assertTrue(out["local_path"].endswith("dream_00001_.png"))
            self.assertEqual(out["peak_temp_c"], 72)
            self.assertEqual(out["seed"], 7)
            self.assertTrue(Path(out["local_path"]).exists())
            led.assert_called_once()

    def test_art_mode_unreachable_fails_closed(self):
        with patch("hearth.toolsurface.dream._ledger_dream", return_value=None), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            out = dream("x")
        self.assertFalse(out["ok"])
        self.assertIn("art mode unreachable", out["error"])

    def test_thermal_throttle_reported_not_raised(self):
        gen = _completed(stdout='RESULT {"ok": false, "error": "thermal throttle at 96C", "peak_temp_c": 96}')
        with patch("hearth.toolsurface.dream._ledger_dream", return_value=None), \
             patch("subprocess.run", return_value=gen):
            out = dream("x")
        self.assertFalse(out["ok"])
        self.assertIn("thermal", out["error"])
        self.assertEqual(out["peak_temp_c"], 96)

    def test_exec_error_from_comfyui_fails_closed(self):
        gen = _completed(stdout='RESULT {"ok": false, "error": "comfyui unreachable: connection refused"}')
        with patch("hearth.toolsurface.dream._ledger_dream", return_value=None), \
             patch("subprocess.run", return_value=gen):
            out = dream("x")
        self.assertFalse(out["ok"])
        self.assertIn("unreachable", out["error"])

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            dream("   ")

    def test_negative_seed_is_randomized(self):
        # short-circuit on ssh error, but the seed must already be a real positive value
        with patch("hearth.toolsurface.dream._ledger_dream", return_value=None), \
             patch("subprocess.run", side_effect=OSError("no route")):
            out = dream("x", seed=-1)
        self.assertGreater(out["seed"], 0)
