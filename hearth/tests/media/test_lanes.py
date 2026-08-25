"""Lane identity tests.

Every fixture here is verbatim output captured from this machine on 2026-08-25,
not invented. That matters: the whole point of the module under test is that
adapter *position* lies, so a test built on plausible-looking synthetic strings
would prove nothing about the thing that actually breaks.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hearth.media import lanes

# Real `vulkaninfo` output, trimmed to the fields the parser reads.
VULKANINFO = """
Devices:
========
GPU0:
\tapiVersion         = 1.4.356
\tvendorID           = 0x8086
\tdeviceID           = 0x7d67
\tdeviceName         = Intel(R) Graphics
\tdeviceUUID         = 8680677d-0600-0000-0002-000000000000
\tdeviceLUID         = 2f690100-00000000
GPU1:
\tapiVersion         = 1.4.356
\tvendorID           = 0x8086
\tdeviceID           = 0xe223
\tdeviceName         = Intel(R) Arc(TM) Pro B70 Graphics
\tdeviceUUID         = 868023e2-0000-0000-0900-000000000000
\tdeviceLUID         = 4b710100-00000000
GPU2:
\tapiVersion         = 1.4.356
\tvendorID           = 0x8086
\tdeviceID           = 0xe223
\tdeviceName         = Intel(R) Arc(TM) Pro B70 Graphics
\tdeviceUUID         = 868023e2-0000-0000-0400-000000000000
\tdeviceLUID         = 216d0100-00000000
"""

# `vulkaninfo --summary` omits deviceLUID entirely.
VULKANINFO_SUMMARY_NO_LUID = """
GPU1:
\tvendorID           = 0x8086
\tdeviceID           = 0xe223
\tdeviceName         = Intel(R) Arc(TM) Pro B70 Graphics
\tdeviceUUID         = 868023e2-0000-0000-0900-000000000000
"""


class IdentityMathTest(unittest.TestCase):
    def test_pci_bus_and_device_come_out_of_the_device_uuid(self) -> None:
        # Cross-checked against Get-PnpDeviceProperty DEVPKEY_Device_LocationInfo.
        self.assertEqual((9, 0), lanes.pci_from_device_uuid("868023e2-0000-0000-0900-000000000000"))
        self.assertEqual((4, 0), lanes.pci_from_device_uuid("868023e2-0000-0000-0400-000000000000"))
        self.assertEqual((0, 2), lanes.pci_from_device_uuid("8680677d-0600-0000-0002-000000000000"))

    def test_malformed_uuid_is_refused_rather_than_guessed(self) -> None:
        for bad in ("", "not-a-uuid", "868023e2-0000-0000-0900"):
            with self.assertRaises(lanes.CalibrationError):
                lanes.pci_from_device_uuid(bad)

    def test_luid_is_decoded_little_endian(self) -> None:
        # Vulkan prints raw bytes; the counters print big-endian halves. Reading
        # these in the wrong order yields a token that matches no counter
        # instance, which looks exactly like an idle GPU.
        self.assertEqual((0, 0x0001692F), lanes.decode_vulkan_luid("2f690100-00000000"))
        self.assertEqual((0, 0x0001714B), lanes.decode_vulkan_luid("4b710100-00000000"))
        self.assertEqual((0, 0x00016D21), lanes.decode_vulkan_luid("216d0100-00000000"))

    def test_malformed_luid_is_refused(self) -> None:
        with self.assertRaises(lanes.CalibrationError):
            lanes.decode_vulkan_luid("4b710100")

    def test_counter_token_matches_the_windows_instance_spelling(self) -> None:
        self.assertEqual(
            "luid_0x00000000_0x0001714b", lanes.luid_counter_token(0, 0x0001714B)
        )


class VulkaninfoParsingTest(unittest.TestCase):
    def test_parses_all_three_adapters(self) -> None:
        devices = lanes.parse_vulkaninfo(VULKANINFO)
        self.assertEqual(3, len(devices))
        by_bus = {device.pci_bus: device for device in devices}
        self.assertEqual("luid_0x00000000_0x0001714b", by_bus[9].counter_token)
        self.assertEqual("luid_0x00000000_0x00016d21", by_bus[4].counter_token)
        self.assertTrue(by_bus[9].is_arc_b70)
        self.assertTrue(by_bus[4].is_arc_b70)
        self.assertFalse(by_bus[0].is_arc_b70)

    def test_device_without_a_luid_is_skipped_not_guessed(self) -> None:
        # A lane we cannot observe on the counters is a lane we cannot schedule.
        self.assertEqual([], lanes.parse_vulkaninfo(VULKANINFO_SUMMARY_NO_LUID))

    def test_lane_ids_are_bus_derived_and_deterministically_ordered(self) -> None:
        devices = lanes.parse_vulkaninfo(VULKANINFO)
        selected = lanes.select_b70_devices(devices)
        self.assertEqual(["b70@bus4", "b70@bus9"], [d.lane_id for d in selected])

    def test_igpu_is_never_selected_as_a_render_lane(self) -> None:
        # `qsv:hw_any` picks the iGPU on this box; the lane map must not.
        selected = lanes.select_b70_devices(lanes.parse_vulkaninfo(VULKANINFO))
        self.assertNotIn(lanes.ARROWLAKE_IGPU_DEVICE_ID, [d.device_id for d in selected])


class FfmpegProbeParsingTest(unittest.TestCase):
    def test_parses_an_adapter_whose_name_contains_parentheses(self) -> None:
        # "Intel(R) Arc(TM) ..." -- a non-greedy capture truncates this to
        # "Intel(R", which is why the pattern runs to the LAST paren.
        line = (
            "[AVHWDeviceContext @ 0x1] Using device 8086:e223 "
            "(Intel(R) Arc(TM) Pro B70 Graphics)."
        )
        self.assertEqual(
            (0x8086, 0xE223, "Intel(R) Arc(TM) Pro B70 Graphics"),
            lanes.parse_ffmpeg_device_probe(line),
        )

    def test_returns_none_when_no_device_was_created(self) -> None:
        self.assertIsNone(lanes.parse_ffmpeg_device_probe("Failed to create Direct3D device"))

    def test_warp_placeholder_is_recognised_but_not_a_b70(self) -> None:
        line = "Using device 1414:008c (Microsoft Basic Render Driver)."
        vendor, device, _name = lanes.parse_ffmpeg_device_probe(line)
        self.assertEqual(lanes.WARP_VENDOR_ID, vendor)
        probe = {0: (0x8086, 0x7D67, "iGPU"), 1: (vendor, device, "warp"),
                 2: (0x8086, 0xE223, "arc"), 3: (0x8086, 0xE223, "arc")}
        # Indices 1 and 4 fail device creation and sit BETWEEN real adapters, so
        # a scan that stopped at the first failure would miss a card.
        self.assertEqual([2, 3], lanes.candidate_arc_indices(probe))


class CounterParsingTest(unittest.TestCase):
    PATHS = [
        r"\GPU Engine(pid_100_luid_0x00000000_0x0001714B_phys_0_eng_0_engtype_3D)\Utilization Percentage",
        r"\GPU Engine(pid_100_luid_0x00000000_0x0001714B_phys_0_eng_1_engtype_VideoDecode)\Utilization Percentage",
        r"\GPU Engine(pid_100_luid_0x00000000_0x0001714B_phys_0_eng_3_engtype_VideoProcessing)\Utilization Percentage",
        r"\GPU Engine(pid_999_luid_0x00000000_0x00016D21_phys_0_eng_1_engtype_VideoDecode)\Utilization Percentage",
        r"not a counter path at all",
    ]

    def test_filters_to_one_process(self) -> None:
        records = lanes.parse_counter_instances(self.PATHS, pid=100)
        self.assertEqual(3, len(records))
        self.assertEqual({100}, {record["pid"] for record in records})

    def test_media_engines_are_classified_from_measured_load(self) -> None:
        # llama-server holds idle instances on EVERY engine of every adapter
        # simply by opening a Vulkan device, so instance EXISTENCE proves
        # nothing. Only utilisation distinguishes work from presence.
        samples = [
            {"luid": "luid_0x00000000_0x0001714b", "engtype": "VideoDecode", "value": 76.99},
            {"luid": "luid_0x00000000_0x0001714b", "engtype": "3D", "value": 12.50},
            {"luid": "luid_0x00000000_0x0001714b", "engtype": "Compute", "value": 0.0},
        ]
        profile = lanes.engine_profile_for_luid(samples, "luid_0x00000000_0x0001714b")
        self.assertEqual(["VideoDecode"], lanes.classify_media_engines(profile))

    def test_engine_identity_is_never_a_module_constant(self) -> None:
        # The original design assumed engtype_VideoEncode. It does not exist on
        # this driver, and a live encode shows up under VideoDecode instead.
        # Rather than swap one accidental name for another, identity is
        # calibrated per lane -- so there must be no such constant to drift.
        self.assertFalse(hasattr(lanes, "MEDIA_ENGINE_TYPES"))
        self.assertFalse(hasattr(lanes, "MEDIA_ENGINE_PREFIX"))


class FingerprintTest(unittest.TestCase):
    def test_is_order_independent(self) -> None:
        devices = lanes.parse_vulkaninfo(VULKANINFO)
        forward = lanes.build_fingerprint(devices, "101.8974", "8.1.2")
        backward = lanes.build_fingerprint(list(reversed(devices)), "101.8974", "8.1.2")
        self.assertEqual(forward, backward)

    def test_driver_or_ffmpeg_change_invalidates_the_map(self) -> None:
        devices = lanes.parse_vulkaninfo(VULKANINFO)
        base = lanes.build_fingerprint(devices, "101.8974", "8.1.2")
        self.assertTrue(lanes.fingerprint_matches(base, dict(base)))
        self.assertFalse(
            lanes.fingerprint_matches(base, lanes.build_fingerprint(devices, "102.0", "8.1.2"))
        )
        self.assertFalse(
            lanes.fingerprint_matches(base, lanes.build_fingerprint(devices, "101.8974", "9.0"))
        )
        self.assertFalse(lanes.fingerprint_matches(base, {}))


class PersistenceTest(unittest.TestCase):
    def _calibration(self) -> lanes.Calibration:
        return lanes.Calibration(
            schema_version=lanes.SCHEMA_VERSION,
            fingerprint={"driver_version": "101.8974", "adapter_uuids": [], "ffmpeg_version": "8.1.2"},
            lanes=[
                lanes.Lane(lane_id="b70@bus9", pci_bus=9, device_uuid="uuid-9",
                           luid="luid_0x00000000_0x0001714b", child_device=2,
                           media_engines=["videodecode"],
                           engine_profile={"videodecode": 76.99, "3d": 12.5},
                           engtypes=["3d", "videodecode"], healthy=True,
                           detail="bound"),
                lanes.Lane(lane_id="b70@bus4", pci_bus=4, device_uuid="uuid-4",
                           luid="luid_0x00000000_0x00016d21", child_device=None,
                           media_engines=[], engine_profile={}, engtypes=[],
                           healthy=False, detail="unbound"),
            ],
            calibrated_at="2026-08-25T05:13:49Z",
        )

    def test_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lanes.json"
            lanes.save_calibration(self._calibration(), path)
            loaded = lanes.load_calibration(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(2, len(loaded.lanes))
            self.assertEqual(2, loaded.lanes[0].child_device)
            # The calibrated engine identity must survive persistence -- it is
            # the whole reason the gate does not hardcode a counter name.
            self.assertEqual(["videodecode"], loaded.lanes[0].media_engines)
            self.assertEqual(76.99, loaded.lanes[0].engine_profile["videodecode"])

    def test_healthy_lanes_are_sorted_and_exclude_unbound(self) -> None:
        # Deterministic order is the whole scheduling policy; an unbound lane is
        # reported, not hidden, but must never be scheduled.
        healthy = self._calibration().healthy_lanes()
        self.assertEqual(["b70@bus9"], [lane.lane_id for lane in healthy])

    def test_unreadable_map_fails_soft_to_none(self) -> None:
        # None means "recalibrate"; the scheduler reads zero healthy lanes as
        # zero capacity, which is the safe direction.
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "absent.json"
            self.assertIsNone(lanes.load_calibration(missing))
            garbage = Path(temporary) / "garbage.json"
            garbage.write_text("{not json", encoding="utf-8")
            self.assertIsNone(lanes.load_calibration(garbage))
            wrong = Path(temporary) / "wrong.json"
            wrong.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            self.assertIsNone(lanes.load_calibration(wrong))

    def test_save_is_atomic_leaving_no_tmp_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lanes.json"
            lanes.save_calibration(self._calibration(), path)
            self.assertEqual([], list(Path(temporary).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
