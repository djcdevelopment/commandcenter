"""Render argument validation.

A render request names media paths and writes into the drafts tree, so this is
the boundary where a malformed or hostile request has to die. The tests are
mostly refusals for that reason.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.media import jobspec
from hearth.toolsurface import _media_scope as media

SESSION = "20260825T023859Z-c83a0e3b"
CLIP = SESSION + "-27275f15365a"
SEGMENT = "raw/%s/2026-08-24_19-38-59.mkv" % SESSION


class JobSpecTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / SESSION).mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / SESSION / "2026-08-24_19-38-59.mkv").write_bytes(b"x")
        (self.root / "work" / SESSION).mkdir(parents=True, exist_ok=True)
        (self.root / "work" / SESSION / "c.srt").write_text("1\n", encoding="utf-8")
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, **overrides) -> dict:
        base = {
            "session_id": SESSION, "clip_id": CLIP, "clip_revision": 0,
            "source_segments": [SEGMENT], "start_seconds": 36.0,
            "end_seconds": 96.0, "variants": ["horizontal", "vertical"],
            "profile_version": "bf6-qsv-v1",
        }
        base.update(overrides)
        return base

    # ------------------------------------------------------------- accepted

    def test_accepts_a_well_formed_request(self) -> None:
        spec = jobspec.parse_render_arguments(self.args())
        self.assertEqual(CLIP, spec.clip_id)
        self.assertEqual(60.0, spec.duration_seconds)
        self.assertEqual(("horizontal", "vertical"), spec.variants)

    def test_deduplicates_variants_preserving_order(self) -> None:
        spec = jobspec.parse_render_arguments(
            self.args(variants=["vertical", "horizontal", "vertical"]))
        self.assertEqual(("vertical", "horizontal"), spec.variants)

    # ---------------------------------------------------------- identifiers

    def test_rejects_a_session_id_that_breaks_the_contract(self) -> None:
        for bad in ("nope", "20260825T023859Z", "20260825T023859Z-XYZ",
                    "20260825t023859z-c83a0e3b"):
            with self.assertRaises(jobspec.RenderArgumentError, msg=bad):
                jobspec.parse_render_arguments(self.args(session_id=bad))

    def test_rejects_a_clip_id_that_is_not_derived_from_the_session(self) -> None:
        other = "20260825T014355Z-b34f0405-27275f15365a"
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(clip_id=other))

    def test_rejects_a_clip_id_with_a_bad_digest(self) -> None:
        for bad in (SESSION + "-short", SESSION + "-ZZZZZZZZZZZZ", SESSION):
            with self.assertRaises(jobspec.RenderArgumentError, msg=bad):
                jobspec.parse_render_arguments(self.args(clip_id=bad))

    def test_rejects_a_negative_or_non_integer_revision(self) -> None:
        for bad in (-1, 1.5, "2", True, None):
            with self.assertRaises(jobspec.RenderArgumentError, msg=repr(bad)):
                jobspec.parse_render_arguments(self.args(clip_revision=bad))

    # ---------------------------------------------------------------- paths

    def test_rejects_a_segment_outside_the_media_root(self) -> None:
        for bad in (r"C:\Windows\System32\cmd.exe", "raw/../../etc/passwd",
                    r"\\server\share\x.mkv"):
            with self.assertRaises(jobspec.RenderArgumentError, msg=bad):
                jobspec.parse_render_arguments(self.args(source_segments=[bad]))

    def test_rejects_a_segment_outside_the_raw_subtree(self) -> None:
        # Sources are raw footage. Rendering from drafts/ would compound losses
        # and, worse, let a request point the renderer at its own output.
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(
                self.args(source_segments=["drafts/%s/x.mp4" % SESSION]))

    def test_rejects_an_empty_or_oversized_segment_list(self) -> None:
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(source_segments=[]))
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(
                self.args(source_segments=[SEGMENT] * 20))

    def test_captions_path_must_live_under_work(self) -> None:
        spec = jobspec.parse_render_arguments(
            self.args(captions_path="work/%s/c.srt" % SESSION))
        self.assertEqual("work/%s/c.srt" % SESSION, spec.captions_path)
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(
                self.args(captions_path="raw/%s/c.srt" % SESSION))

    def test_refuses_two_sources_of_caption_truth(self) -> None:
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(
                self.args(captions="1\n", captions_path="work/%s/c.srt" % SESSION))

    # -------------------------------------------------------------- timings

    def test_rejects_a_non_positive_or_inverted_window(self) -> None:
        for start, end in ((36.0, 36.0), (96.0, 36.0), (-1.0, 10.0)):
            with self.assertRaises(jobspec.RenderArgumentError):
                jobspec.parse_render_arguments(
                    self.args(start_seconds=start, end_seconds=end))

    def test_rejects_a_clip_longer_than_the_pipeline_cap(self) -> None:
        # The AM4 pipeline caps clips at 180 s; a longer request means the two
        # sides have drifted, which is worth failing on rather than rendering.
        with self.assertRaises(jobspec.RenderArgumentError) as caught:
            jobspec.parse_render_arguments(
                self.args(start_seconds=0.0, end_seconds=200.0))
        self.assertIn("180", str(caught.exception))

    def test_rejects_non_finite_times(self) -> None:
        for bad in (float("inf"), float("nan")):
            with self.assertRaises(jobspec.RenderArgumentError):
                jobspec.parse_render_arguments(self.args(end_seconds=bad))

    # ------------------------------------------------------------- profiles

    def test_rejects_an_unknown_profile_version(self) -> None:
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(profile_version="nope-v9"))

    def test_rejects_the_reference_only_profile(self) -> None:
        # bf6-nvenc-v1 documents the AM4 contract and must never be dispatched.
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(profile_version="bf6-nvenc-v1"))

    def test_rejects_unknown_variants_and_empty_lists(self) -> None:
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(variants=["square"]))
        with self.assertRaises(jobspec.RenderArgumentError):
            jobspec.parse_render_arguments(self.args(variants=[]))

    def test_rejects_unknown_keys(self) -> None:
        with self.assertRaises(jobspec.RenderArgumentError) as caught:
            jobspec.parse_render_arguments(self.args(ffmpeg_args="-vf evil"))
        self.assertIn("ffmpeg_args", str(caught.exception))

    # ---------------------------------------------------------- idempotency

    def test_key_is_stable_and_order_independent(self) -> None:
        a = jobspec.parse_render_arguments(
            self.args(variants=["horizontal", "vertical"])).idempotency_key()
        b = jobspec.parse_render_arguments(
            self.args(variants=["vertical", "horizontal"])).idempotency_key()
        self.assertEqual(a, b)

    def test_key_changes_with_revision_profile_and_variants(self) -> None:
        base = jobspec.parse_render_arguments(self.args()).idempotency_key()
        self.assertNotEqual(base, jobspec.parse_render_arguments(
            self.args(clip_revision=1)).idempotency_key())
        self.assertNotEqual(base, jobspec.parse_render_arguments(
            self.args(variants=["horizontal"])).idempotency_key())

    def test_key_does_not_change_with_timings(self) -> None:
        # Deliberate: an in/out tweak is a new REVISION, and the revision is
        # what the key tracks. If times alone changed the key, an edit would
        # render twice without ever bumping the authority record.
        base = jobspec.parse_render_arguments(self.args()).idempotency_key()
        moved = jobspec.parse_render_arguments(
            self.args(start_seconds=40.0, end_seconds=100.0)).idempotency_key()
        self.assertEqual(base, moved)


if __name__ == "__main__":
    unittest.main()
