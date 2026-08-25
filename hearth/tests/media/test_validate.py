"""Post-encode validation, including the guardrail QSV cannot enforce itself.

`ffmpeg exited 0` proves almost nothing: a truncated file, a dropped audio
track, a silently rescaled frame and a wildly oversized output all exit 0. These
tests pin the checks that stand between a bad encode and a promoted draft.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.media import validate


def probe_payload(*, width=3840, height=2160, fps="60/1", duration="20.000000",
                  audio=True, codec="h264"):
    streams = [{
        "codec_type": "video", "codec_name": codec, "width": width,
        "height": height, "r_frame_rate": fps,
    }]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac",
                        "sample_rate": "48000", "channels": 2})
    return {"streams": streams, "format": {"duration": duration}}


class ValidateOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "out.mp4"
        # 20s at ~40 Mbps.
        self.path.write_bytes(b"\0" * 100_000_000)

    def check(self, payload=None, **kwargs):
        payload = payload if payload is not None else probe_payload()
        with patch.object(validate, "probe", return_value=payload):
            return validate.validate_output(self.path, **kwargs)

    def test_accepts_a_correct_output(self) -> None:
        result = self.check(expected_width=3840, expected_height=2160,
                            expected_duration_s=20.0, expected_fps=60.0)
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(3840, result.measured["width"])
        self.assertAlmostEqual(40.0, result.measured["bitrate_mbps"], places=1)

    def test_zero_byte_output_fails_before_probing(self) -> None:
        self.path.write_bytes(b"")
        result = validate.validate_output(self.path)
        self.assertFalse(result.ok)
        self.assertIn("zero bytes", result.failures[0])

    def test_missing_output_fails(self) -> None:
        result = validate.validate_output(Path(self._temp.name) / "absent.mp4")
        self.assertFalse(result.ok)

    def test_wrong_dimensions_fail(self) -> None:
        # A silently rescaled frame still plays; only an explicit check catches it.
        result = self.check(probe_payload(width=1920, height=1080),
                            expected_width=3840, expected_height=2160)
        self.assertFalse(result.ok)
        self.assertTrue(any("width" in f for f in result.failures))

    def test_wrong_frame_rate_fails(self) -> None:
        result = self.check(probe_payload(fps="30/1"), expected_fps=60.0)
        self.assertFalse(result.ok)

    def test_missing_audio_fails_when_the_source_had_audio(self) -> None:
        result = self.check(probe_payload(audio=False), require_audio=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("audio" in f for f in result.failures))

    def test_missing_audio_is_fine_for_a_silent_source(self) -> None:
        result = self.check(probe_payload(audio=False), require_audio=False)
        self.assertTrue(result.ok, result.failures)

    def test_truncated_duration_fails(self) -> None:
        result = self.check(probe_payload(duration="12.000000"),
                            expected_duration_s=20.0)
        self.assertFalse(result.ok)
        self.assertTrue(any("duration" in f for f in result.failures))

    def test_small_timebase_rounding_is_tolerated(self) -> None:
        result = self.check(probe_payload(duration="20.021000"),
                            expected_duration_s=20.0)
        self.assertTrue(result.ok, result.failures)

    def test_unreadable_duration_fails_rather_than_passing_silently(self) -> None:
        result = self.check(probe_payload(duration="not-a-number"),
                            expected_duration_s=20.0)
        self.assertFalse(result.ok)

    def test_probe_failure_is_never_treated_as_valid(self) -> None:
        with patch.object(validate, "probe",
                          side_effect=validate.ProbeError("ffprobe died")):
            result = validate.validate_output(self.path)
        self.assertFalse(result.ok)

    def test_all_failures_are_collected_not_short_circuited(self) -> None:
        result = self.check(probe_payload(width=1920, height=1080, fps="30/1",
                                          audio=False, duration="5.0"),
                            expected_width=3840, expected_height=2160,
                            expected_fps=60.0, expected_duration_s=20.0,
                            require_audio=True)
        self.assertFalse(result.ok)
        self.assertGreaterEqual(len(result.failures), 4)


class BitrateGuardrailTest(unittest.TestCase):
    """The ceiling QSV's ICQ mode cannot enforce.

    Measured: `-maxrate 85M` alongside global_quality=18 still emitted
    87.96 Mbps, and adding `-b:v 0` disabled ICQ entirely. So the ceiling is
    policy checked against the artifact, not an encoder setting.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "out.mp4"

    def _result(self, megabytes, *, guard):
        self.path.write_bytes(b"\0" * int(megabytes * 1_000_000))
        with patch.object(validate, "probe", return_value=probe_payload()):
            return validate.validate_output(self.path, max_bitrate_mbps=guard)

    def test_output_over_the_guardrail_fails_explicitly(self) -> None:
        # 250 MB over 20 s = 100 Mbps, against a 95 Mbps guard.
        result = self._result(250, guard=95)
        self.assertFalse(result.ok)
        self.assertTrue(any("guardrail" in f for f in result.failures))
        # The measured number is reported, not just the verdict.
        self.assertAlmostEqual(100.0, result.measured["bitrate_mbps"], places=1)

    def test_output_under_the_guardrail_passes(self) -> None:
        result = self._result(180, guard=95)
        self.assertTrue(result.ok, result.failures)

    def test_bitrate_is_always_recorded_even_without_a_guard(self) -> None:
        # The receipt records actual size/bitrate whether or not a limit applies.
        result = self._result(100, guard=None)
        self.assertTrue(result.ok)
        self.assertIn("bitrate_mbps", result.measured)
        self.assertIn("size_bytes", result.measured)

    def test_the_guard_is_never_silently_absorbed(self) -> None:
        # An over-ceiling output must NOT be quietly accepted, and must NOT be
        # quietly re-encoded at a different quality. It fails, loudly, with
        # numbers -- a retry policy would be a deliberate design decision.
        result = self._result(300, guard=95)
        self.assertFalse(result.ok)
        self.assertEqual(1, sum("guardrail" in f for f in result.failures))


if __name__ == "__main__":
    unittest.main()
