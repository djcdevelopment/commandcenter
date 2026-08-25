"""Media-occupancy tests.

The central assertion here is NEGATIVE: this layer must not know the name of any
Windows performance counter engine. The suite therefore drives it with lanes
whose calibrated engine names are deliberately invented -- if the implementation
ever falls back to looking for "VideoEncode", "VideoDecode" or anything else
by name, these tests break.

That matters because the real hardware already broke the obvious assumption
once: there is no VideoEncode node on this driver, and a live encode shows up
under VideoDecode instead. The next driver could move it again.
"""
from __future__ import annotations

import unittest

from hearth.media import lanes as L
from hearth.media import occupancy as occ

LUID = "luid_0x00000000_0x0001714b"
OTHER_LUID = "luid_0x00000000_0x00016d21"


def lane(media_engines, luid=LUID, lane_id="b70@bus9"):
    return L.Lane(
        lane_id=lane_id, pci_bus=9, device_uuid="uuid", luid=luid, child_device=2,
        media_engines=list(media_engines), engine_profile={}, engtypes=[],
        healthy=True, detail="",
    )


def counter_line(luid, engtype, value, pid=999, eng=1):
    return r"\\HOST\GPU Engine(pid_%d_%s_phys_0_eng_%d_engtype_%s)\Utilization Percentage|%s" % (
        pid, luid, eng, engtype, value
    )


class EngineIdentityIsCalibratedTest(unittest.TestCase):
    def test_uses_whatever_engine_the_lane_was_calibrated_with(self) -> None:
        # A name that exists on no Windows system. If the implementation reads
        # the lane, this works; if it looks for a known counter name, it fails.
        invented = "QuantumCodecUnit"
        sample = "\n".join([counter_line(LUID, invented, 88.0)])
        result = occ.media_occupancy(lane([invented]), sampler=lambda: sample)
        self.assertTrue(result.known)
        self.assertTrue(result.busy)
        self.assertAlmostEqual(88.0, result.utilisation_pct)

    def test_ignores_engines_the_lane_was_not_calibrated_with(self) -> None:
        # VideoEncode is the name the original design assumed. A lane calibrated
        # to something else must not be judged by it.
        sample = "\n".join([
            counter_line(LUID, "VideoEncode", 95.0),
            counter_line(LUID, "MediaXe", 3.0),
        ])
        result = occ.media_occupancy(lane(["MediaXe"]), sampler=lambda: sample)
        self.assertTrue(result.known)
        self.assertFalse(result.busy, "VideoEncode must not leak into the reading")
        self.assertAlmostEqual(3.0, result.utilisation_pct)

    def test_does_not_count_the_compute_engine(self) -> None:
        # 3D/Compute is what Vulkan inference uses. Counting it would collapse
        # the two occupancy dimensions into one.
        sample = "\n".join([
            counter_line(LUID, "3D", 99.0),
            counter_line(LUID, "Compute", 99.0),
            counter_line(LUID, "videodecode", 1.0),
        ])
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: sample)
        self.assertFalse(result.busy)
        self.assertAlmostEqual(1.0, result.utilisation_pct)

    def test_engine_name_matching_is_case_insensitive(self) -> None:
        sample = counter_line(LUID, "VideoDecode", 50.0)
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: sample)
        self.assertTrue(result.busy)

    def test_sums_across_engine_instances_of_the_same_type(self) -> None:
        # One logical engine is exposed as several eng_N nodes.
        sample = "\n".join([
            counter_line(LUID, "videodecode", 12.0, eng=1),
            counter_line(LUID, "videodecode", 11.0, eng=4),
        ])
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: sample)
        self.assertAlmostEqual(23.0, result.utilisation_pct)
        self.assertTrue(result.busy)

    def test_sums_across_processes_not_just_our_own(self) -> None:
        # OBS recording contends for the codec engine exactly as our render does.
        sample = "\n".join([
            counter_line(LUID, "videodecode", 15.0, pid=111),
            counter_line(LUID, "videodecode", 15.0, pid=222),
        ])
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: sample)
        self.assertAlmostEqual(30.0, result.utilisation_pct)
        self.assertTrue(result.busy)

    def test_other_adapters_do_not_affect_this_lane(self) -> None:
        sample = "\n".join([
            counter_line(OTHER_LUID, "videodecode", 99.0),
            counter_line(LUID, "videodecode", 2.0),
        ])
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: sample)
        self.assertAlmostEqual(2.0, result.utilisation_pct)
        self.assertFalse(result.busy)


