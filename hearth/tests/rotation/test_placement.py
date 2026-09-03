"""Placement assertion (P3, ADR-0042): the load report decides, never an index."""
from __future__ import annotations

import unittest

from hearth.rotation.placement import assert_placement, parse_load_report

DUAL_OK = """
ggml_vulkan: Found 3 Vulkan devices:
- Vulkan0 : Intel(R) Graphics (Intel open-source Mesa driver) | uma: 1
- Vulkan1 : Intel(R) Arc(TM) Pro B70 Graphics (Intel Corporation) | uma: 0 (32558 MiB)
- Vulkan2 : Intel(R) Arc(TM) Pro B70 Graphics (Intel Corporation) | uma: 0 (32558 MiB)
llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics) - 31000 MiB free
llama_prepare_model_devices: using device Vulkan2 (Intel(R) Arc(TM) Pro B70 Graphics) - 31000 MiB free
load_tensors: offloaded 49/49 layers to GPU
load_tensors:      Vulkan1 model buffer size =  8464.87 MiB
load_tensors:      Vulkan2 model buffer size =  8937.51 MiB
load_tensors:  Vulkan_Host model buffer size =   315.30 MiB
llama_kv_cache:    Vulkan1 KV buffer size =   512.00 MiB
llama_kv_cache:    Vulkan2 KV buffer size =   512.00 MiB
sched_reserve:    Vulkan1 compute buffer size =   296.00 MiB
sched_reserve:    Vulkan2 compute buffer size =   296.00 MiB
"""

# The 2026-08-29 defect: the index filter selected one B70 plus the iGPU; the iGPU was dropped and
# all 49 layers landed on ONE card. A single-card entry that reports this is wrong for a dual rung
# and an iGPU with weights is always wrong.
IGPU_TOOK_WEIGHTS = """
llama_prepare_model_devices: using device Vulkan0 (Intel(R) Graphics) - 60000 MiB free
load_tensors: offloaded 40/40 layers to GPU
load_tensors:      Vulkan0 model buffer size =  8300.00 MiB
"""

SINGLE_OK = """
llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics) - 16000 MiB free
load_tensors: offloaded 40/40 layers to GPU
load_tensors:      Vulkan1 model buffer size =  8300.00 MiB
llama_kv_cache:    Vulkan1 KV buffer size =   256.00 MiB
"""

SPILLED = """
llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics)
load_tensors: offloaded 30/49 layers to GPU
load_tensors:      Vulkan1 model buffer size =  9000.00 MiB
load_tensors:          CPU model buffer size =  8000.00 MiB
"""


class ParseTests(unittest.TestCase):
    def test_parses_devices_buffers_and_layers(self) -> None:
        report = parse_load_report(DUAL_OK)
        self.assertTrue(report.parsed)
        self.assertEqual(report.using, ("Vulkan1", "Vulkan2"))
        self.assertEqual(report.offloaded_layers, 49)
        by = {d.handle: d for d in report.devices}
        self.assertAlmostEqual(by["Vulkan1"].model_mib, 8464.87)
        self.assertAlmostEqual(by["Vulkan2"].total_mib, 8937.51 + 512 + 296)
        self.assertTrue(by["Vulkan1"].is_b70)
        self.assertTrue(by["Vulkan0"].is_igpu)
        # Vulkan_Host is pinned host staging, not a device holding weights.
        self.assertEqual([d.handle for d in report.with_weights()], ["Vulkan1", "Vulkan2"])

    def test_empty_text_is_unparsed(self) -> None:
        self.assertFalse(parse_load_report("").parsed)
        self.assertFalse(parse_load_report("nothing here").parsed)


class AssertTests(unittest.TestCase):
    def test_dual_split_ok(self) -> None:
        verdict = assert_placement(parse_load_report(DUAL_OK), expected_cards=2)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertEqual(verdict.b70_with_weights, 2)
        self.assertFalse(verdict.igpu_with_weights)
        self.assertAlmostEqual(verdict.per_card_gb["Vulkan1"], (8464.87 + 512 + 296) / 1024, places=3)
        self.assertIsNone(verdict.bdf_corroborated)

    def test_single_card_ok(self) -> None:
        verdict = assert_placement(parse_load_report(SINGLE_OK), expected_cards=1)
        self.assertTrue(verdict.ok, verdict.reason)

    def test_igpu_with_weights_fails(self) -> None:
        verdict = assert_placement(parse_load_report(IGPU_TOOK_WEIGHTS), expected_cards=1)
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.igpu_with_weights)
        self.assertIn("iGPU", verdict.reason)

    def test_wrong_card_count_fails(self) -> None:
        verdict = assert_placement(parse_load_report(SINGLE_OK), expected_cards=2)
        self.assertFalse(verdict.ok)
        self.assertIn("expected 2", verdict.reason)

    def test_spill_to_host_fails(self) -> None:
        verdict = assert_placement(parse_load_report(SPILLED), expected_cards=1)
        self.assertFalse(verdict.ok)
        self.assertIn("spilled", verdict.reason)

    def test_unparseable_fails_closed(self) -> None:
        verdict = assert_placement(parse_load_report(""), expected_cards=1)
        self.assertFalse(verdict.ok)
        self.assertIn("unparseable", verdict.reason)

    def test_bdf_commit_signature_corroborates_or_refutes(self) -> None:
        report = parse_load_report(SINGLE_OK)
        before = {"0000:04:00.0": 14.5, "0000:09:00.0": 15.4}
        good = assert_placement(report, 1, before, {"0000:04:00.0": 22.9, "0000:09:00.0": 15.5})
        self.assertTrue(good.ok)
        self.assertTrue(good.bdf_corroborated)
        self.assertAlmostEqual(good.bdf_delta_gb["0000:04:00.0"], 8.4)
        both = assert_placement(report, 1, before, {"0000:04:00.0": 22.9, "0000:09:00.0": 23.8})
        self.assertFalse(both.ok)
        self.assertIn("commit signature", both.reason)
        none = assert_placement(report, 1, before, dict(before))
        self.assertFalse(none.ok)


if __name__ == "__main__":
    unittest.main()
