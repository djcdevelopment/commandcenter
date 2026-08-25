"""Revision-authority tests -- the proof that a stale render cannot overwrite a newer draft.

The headline case is `test_late_stale_render_is_refused_promotion`, which walks
the exact scenario the design has to survive: a revision-N render that loses its
cancellation, finishes late, passes validation, and must still be refused.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution import CapacityLeaseStore
from hearth.media import revision as rev
from hearth.toolsurface import _media_scope as media

SESSION = "20260825T023859Z-c83a0e3b"
CLIP = "20260825T023859Z-c83a0e3b-27275f15365a"


class RevisionAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.leases = CapacityLeaseStore(Path(self._temp.name) / "coordination.sqlite")

    # ------------------------------------------------------------- helpers

    def _set_authority(self, revision: int) -> None:
        rev.write_authority(SESSION, CLIP, revision, "2026-08-25T05:00:00Z")

    def _stage(self, name: str = "staged.mp4", content: str = "new") -> Path:
        staged = self.root / "work" / SESSION / "render" / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(content, encoding="utf-8")
        return staged

    def _destination(self) -> Path:
        return self.root / "drafts" / SESSION / ("%s-horizontal.mp4" % CLIP)

    def _promote(self, job_revision: int, staged: Path, job_id: str = "job_a") -> rev.PromotionOutcome:
        return rev.promote_if_current(
            leases=self.leases,
            session_id=SESSION,
            clip_id=CLIP,
            job_revision=job_revision,
            job_id=job_id,
            invocation_id="inv_" + job_id,
            staged=staged,
            destination=self._destination(),
        )

    # --------------------------------------------------------- reading

    def test_reads_the_authoritative_revision(self) -> None:
        self._set_authority(3)
        self.assertEqual(3, rev.read_authority(SESSION, CLIP))

    def test_missing_authority_raises(self) -> None:
        with self.assertRaises(rev.AuthorityUnavailable):
            rev.read_authority(SESSION, CLIP)

    def test_malformed_authority_raises(self) -> None:
        path = self.root / "work" / SESSION / ("%s.revision.json" % CLIP)
        path.parent.mkdir(parents=True, exist_ok=True)
        for bad in (
            "{not json",
            json.dumps([1, 2, 3]),
            json.dumps({"schema_version": 99, "clip_id": CLIP, "clip_revision": 1}),
            json.dumps({"schema_version": 1, "clip_id": "other", "clip_revision": 1}),
            json.dumps({"schema_version": 1, "clip_id": CLIP, "clip_revision": "two"}),
            json.dumps({"schema_version": 1, "clip_id": CLIP, "clip_revision": -1}),
            json.dumps({"schema_version": 1, "clip_id": CLIP, "clip_revision": True}),
        ):
            path.write_text(bad, encoding="utf-8")
            with self.assertRaises(rev.AuthorityUnavailable, msg=bad):
                rev.read_authority(SESSION, CLIP)

    def test_authority_write_is_atomic(self) -> None:
        self._set_authority(1)
        directory = self.root / "work" / SESSION
        self.assertEqual([], list(directory.glob("*.tmp")))

    # ------------------------------------------------------- promotion

    def test_current_revision_is_promoted(self) -> None:
        self._set_authority(2)
        staged = self._stage()
        outcome = self._promote(2, staged)
        self.assertTrue(outcome.promoted)
        self.assertEqual(rev.REASON_PROMOTED, outcome.reason)
        self.assertTrue(self._destination().exists())
        self.assertFalse(staged.exists(), "staged file should have been moved")

    def test_missing_authority_fails_closed_and_preserves_existing_draft(self) -> None:
        destination = self._destination()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("existing draft", encoding="utf-8")
        staged = self._stage(content="would-be-new")

        outcome = self._promote(1, staged)  # no authority written at all

        self.assertFalse(outcome.promoted)
        self.assertEqual(rev.REASON_AUTHORITY_UNAVAILABLE, outcome.reason)
        self.assertIsNone(outcome.authoritative_revision)
        self.assertEqual("existing draft", destination.read_text(encoding="utf-8"))
        self.assertFalse(staged.exists(), "staged file should be discarded")

    def test_unreadable_authority_fails_closed(self) -> None:
        path = self.root / "work" / SESSION / ("%s.revision.json" % CLIP)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{corrupt", encoding="utf-8")
        outcome = self._promote(1, self._stage())
        self.assertFalse(outcome.promoted)
        self.assertEqual(rev.REASON_AUTHORITY_UNAVAILABLE, outcome.reason)
        self.assertFalse(self._destination().exists())

    # ----------------------------------------- THE BLOCKER SCENARIO

    def test_late_stale_render_is_refused_promotion(self) -> None:
        """revision N renders -> AM4 advances to N+1 -> N finishes late -> N refused.

        Cancellation is deliberately never delivered: correctness must not
        depend on it, because a running ffmpeg cannot reliably be stopped.
        """
        # 1. revision 1 begins rendering (authority says 1).
        self._set_authority(1)
        staged_old = self._stage("old.mp4", content="revision-1-output")

        # 2. AM4 extends the clip: authority advances to 2, a new job is created.
        #    No cancellation is delivered to the revision-1 job.
        self._set_authority(2)
        staged_new = self._stage("new.mp4", content="revision-2-output")

        # 3. revision 2 completes first and promotes.
        outcome_new = self._promote(2, staged_new, job_id="job_rev2")
        self.assertTrue(outcome_new.promoted)
        self.assertEqual("revision-2-output", self._destination().read_text(encoding="utf-8"))

        # 4. revision 1 finishes LATE. Its output is perfectly valid -- validation
        #    already passed -- but it is stale.
        outcome_old = self._promote(1, staged_old, job_id="job_rev1")

        self.assertFalse(outcome_old.promoted, "a stale render must never be promoted")
        self.assertEqual(rev.REASON_SUPERSEDED, outcome_old.reason)
        self.assertEqual(1, outcome_old.job_revision)
        self.assertEqual(2, outcome_old.authoritative_revision)

        # 5. The newer draft is the only one on disk, untouched.
        self.assertEqual("revision-2-output", self._destination().read_text(encoding="utf-8"))
        self.assertFalse(staged_old.exists(), "stale staged output should be discarded")

    def test_stale_render_finishing_before_the_new_one_still_refuses(self) -> None:
        # Ordering independence: the check is against authority, not arrival.
        self._set_authority(1)
        staged_old = self._stage("old.mp4", content="revision-1-output")
        self._set_authority(2)

        outcome_old = self._promote(1, staged_old, job_id="job_rev1")
        self.assertFalse(outcome_old.promoted)
        self.assertFalse(self._destination().exists(), "nothing should have been published")

        staged_new = self._stage("new.mp4", content="revision-2-output")
        self.assertTrue(self._promote(2, staged_new, job_id="job_rev2").promoted)
        self.assertEqual("revision-2-output", self._destination().read_text(encoding="utf-8"))

    # ------------------------------------------------------ serialisation

    def test_promotion_is_serialised_per_clip(self) -> None:
        # Two jobs for one clip must not interleave read-authority and replace.
        self._set_authority(1)
        scope = rev.promotion_scope(CLIP)
        held = self.leases.acquire(
            scope=scope, job_id="other", invocation_id="inv", limit=1, ttl_seconds=30
        )
        try:
            self.assertEqual(1, self.leases.active_count(scope))
            from hearth.execution import CapacityUnavailable

            with self.assertRaises(CapacityUnavailable):
                self._promote(1, self._stage())
        finally:
            self.leases.release(held)

    def test_lease_is_released_even_when_promotion_is_refused(self) -> None:
        # A refusal that leaked its lease would deadlock every later revision.
        scope = rev.promotion_scope(CLIP)
        self._promote(1, self._stage())  # no authority -> refused
        self.assertEqual(0, self.leases.active_count(scope))

    def test_different_clips_do_not_block_each_other(self) -> None:
        other_clip = "20260825T023859Z-c83a0e3b-aaaaaaaaaaaa"
        held = self.leases.acquire(
            scope=rev.promotion_scope(other_clip),
            job_id="x", invocation_id="y", limit=1, ttl_seconds=30,
        )
        try:
            self._set_authority(1)
            self.assertTrue(self._promote(1, self._stage()).promoted)
        finally:
            self.leases.release(held)

    def test_concurrent_promotions_yield_one_winner_and_no_torn_draft(self) -> None:
        self._set_authority(5)
        results = []
        errors = []

        def attempt(index: int) -> None:
            staged = self._stage("c%d.mp4" % index, content="payload-%d" % index)
            try:
                results.append(self._promote(5, staged, job_id="job_%d" % index))
            except Exception as exc:  # lease contention is an expected outcome
                errors.append(exc)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        promoted = [outcome for outcome in results if outcome.promoted]
        self.assertGreaterEqual(len(promoted) + len(errors), 1)
        # Whatever the interleaving, the draft is exactly one intact payload.
        self.assertTrue(self._destination().exists())
        self.assertRegex(self._destination().read_text(encoding="utf-8"), r"^payload-\d$")
        self.assertEqual(0, self.leases.active_count(rev.promotion_scope(CLIP)))


if __name__ == "__main__":
    unittest.main()
