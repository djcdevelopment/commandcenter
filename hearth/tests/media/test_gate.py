"""The gaming/OBS gate.

Two behaviours matter most and both are easy to get subtly wrong:

* it withholds a LANE, not the whole renderer -- OBS encodes on one B70, so the
  other stays available and the queue keeps moving;
* it never matches ``bf6.exe`` by name, because there are two of them and one is
  a 57 KB capture-test stub.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from hearth.media import gate
from hearth.media import lanes as L

LUID_A = "luid_0x00000000_0x00016d21"   # b70@bus4
LUID_B = "luid_0x00000000_0x0001714b"   # b70@bus9

STEAM = r"C:\Program Files (x86)\Steam\steamapps\common\Battlefield 6\bf6.exe"
STUB = r"E:\omen\bf6-highlights\runtime\bf6.exe"


def lane(lane_id, luid, bus):
    return L.Lane(lane_id=lane_id, pci_bus=bus, device_uuid="u", luid=luid,
                  child_device=bus, media_engines=["videodecode"],
                  engine_profile={}, engtypes=[], healthy=True, detail="")


LANES = [lane("b70@bus4", LUID_A, 4), lane("b70@bus9", LUID_B, 9)]


class GameDetectionTest(unittest.TestCase):
    def test_the_steam_install_counts_as_gaming(self) -> None:
        gaming, detail = gate.classify_game([(100, STEAM)])
        self.assertTrue(gaming)
        self.assertIn("Steam", detail)

    def test_the_test_stub_is_ignored_by_default(self) -> None:
        # A name-only match would withhold a lane every time the capture
        # harness runs, for no reason.
        gaming, _ = gate.classify_game([(100, STUB)])
        self.assertFalse(gaming)

    def test_the_test_stub_counts_only_in_test_mode(self) -> None:
        gaming, detail = gate.classify_game([(100, STUB)], test_mode=True)
        self.assertTrue(gaming)
        self.assertIn("STUB", detail)

    def test_an_unreadable_path_is_treated_as_the_game(self) -> None:
        # Fail safe: not being able to read the path is not evidence of a stub.
        gaming, _ = gate.classify_game([(100, "")])
        self.assertTrue(gaming)

    def test_no_process_is_not_gaming(self) -> None:
        self.assertEqual((False, "no Battlefield 6 process"), gate.classify_game([]))

    def test_parses_process_lines(self) -> None:
        parsed = gate.parse_processes("100|%s\n\nnot-a-line\n" % STEAM)
        self.assertEqual([(100, STEAM)], parsed)


class RecordingDetectionTest(unittest.TestCase):
    def test_last_marker_wins(self) -> None:
        log = "\n".join([
            "19:39:02.659: ==== Recording Start ====",
            "21:01:51.985: ==== Recording Stop ====",
            "21:10:00.000: ==== Recording Start ====",
        ])
        self.assertTrue(gate.recording_from_log(log))

    def test_stopped_after_start(self) -> None:
        log = "==== Recording Start ====\n==== Recording Stop ===="
        self.assertFalse(gate.recording_from_log(log))

    def test_silence_is_unknown_not_idle(self) -> None:
        self.assertIsNone(gate.recording_from_log("nothing relevant here"))


class ContendedLaneTest(unittest.TestCase):
    def test_identifies_the_adapter_carrying_media_work(self) -> None:
        totals = {(LUID_A, "videodecode"): 65.0, (LUID_B, "videodecode"): 0.0}
        self.assertEqual(LUID_A, gate.busiest_luid(totals, ["videodecode"]))

    def test_ignores_non_media_engines(self) -> None:
        # A process merely holding a Vulkan device must not look like a recorder.
        totals = {(LUID_A, "3D"): 99.0, (LUID_B, "Compute"): 99.0}
        self.assertIsNone(gate.busiest_luid(totals, ["videodecode"]))

    def test_no_load_means_no_contended_lane(self) -> None:
        self.assertIsNone(gate.busiest_luid({}, ["videodecode"]))


class GatePolicyTest(unittest.TestCase):
    def _gate(self, *, processes, engines=None, log=None, test_mode=False):
        return gate.make_gate(
            lambda: LANES,
            process_probe=lambda image: processes.get(image, []),
            engine_probe=lambda pid: engines or {},
            log_reader=lambda: log,
            test_mode=test_mode,
        )

    def test_idle_machine_withholds_nothing(self) -> None:
        g = self._gate(processes={})
        for item in LANES:
            self.assertEqual((False, ""), g(item))

    def test_recording_withholds_only_the_contended_lane(self) -> None:
        # THE central behaviour: OBS encodes on one B70; the other keeps working.
        g = self._gate(
            processes={gate.OBS_IMAGE: [(500, r"C:\obs\obs64.exe")]},
            engines={(LUID_A, "videodecode"): 70.0},
            log="==== Recording Start ====",
        )
        withheld_a, reason_a = g(LANES[0])
        withheld_b, _ = g(LANES[1])
        self.assertTrue(withheld_a)
        self.assertIn("b70@bus4", reason_a)
        self.assertFalse(withheld_b, "the free card must stay schedulable")

    def test_obs_running_but_not_recording_withholds_nothing(self) -> None:
        g = self._gate(
            processes={gate.OBS_IMAGE: [(500, r"C:\obs\obs64.exe")]},
            log="==== Recording Start ====\n==== Recording Stop ====",
        )
        for item in LANES:
            self.assertEqual((False, ""), g(item))

    def test_unknown_recording_state_is_treated_as_recording(self) -> None:
        # OBS up with an unreadable log is unknown, not idle.
        g = self._gate(processes={gate.OBS_IMAGE: [(500, r"C:\obs\obs64.exe")]},
                       log=None)
        withheld, reason = g(LANES[0])
        self.assertTrue(withheld)
        self.assertIn("unidentified", reason)

    def test_unidentified_contended_lane_withholds_all(self) -> None:
        # Guessing wrong stutters the game or the recording.
        g = self._gate(
            processes={gate.OBS_IMAGE: [(500, r"C:\obs\obs64.exe")]},
            engines={}, log="==== Recording Start ====",
        )
        for item in LANES:
            withheld, reason = g(item)
            self.assertTrue(withheld)
            self.assertIn("withholding all", reason)

    def test_gaming_without_obs_withholds_all_when_unidentified(self) -> None:
        g = self._gate(processes={gate.GAME_IMAGE: [(100, STEAM)]})
        withheld, reason = g(LANES[0])
        self.assertTrue(withheld)
        self.assertIn("Steam", reason)

    def test_the_stub_alone_does_not_withhold(self) -> None:
        g = self._gate(processes={gate.GAME_IMAGE: [(100, STUB)]})
        for item in LANES:
            self.assertEqual((False, ""), g(item))

    def test_gate_plugs_into_the_scheduler_lane_filter(self) -> None:
        # The already-built filter consumes exactly this shape.
        import tempfile
        from pathlib import Path
        from hearth.execution.coordination import CapacityLeaseStore
        from hearth.media import scheduler as S

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            leases = CapacityLeaseStore(Path(temp) / "c.sqlite")
            g = self._gate(
                processes={gate.OBS_IMAGE: [(500, r"C:\obs\obs64.exe")]},
                engines={(LUID_A, "videodecode"): 70.0},
                log="==== Recording Start ====",
            )
            candidates, decision = S.select_lane_candidates(
                LANES, accepted_lane_count=2, leases=leases, gate=g)
            self.assertEqual(["b70@bus9"], [c.lane_id for c in candidates])
            self.assertIn("withheld", decision.rejected["b70@bus4"])


if __name__ == "__main__":
    unittest.main()
