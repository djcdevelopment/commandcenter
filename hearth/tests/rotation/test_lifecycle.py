"""load_with_assertion (P4): fence -> admission -> ready -> placement, sibling retry, receipts."""
from __future__ import annotations

import unittest

from hearth.rotation.admission import AdmissionGates
import tempfile
from pathlib import Path
from unittest.mock import patch

from hearth.rotation import lifecycle as L
from hearth.rotation.lifecycle import load_with_assertion, select_model_log
from hearth.rotation.swapclient import LoadOutcome
from hearth.rotation.telemetry import CardTelemetry, HostTelemetry

A = "0000:04:00.0"
B = "0000:09:00.0"
GOOD_LOG = """
[phi4-vk1] llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics) - 16000 MiB free
[phi4-vk1] load_tensors: offloaded 40/40 layers to GPU
[phi4-vk1] load_tensors:      Vulkan1 model buffer size =  8300.00 MiB
"""
IGPU_LOG = """
[phi4-vk1] llama_prepare_model_devices: using device Vulkan0 (Intel(R) Graphics) - 60000 MiB free
[phi4-vk1] load_tensors: offloaded 40/40 layers to GPU
[phi4-vk1] load_tensors:      Vulkan0 model buffer size =  8300.00 MiB
"""


class _Client:
    def __init__(self, ready=True, logs_by_entry=None):
        self.ready = ready
        self.logs_by_entry = logs_by_entry or {}
        self.unloaded = []
        self.loaded = []
        self.current = None

    def running(self):
        from hearth.rotation.swapclient import RunningModel
        return [RunningModel(m, "ready") for m in getattr(self, "resident", [])]

    def wait_ready(self, entry, deadline_s=300.0):
        self.loaded.append(entry)
        self.current = entry
        return LoadOutcome(self.ready, 8.2, 503 if self.ready else 0, {"predicted_n": 1} if self.ready else None,
                           None if self.ready else "upstream /health 0", 3)

    def logs(self):
        return self.logs_by_entry.get(self.current, "")

    def unload(self, entry):
        self.unloaded.append(entry)
        return True


def _telemetry(local_a=14.52, local_b=15.44):
    return HostTelemetry("t", 39.0, (
        CardTelemetry(A, "Intel(R) Arc(TM) Pro B70 Graphics", "x", 32.5, local_a, 0.0, 52.0, 52.0),
        CardTelemetry(B, "Intel(R) Arc(TM) Pro B70 Graphics", "y", 32.5, local_b, 0.0, 50.0, 50.0)))


