"""Render-profile tests.

The assertions that matter here are the ones guarding things that were WRONG at
some point during development and produced plausible-looking output anyway:

* an audio chain that no longer matches the AM4 renderer byte-for-byte;
* `-b:v 0` or `-maxrate` sneaking back into an ICQ command, which silently
  disables global_quality and pins every quality setting to the same bitrate;
* a QSV command without an explicit child_device, which quietly encodes on the
  integrated GPU;
* seeking that lands on a different frame than the software decoder;
* a vertical bed scaled 16:9 straight into 9:16, which squashes the picture.

Every one of those failures still produces a video file that plays.
"""
from __future__ import annotations

import unittest

from hearth.media import profiles as P

# The exact string the AM4 renderer builds for a 3-track source.
AM4_AUDIO = (
    "[0:a:0]volume=1.0[a0];[0:a:1]volume=1.35[a1];[0:a:2]volume=1.25[a2];"
    "[a0][a1][a2]amix=inputs=3:normalize=0:dropout_transition=2,"
    "loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[mix]"
)

FF = "ffmpeg"
SRC = "E:/BF6-Highlights/raw/s/seg.mkv"


class AudioContractTest(unittest.TestCase):
    def test_matches_the_am4_renderer_verbatim(self) -> None:
        chain, label = P.mix_filter(3)
        self.assertEqual(AM4_AUDIO, chain)
        self.assertEqual("[mix]", label)

    def test_normalize_zero_is_present(self) -> None:
        # Without it amix applies 1/N attenuation and the explicit per-track
        # gains stop meaning what they say.
        self.assertIn("normalize=0", P.mix_filter(3)[0])

    def test_no_audio_yields_no_filter(self) -> None:
        self.assertEqual(("", ""), P.mix_filter(0))

    def test_track_count_is_capped_at_three(self) -> None:
        chain, _ = P.mix_filter(5)
        self.assertIn("amix=inputs=3", chain)
        self.assertNotIn("[0:a:3]", chain)

    def test_trim_is_applied_to_every_track_on_the_same_boundary(self) -> None:
        # Audio trimmed on a different boundary than video drifts against it.
        chain, _ = P.mix_filter(3, trim=(36.0, 20.0))
        self.assertEqual(3, chain.count("atrim=start=36.000:duration=20.000"))
        self.assertEqual(3, chain.count("asetpts=PTS-STARTPTS"))
        # Gains and loudness survive the trim unchanged.
        for gain in ("volume=1.0", "volume=1.35", "volume=1.25"):
            self.assertIn(gain, chain)
        self.assertIn("loudnorm=I=-14:TP=-1:LRA=11", chain)


class RateControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = P.get_profile("bf6-qsv-v1")

    def test_icq_emits_global_quality(self) -> None:
        args = P._encoder_args(self.profile.variant("horizontal"))
        self.assertIn("-global_quality", args)

    def test_icq_never_emits_the_flags_that_disable_it(self) -> None:
        # MEASURED: either of these drops h264_qsv into VBR, after which
        # global_quality is ignored and a gq 14..22 sweep produced
        # byte-identical output at 18.69 Mbps.
        for variant in ("horizontal", "vertical"):
            args = P._encoder_args(self.profile.variant(variant))
            self.assertNotIn("-b:v", args, variant)
            self.assertNotIn("-maxrate", args, variant)
            self.assertNotIn("-bufsize", args, variant)

    def test_icq_without_global_quality_is_refused(self) -> None:
        spec = self.profile.variant("horizontal")
        broken = P.VariantSpec(spec.encoder, spec.args, None, None, None, None, None,
                               None, False, True, None, None, "icq", None)
        with self.assertRaises(P.ProfileError):
            P._encoder_args(broken)

    def test_size_guard_is_carried_as_a_validation_value(self) -> None:
        # The ceiling cannot be an encoder setting (maxrate does not cap in
        # ICQ: gq=18 with -maxrate 85M still emitted 87.96 Mbps), so it must
        # survive as data for the validator.
        self.assertEqual(95, self.profile.variant("horizontal").max_bitrate_mbps)
        self.assertEqual(30, self.profile.variant("vertical").max_bitrate_mbps)

    def test_both_variants_are_marked_calibrated(self) -> None:
        for variant in ("horizontal", "vertical"):
            self.assertTrue(self.profile.variant(variant).calibrated, variant)


class QsvCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = P.get_profile("bf6-qsv-v1")

    def _cmd(self, variant, **kw):
        params = dict(profile=self.profile, variant=variant, ffmpeg=FF, inputs=[SRC],
                      start_seconds=36.0, duration_seconds=20.0, output="out.mp4",
                      audio_streams=3, child_device=3, progress=False)
        params.update(kw)
        return P.build_command(**params)

    def test_refuses_to_run_without_an_explicit_child_device(self) -> None:
        # qsv:hw_any selects DXGI adapter 0, which on this box is the iGPU.
        with self.assertRaises(P.ProfileError) as caught:
            self._cmd("horizontal", child_device=None)
        self.assertIn("iGPU", str(caught.exception))

    def test_pins_the_requested_adapter(self) -> None:
        joined = " ".join(self._cmd("horizontal"))
        self.assertIn("child_device=3", joined)
        self.assertIn("child_device_type=d3d11va", joined)

    def test_uses_preroll_seek_with_copyts_and_start_at_zero(self) -> None:
        cmd = self._cmd("horizontal")
        self.assertIn("-copyts", cmd)
        self.assertIn("-start_at_zero", cmd)
        # Seeks to start - preroll, not to start.
        self.assertIn("%.3f" % (36.0 - P.QSV_PREROLL_SECONDS), cmd)
        # And never re-introduces the flag that shifted output by two frames.
        self.assertNotIn("-avoid_negative_ts", cmd)

    def test_preroll_clamps_at_zero_for_an_early_clip(self) -> None:
        cmd = self._cmd("horizontal", start_seconds=3.0)
        self.assertIn("0.000", cmd)

    def test_trims_video_to_the_exact_in_point(self) -> None:
        joined = " ".join(self._cmd("horizontal"))
        self.assertIn("trim=start=36.000:duration=20.000", joined)
        self.assertIn("setpts=PTS-STARTPTS", joined)

    def test_horizontal_never_scales(self) -> None:
        # The 4K master passes through; output is 3840x2160 because the source
        # is, not because anything resizes it.
        joined = " ".join(self._cmd("horizontal"))
        self.assertNotIn("vpp_qsv=w=", joined)
        self.assertNotIn("scale=", joined)

    def test_vertical_crops_to_aspect_before_scaling(self) -> None:
        # Scaling 16:9 straight into 9:16 squashes the picture. 3840x2160 must
        # be cropped to 1215x2160 (9:16) first.
        joined = " ".join(self._cmd("vertical"))
        self.assertIn("cw=1215:ch=2160", joined)

    def test_vertical_blur_is_staged_not_a_single_jump(self) -> None:
        # A single 68x120 -> 1080x1920 jump is a hard mosaic, not a blur.
        joined = " ".join(self._cmd("vertical"))
        self.assertIn("w=68:h=120", joined)
        self.assertIn("w=270:h=480", joined)
        self.assertIn("w=1080:h=1920", joined)

    def test_vertical_burns_captions_when_given_and_stays_on_gpu_otherwise(self) -> None:
        with_srt = " ".join(self._cmd("vertical", srt_path="E:/x/c.srt"))
        self.assertIn("subtitles=", with_srt)
        self.assertIn("hwdownload", with_srt)
        # hwupload back into QSV fails on this driver; the encoder uploads.
        self.assertNotIn("hwupload", with_srt)

        without = " ".join(self._cmd("vertical", srt_path=None))
        self.assertNotIn("subtitles=", without)
        self.assertNotIn("hwdownload", without)
        # The chain still terminates in [v] so -map [v] always resolves.
        self.assertIn("[composite]null[v]", without)

    def test_vertical_forces_sixty_fps(self) -> None:
        cmd = self._cmd("vertical")
        self.assertIn("-r", cmd)
        self.assertIn("60", cmd)

    def test_audio_contract_reaches_the_command(self) -> None:
        joined = " ".join(self._cmd("horizontal"))
        for fragment in ("volume=1.35", "normalize=0", "loudnorm=I=-14:TP=-1:LRA=11",
                         "aresample=48000"):
            self.assertIn(fragment, joined)
        self.assertIn("-b:a", joined)
        self.assertIn("320k", joined)

    def test_silent_source_disables_audio_rather_than_mapping_nothing(self) -> None:
        cmd = self._cmd("horizontal", audio_streams=0)
        self.assertIn("-an", cmd)

    def test_multi_segment_requires_a_concat_list(self) -> None:
        with self.assertRaises(P.ProfileError):
            self._cmd("horizontal", inputs=[SRC, SRC])
        joined = " ".join(self._cmd("horizontal", inputs=[SRC, SRC],
                                    concat_path="E:/x/c.ffconcat"))
        self.assertIn("-f concat", joined)
        self.assertIn("-safe 0", joined)

    def test_faststart_is_always_set(self) -> None:
        self.assertIn("+faststart", " ".join(self._cmd("horizontal")))

    def test_unknown_variant_is_refused(self) -> None:
        with self.assertRaises(P.ProfileError):
            self._cmd("square")


class ProfileRegistryTest(unittest.TestCase):
    def test_unknown_version_is_refused_not_defaulted(self) -> None:
        with self.assertRaises(P.ProfileError):
            P.get_profile("bf6-does-not-exist")

    def test_reference_only_profile_cannot_be_dispatched(self) -> None:
        # bf6-nvenc-v1 documents the AM4 contract; it must never be run here.
        with self.assertRaises(P.ProfileError):
            P.get_profile("bf6-nvenc-v1")

    def test_nvenc_reference_preserves_the_original_graph(self) -> None:
        profile = P.load_profiles()["bf6-nvenc-v1"]
        chain = P.nvenc_vertical_filter(profile.variant("vertical"), "E:/x/c.srt")
        self.assertIn("gblur=sigma=28", chain)
        self.assertIn("force_original_aspect_ratio=increase", chain)
        self.assertIn("crop=1080:1920", chain)

    def test_subtitle_style_is_unchanged(self) -> None:
        for fragment in ("FontName=DejaVu Sans", "FontSize=18", "BorderStyle=3",
                         "Outline=2", "MarginV=110"):
            self.assertIn(fragment, P.SUBTITLE_STYLE)

    def test_filter_path_escaping_handles_windows_paths(self) -> None:
        escaped = P.escape_filter_path(r"E:\BF6-Highlights\work\s\c.srt")
        self.assertIn(r"E\:", escaped)
        self.assertNotIn("\\\\", escaped)


if __name__ == "__main__":
    unittest.main()