class FailClosedTest(unittest.TestCase):
    def test_uncalibrated_lane_is_reported_busy_and_unknown(self) -> None:
        # "We cannot tell" must never schedule.
        result = occ.media_occupancy(lane([]), sampler=lambda: "")
        self.assertTrue(result.busy)
        self.assertFalse(result.known)
        self.assertIn("recalibrate", result.detail)

    def test_sampler_failure_is_reported_busy_and_unknown(self) -> None:
        def boom():
            raise OSError("counters unavailable")

        result = occ.media_occupancy(lane(["videodecode"]), sampler=boom)
        self.assertTrue(result.busy)
        self.assertFalse(result.known)

    def test_empty_sample_is_known_and_idle(self) -> None:
        # Successfully reading zero load is different from failing to read.
        result = occ.media_occupancy(lane(["videodecode"]), sampler=lambda: "")
        self.assertTrue(result.known)
        self.assertFalse(result.busy)


class ClassificationTest(unittest.TestCase):
    def test_dominance_picks_the_codec_engine_and_rejects_3d(self) -> None:
        # The REAL measured profile from this machine.
        profile = {"videodecode": 76.99, "3d": 12.5, "copy": 0.0,
                   "videoprocessing": 0.0, "compute": 0.0, "gsc": 0.0}
        self.assertEqual(["videodecode"], L.classify_media_engines(profile))

    def test_would_pick_a_renamed_engine_without_code_changes(self) -> None:
        # The whole point: a future driver taxonomy still calibrates correctly.
        profile = {"MediaEngine0": 80.0, "3d": 10.0}
        self.assertEqual(["MediaEngine0"], L.classify_media_engines(profile))

    def test_picks_multiple_engines_when_work_is_genuinely_split(self) -> None:
        profile = {"videodecode": 60.0, "videoprocessing": 55.0, "3d": 5.0}
        self.assertEqual(["videodecode", "videoprocessing"],
                         L.classify_media_engines(profile))

    def test_an_idle_probe_classifies_nothing(self) -> None:
        # Guards the calibration hazard where a trivial source leaves the
        # encoder idle and the profile inverts.
        self.assertEqual([], L.classify_media_engines({"3d": 1.0, "videodecode": 0.0}))
        self.assertEqual([], L.classify_media_engines({}))

    def test_profile_sums_instances_per_engine(self) -> None:
        samples = [
            {"luid": LUID, "engtype": "videodecode", "value": 30.0},
            {"luid": LUID, "engtype": "videodecode", "value": 25.0},
            {"luid": OTHER_LUID, "engtype": "videodecode", "value": 99.0},
        ]
        self.assertEqual({"videodecode": 55.0},
                         L.engine_profile_for_luid(samples, LUID))


class SpillGuardTest(unittest.TestCase):
    GB = 1024.0 ** 3

    def _sample(self, luid, gb):
        return r"\\HOST\GPU Adapter Memory(%s_phys_0)\Shared Usage|%d" % (luid, gb * self.GB)

    def test_reads_shared_usage_for_the_right_adapter(self) -> None:
        raw = "\n".join([self._sample(LUID, 0.25), self._sample(OTHER_LUID, 9.0)])
        self.assertAlmostEqual(0.25, occ.shared_memory_gb(LUID, sampler=lambda: raw), places=3)

    def test_flags_a_rise_above_the_baseline(self) -> None:
        raw = self._sample(LUID, 2.0)
        is_spilling, detail = occ.spilling(lane(["videodecode"]), idle_baseline_gb=0.03,
                                           sampler=lambda: raw)
        self.assertTrue(is_spilling)
        self.assertIn("above", detail)

    def test_allows_normal_variation_around_the_baseline(self) -> None:
        raw = self._sample(LUID, 0.30)
        is_spilling, _ = occ.spilling(lane(["videodecode"]), idle_baseline_gb=0.236,
                                      sampler=lambda: raw)
        self.assertFalse(is_spilling)

    def test_unreadable_shared_memory_does_not_remove_all_capacity(self) -> None:
        # Unlike occupancy, an unknown spill reading must not withhold the lane:
        # media occupancy already fails closed, and doubling up would leave a
        # machine with unreadable counters unable to render at all.
        is_spilling, detail = occ.spilling(lane(["videodecode"]), sampler=lambda: "")
        self.assertFalse(is_spilling)
        self.assertIn("unreadable", detail)


class NoHardcodedCounterNamesTest(unittest.TestCase):
    def test_executable_code_never_names_a_gpu_engine(self) -> None:
        # A structural guard: the next person to "simplify" this by grepping for
        # VideoEncode/VideoDecode trips here rather than in production.
        #
        # Only EXECUTABLE code is checked. Comments and docstrings SHOULD name
        # the engines -- that prose is the record of why this indirection
        # exists, and deleting it invites the exact regression it prevents.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(occ))
        # Drop every docstring, leaving literals that actually run.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:]

        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        names = [
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        ] + [
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        ]
        haystack = " ".join(literals + names).lower()
        for forbidden in ("videoencode", "videodecode", "videoprocessing"):
            self.assertNotIn(
                forbidden, haystack,
                "occupancy.py must not name engine %r in executable code; "
                "read lane.media_engines" % forbidden,
            )


if __name__ == "__main__":
    unittest.main()