def _snapshots(*snaps):
    it = iter(snaps)
    return lambda: next(it)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        # The default log reader looks in hearth/var/swap-logs/; a live proof populates that dir,
        # so tests point it at an empty temp dir and exercise the llama-swap-tail fallback.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(L, "SWAP_LOG_DIR", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def _common(self, **over):
        base = dict(expected_cards=1, per_card_gb=8.3, vram_gb=None, placement="single",
                    resident_by_bdf={A: 14.52, B: 15.44}, gates=AdmissionGates(),
                    fence=lambda: None, deadline_s=30.0)
        base.update(over)
        return base

    def test_happy_path_first_entry_lands_on_one_b70_with_commit_corroboration(self) -> None:
        client = _Client(logs_by_entry={"phi4-vk1": GOOD_LOG})
        snaps = _snapshots(_telemetry(), _telemetry(local_a=14.52 + 8.4))
        out = load_with_assertion(client, ["phi4-vk1", "phi4-vk0"], snapshot=snaps, **self._common())
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(out.entry_used, "phi4-vk1")
        self.assertEqual(out.attempts, 1)
        self.assertEqual(client.unloaded, [])
        self.assertTrue(out.verdict.bdf_corroborated)
        steps = [e["step"] for e in out.events]
        self.assertEqual(steps, ["fence", "telemetry", "admission", "load", "ready", "telemetry", "placement"])

    def test_already_resident_entry_skips_the_commit_corroboration(self) -> None:
        # 2026-09-03: phi4-vk1 was resident from an earlier call; the retry saw a 0 GB delta and
        # unloaded a correct placement. The log report decides; the receipt names the case.
        client = _Client(logs_by_entry={"phi4-vk1": GOOD_LOG})
        client.resident = ["phi4-vk1"]
        snaps = _snapshots(_telemetry(local_a=14.52 + 8.4), _telemetry(local_a=14.52 + 8.4))
        out = load_with_assertion(client, ["phi4-vk1", "phi4-vk0"], snapshot=snaps, **self._common())
        self.assertTrue(out.ok, out.reason)
        self.assertIsNone(out.verdict.bdf_corroborated)
        placement = [e for e in out.events if e["step"] == "placement"][0]
        self.assertTrue(placement["already_resident"])
        self.assertEqual(client.unloaded, [])

    def test_igpu_on_first_entry_unloads_and_retries_the_sibling(self) -> None:
        client = _Client(logs_by_entry={"phi4-vk1": IGPU_LOG, "phi4-vk0": GOOD_LOG.replace("phi4-vk1", "phi4-vk0")})
        snaps = _snapshots(_telemetry(), _telemetry(), _telemetry(local_a=14.52 + 8.4))
        out = load_with_assertion(client, ["phi4-vk1", "phi4-vk0"], snapshot=snaps, **self._common())
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(out.entry_used, "phi4-vk0")
        self.assertEqual(out.attempts, 2)
        self.assertEqual(client.unloaded, ["phi4-vk1"])

    def test_both_entries_wrong_fails_and_unloads_both(self) -> None:
        client = _Client(logs_by_entry={"phi4-vk1": IGPU_LOG, "phi4-vk0": IGPU_LOG.replace("phi4-vk1", "phi4-vk0")})
        snaps = _snapshots(_telemetry(), _telemetry(), _telemetry())
        out = load_with_assertion(client, ["phi4-vk1", "phi4-vk0"], snapshot=snaps, **self._common())
        self.assertFalse(out.ok)
        self.assertEqual(client.unloaded, ["phi4-vk1", "phi4-vk0"])
        self.assertIn("iGPU", out.reason)

    def test_active_image_session_refuses_before_any_load(self) -> None:
        client = _Client()
        out = load_with_assertion(client, ["phi4-vk1"], snapshot=_snapshots(_telemetry()),
                                  **self._common(fence=lambda: "imgsess_x"))
        self.assertFalse(out.ok)
        self.assertIn("imgsess_x", out.reason)
        self.assertEqual(client.loaded, [])
        self.assertEqual(out.events[0]["step"], "refused")

    def test_unreadable_fence_refuses_fail_closed(self) -> None:
        client = _Client()
        out = load_with_assertion(client, ["phi4-vk1"], snapshot=_snapshots(_telemetry()),
                                  **self._common(fence=lambda: "unreadable"))
        self.assertFalse(out.ok)
        self.assertIn("fail closed", out.reason)
        self.assertEqual(client.loaded, [])

    def test_admission_refusal_stops_before_load(self) -> None:
        client = _Client()
        out = load_with_assertion(client, ["phi4-vk1"], snapshot=_snapshots(HostTelemetry("t", 5.0, _telemetry().cards)),
                                  **self._common())
        self.assertFalse(out.ok)
        self.assertEqual(out.admission.reason_code, "commit_floor")
        self.assertEqual(client.loaded, [])

    def test_not_ready_unloads_and_tries_the_sibling(self) -> None:
        client = _Client(ready=False)
        snaps = _snapshots(_telemetry(), _telemetry(), _telemetry())
        out = load_with_assertion(client, ["phi4-vk1", "phi4-vk0"], snapshot=snaps, **self._common())
        self.assertFalse(out.ok)
        self.assertEqual(client.unloaded, ["phi4-vk1", "phi4-vk0"])
        self.assertIn("not ready", out.reason)

    def test_select_model_log_keeps_the_tagged_lines_of_the_last_load(self) -> None:
        text = "old\n[a] x\n[phi4-vk1] one\nmore\n[phi4-vk1] two\nend\n"
        self.assertEqual(select_model_log(text, "phi4-vk1"), "[phi4-vk1] one\n[phi4-vk1] two\n")
        reload = ("[m] ggml_vulkan: Found 3 Vulkan devices:\n[m] using device Vulkan1 (A)\n[m] Vulkan1 model buffer size = 1 MiB\n"
                  "[m] ggml_vulkan: Found 3 Vulkan devices:\n[m] using device Vulkan1 (A)\n[m] using device Vulkan2 (B)\n"
                  "[m] Vulkan1 model buffer size = 2 MiB\n[m] Vulkan2 model buffer size = 3 MiB\n")
        kept = select_model_log(reload, "m")
        self.assertEqual(kept.count("Found 3 Vulkan devices"), 1)
        self.assertIn("using device Vulkan2", kept)
        self.assertNotIn("= 1 MiB", kept)
        self.assertEqual(select_model_log("no mention", "phi4-vk1"), "no mention")


if __name__ == "__main__":
    unittest.main()
