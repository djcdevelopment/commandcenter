"""All-or-none multi-variant promotion.

    A successful render job promotes the COMPLETE requested variant set.
    A failed render job promotes NONE of that attempt's variants.

This suite exists because real hardware produced the violation: on a busy
gameplay clip, horizontal exceeded its bitrate guardrail while vertical
validated and promoted, leaving the drafts tree holding two different revisions
of one clip. The job correctly read `failed`, and the filesystem still ended up
partially updated.

`os.replace` is atomic per file and NOT across files, so the tests also cover
what happens when the second filesystem operation fails after the first
succeeded -- the case that would otherwise leave horizontal at N+1 and vertical
at N.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.coordination import CapacityLeaseStore
from hearth.media import revision as rev
from hearth.toolsurface import _media_scope as media

SESSION = "20260825T023859Z-c83a0e3b"
CLIP = SESSION + "-27275f15365a"


class PromotionSetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.leases = CapacityLeaseStore(Path(self._temp.name) / "coordination.sqlite")
        self.drafts = self.root / "drafts" / SESSION
        self.drafts.mkdir(parents=True, exist_ok=True)
        self.work = self.root / "work" / SESSION / "render" / "job_new"
        self.work.mkdir(parents=True, exist_ok=True)
        rev.write_authority(SESSION, CLIP, 2, "2026-08-25T08:00:00Z")

    # ----------------------------------------------------------- fixtures

    def _existing_drafts(self) -> dict:
        """A complete previous draft set, at revision 1."""
        paths = {}
        for variant in ("horizontal", "vertical"):
            path = self.drafts / ("%s-%s.mp4" % (CLIP, variant))
            path.write_text("old-%s-rev1" % variant, encoding="utf-8")
            paths[variant] = path
        return paths

    def _staged(self, variants=("horizontal", "vertical")) -> dict:
        staged = {}
        for variant in variants:
            source = self.work / ("%s-%s.mp4.part" % (CLIP, variant))
            source.write_text("new-%s-rev2" % variant, encoding="utf-8")
            staged[variant] = (source, self.drafts / ("%s-%s.mp4" % (CLIP, variant)))
        return staged

    def _promote(self, staged, *, revision=2, replace=None, job_id="job_new"):
        return rev.promote_set(
            leases=self.leases, session_id=SESSION, clip_id=CLIP,
            job_revision=revision, job_id=job_id,
            invocation_id="inv", staged=staged, replace=replace,
        )

    # ------------------------------------------------------------- happy

    def test_promotes_the_complete_set(self) -> None:
        previous = self._existing_drafts()
        outcome = self._promote(self._staged())
        self.assertTrue(outcome.promoted)
        for variant, path in previous.items():
            self.assertEqual("new-%s-rev2" % variant, path.read_text(encoding="utf-8"))

    def test_leaves_no_rollback_files_behind_on_success(self) -> None:
        self._existing_drafts()
        self._promote(self._staged())
        self.assertEqual([], list(self.drafts.glob("*" + rev.ROLLBACK_SUFFIX + "*")))

    def test_promotes_into_an_empty_drafts_dir(self) -> None:
        outcome = self._promote(self._staged())
        self.assertTrue(outcome.promoted)
        self.assertEqual(2, len(list(self.drafts.glob("*.mp4"))))

    # --------------------------------------------- superseded / authority

    def test_a_superseded_set_promotes_nothing(self) -> None:
        previous = self._existing_drafts()
        staged = self._staged()
        rev.write_authority(SESSION, CLIP, 3, "2026-08-25T08:05:00Z")  # moved on
        outcome = self._promote(staged, revision=2)
        self.assertFalse(outcome.promoted)
        self.assertEqual(rev.REASON_SUPERSEDED, outcome.reason)
        for variant, path in previous.items():
            self.assertEqual("old-%s-rev1" % variant, path.read_text(encoding="utf-8"))
        for source, _dest in staged.values():
            self.assertFalse(source.exists(), "stale staging must be discarded")

    def test_unreadable_authority_promotes_nothing(self) -> None:
        previous = self._existing_drafts()
        (self.root / "work" / SESSION / ("%s.revision.json" % CLIP)).write_text(
            "{corrupt", encoding="utf-8")
        outcome = self._promote(self._staged())
        self.assertFalse(outcome.promoted)
        self.assertEqual(rev.REASON_AUTHORITY_UNAVAILABLE, outcome.reason)
        for variant, path in previous.items():
            self.assertEqual("old-%s-rev1" % variant, path.read_text(encoding="utf-8"))

    # ------------------------------- THE INJECTED SECOND-PROMOTION FAILURE

    def test_failure_on_the_second_replace_restores_the_previous_set(self) -> None:
        """The case os.replace's per-file atomicity cannot cover.

        Without rollback this leaves horizontal at revision 2 and vertical at
        revision 1 -- a mixed-revision pair that nothing downstream understands.
        """
        previous = self._existing_drafts()
        staged = self._staged()
        calls = {"n": 0}

        def flaky(src, dst):
            # Displacing both old files is 2 calls; installing is calls 3 and 4.
            calls["n"] += 1
            if calls["n"] == 4:
                raise OSError("injected failure installing the second variant")
            return os.replace(src, dst)

        outcome = self._promote(staged, replace=flaky)

        self.assertFalse(outcome.promoted)
        self.assertEqual(rev.REASON_PROMOTION_FAILED, outcome.reason)
        self.assertIn("restored", outcome.detail)
        # The PREVIOUS complete set is back, byte for byte.
        for variant, path in previous.items():
            self.assertTrue(path.exists(), "%s must be restored" % variant)
            self.assertEqual("old-%s-rev1" % variant, path.read_text(encoding="utf-8"))
        # No mixed-revision pair, and no rollback litter.
        self.assertEqual([], list(self.drafts.glob("*" + rev.ROLLBACK_SUFFIX + "*")))
        self.assertEqual(2, len(list(self.drafts.glob("*.mp4"))))

    def test_failure_while_displacing_restores_and_promotes_nothing(self) -> None:
        previous = self._existing_drafts()
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("injected failure displacing the second variant")
            return os.replace(src, dst)

        outcome = self._promote(self._staged(), replace=flaky)
        self.assertFalse(outcome.promoted)
        for variant, path in previous.items():
            self.assertEqual("old-%s-rev1" % variant, path.read_text(encoding="utf-8"))

    # ------------------------------------------------------- recovery

    def test_recovery_restores_a_promotion_that_never_finished(self) -> None:
        # Simulate process death after displacing but before installing.
        previous = self._existing_drafts()
        for variant, path in previous.items():
            os.replace(path, rev.rollback_path(path, "job_dead"))
        for path in previous.values():
            self.assertFalse(path.exists())

        restored = rev.recover_incomplete_promotions(self.drafts)

        self.assertEqual(2, len(restored))
        for variant, path in previous.items():
            self.assertTrue(path.exists())
            self.assertEqual("old-%s-rev1" % variant, path.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.drafts.glob("*" + rev.ROLLBACK_SUFFIX + "*")))

    def test_recovery_is_a_no_op_when_nothing_was_interrupted(self) -> None:
        self._existing_drafts()
        self.assertEqual([], rev.recover_incomplete_promotions(self.drafts))

    def test_promotion_is_still_serialised_per_clip(self) -> None:
        from hearth.execution import CapacityUnavailable

        held = self.leases.acquire(scope=rev.promotion_scope(CLIP), job_id="other",
                                   invocation_id="inv", limit=1, ttl_seconds=30)
        try:
            with self.assertRaises(CapacityUnavailable):
                self._promote(self._staged())
        finally:
            self.leases.release(held)


class RenderJobAllOrNoneTest(unittest.TestCase):
    """The end-to-end shape of the failure real hardware produced."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / SESSION).mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / SESSION / "seg.mkv").write_bytes(b"x")
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.leases = CapacityLeaseStore(Path(self._temp.name) / "coordination.sqlite")
        self.drafts = self.root / "drafts" / SESSION
        self.drafts.mkdir(parents=True, exist_ok=True)
        rev.write_authority(SESSION, CLIP, 1, "2026-08-25T08:00:00Z")
        self.previous = {}
        for variant in ("horizontal", "vertical"):
            path = self.drafts / ("%s-%s.mp4" % (CLIP, variant))
            path.write_text("previous-%s" % variant, encoding="utf-8")
            self.previous[variant] = path

    def _run(self, failing_variant):
        """Drive render_clip with encoding stubbed, failing one variant."""
        from hearth.media import render as render_mod
        from hearth.media.jobspec import parse_render_arguments

        spec = parse_render_arguments({
            "session_id": SESSION, "clip_id": CLIP, "clip_revision": 1,
            "source_segments": ["raw/%s/seg.mkv" % SESSION],
            "start_seconds": 0.0, "end_seconds": 10.0,
            "variants": ["horizontal", "vertical"],
            "profile_version": "bf6-qsv-v1",
        })

        def fake_encode(*, variant, work_dir, drafts_dir, **kwargs):
            staged = Path(work_dir) / ("%s-%s.mp4.part" % (CLIP, variant))
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text("new-%s" % variant, encoding="utf-8")
            destination = Path(drafts_dir) / ("%s-%s.mp4" % (CLIP, variant))
            ok = variant != failing_variant
            validation = {
                "ok": ok,
                "failures": ([] if ok else [
                    "bitrate 111.54 Mbps exceeds the 95.00 Mbps guardrail for this "
                    "variant; ICQ does not cap rate, so this is enforced here"]),
                "measured": {"bitrate_mbps": 111.54 if not ok else 12.11,
                             "size_bytes": 1234},
            }
            return render_mod.VariantResult(
                variant, False, "validated" if ok else "validation_failed",
                validation=validation, staged=staged, destination=destination,
                valid=ok)

        lane = type("L", (), {"lane_id": "b70@bus4", "child_device": 3})()
        with patch.object(render_mod, "_encode_variant", side_effect=fake_encode), \
             patch.object(render_mod.validate_mod, "source_dimensions",
                          return_value=(3840, 2160, 3)):
            return render_mod.render_clip(
                spec=spec, lane=lane, job_id="job_partial",
                ffmpeg="ffmpeg", ffprobe="ffprobe", leases=self.leases)

    def _assert_nothing_promoted(self, receipt):
        self.assertFalse(receipt.ok)
        for variant, path in self.previous.items():
            self.assertEqual("previous-%s" % variant,
                             path.read_text(encoding="utf-8"),
                             "%s draft must be byte-identical" % variant)
        self.assertEqual([], list(self.drafts.glob("*.part")),
                         "no staging file may masquerade as a draft")
        self.assertEqual([], list(self.drafts.glob("*" + rev.ROLLBACK_SUFFIX + "*")))
        for result in receipt.variants:
            self.assertFalse(result.promoted)

    def test_horizontal_fails_validation_so_vertical_is_withheld(self) -> None:
        # The exact case hardware produced.
        receipt = self._run("horizontal")
        self._assert_nothing_promoted(receipt)
        by_variant = {v.variant: v for v in receipt.variants}
        self.assertEqual("validation_failed", by_variant["horizontal"].reason)
        self.assertEqual("withheld_sibling_failed", by_variant["vertical"].reason)
        # The receipt names the failing variant, its measured bitrate, and the guard.
        failure = by_variant["horizontal"].validation["failures"][0]
        self.assertIn("111.54", failure)
        self.assertIn("95.00", failure)
        self.assertEqual(111.54,
                         by_variant["horizontal"].validation["measured"]["bitrate_mbps"])

    def test_the_inverse_also_promotes_nothing(self) -> None:
        receipt = self._run("vertical")
        self._assert_nothing_promoted(receipt)
        by_variant = {v.variant: v for v in receipt.variants}
        self.assertEqual("validation_failed", by_variant["vertical"].reason)
        self.assertEqual("withheld_sibling_failed", by_variant["horizontal"].reason)

    def test_both_valid_promotes_the_complete_set(self) -> None:
        receipt = self._run(failing_variant=None)
        self.assertTrue(receipt.ok)
        for variant, path in self.previous.items():
            self.assertEqual("new-%s" % variant, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
