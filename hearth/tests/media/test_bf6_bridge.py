"""The BF6 sidecar bridge.

The property that matters is convergence: the bridge must reach the right state
from whatever is on disk, because a terminal job and a published result are
separate events and a crash can land between them.

Two independent mechanisms prevent a duplicate render -- the claim sidecar, and
the deterministic idempotency key. Both are exercised here, including the case
where the claim was lost.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.media import bf6_bridge as bridge
from hearth.toolsurface import _media_scope as media

SESSION = "20260825T023859Z-c83a0e3b"
CLIP = SESSION + "-27275f15365a"


class BridgeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.work = self.root / "work" / SESSION
        self.work.mkdir(parents=True, exist_ok=True)
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.submits = []
        self.cancels = []

    def write_request(self, clip=CLIP, revision=1) -> Path:
        path = self.work / (clip + bridge.REQUEST_SUFFIX)
        path.write_text(json.dumps({
            "schema_version": 1, "session_id": SESSION, "clip_id": clip,
            "clip_revision": revision,
            "source_segments": ["raw/%s/seg.mkv" % SESSION],
            "start_seconds": 36.0, "end_seconds": 96.0,
            "variants": ["horizontal", "vertical"],
            "profile_version": "bf6-qsv-v1",
        }), encoding="utf-8")
        return path

    def make(self, *, status_map=None, job_id="job_1", receipt=None):
        status_map = status_map or {"status": "queued"}

        def submit(arguments):
            self.submits.append(arguments)
            return {"ok": True, "job_id": job_id, "status": "queued",
                    "idempotency_key": "abc123"}

        def status(jid):
            return dict(status_map, job_id=jid)

        def cancel(jid, reason):
            self.cancels.append((jid, reason))
            return {"ok": True, "job_id": jid, "status": "cancelled",
                    "stopped_before_start": True}

        return bridge.Bf6Bridge(submit=submit, status=status, cancel=cancel,
                                receipt=lambda s: receipt)

    def read(self, suffix, clip=CLIP):
        path = self.work / (clip + suffix)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class SubmissionTest(BridgeTestBase):
    def test_submits_a_new_request_and_records_the_claim(self) -> None:
        self.write_request()
        tick = self.make().tick()
        self.assertEqual([CLIP], tick.submitted)
        claim = self.read(bridge.CLAIM_SUFFIX)
        self.assertEqual("job_1", claim["job_id"])
        self.assertEqual("abc123", claim["idempotency_key"])

    def test_passes_exactly_the_tool_arguments(self) -> None:
        # AM4 writes what submit_render takes, so nothing is translated in flight.
        self.write_request()
        self.make().tick()
        self.assertEqual(1, len(self.submits))
        self.assertEqual(
            {"session_id", "clip_id", "clip_revision", "source_segments",
             "start_seconds", "end_seconds", "variants", "profile_version"},
            set(self.submits[0]))

    def test_a_known_job_is_queried_never_resubmitted(self) -> None:
        # THE reconciliation rule: do not blindly resubmit.
        self.write_request()
        b = self.make()
        b.tick()
        self.assertEqual(1, len(self.submits))
        for _ in range(3):
            b.tick()
        self.assertEqual(1, len(self.submits), "a claimed job must never resubmit")

    def test_a_completed_clip_is_skipped_entirely(self) -> None:
        self.write_request()
        (self.work / (CLIP + bridge.RESULT_SUFFIX)).write_text("{}", encoding="utf-8")
        tick = self.make().tick()
        self.assertEqual([CLIP], tick.skipped)
        self.assertEqual([], self.submits)

    def test_non_terminal_jobs_are_left_alone(self) -> None:
        self.write_request()
        tick = self.make(status_map={"status": "running"}).tick()
        self.assertEqual([CLIP], tick.waiting)
        self.assertIsNone(self.read(bridge.RESULT_SUFFIX))


class ConvergenceTest(BridgeTestBase):
    RECEIPT = {
        "variants": [
            {"variant": "horizontal", "promoted": True, "reason": "promoted",
             "output": "E:/d/h.mp4",
             "validation": {"measured": {"bitrate_mbps": 85.65,
                                         "size_bytes": 123, "sha256": "ab"}}},
            {"variant": "vertical", "promoted": True, "reason": "promoted",
             "output": "E:/d/v.mp4",
             "validation": {"measured": {"bitrate_mbps": 12.29,
                                         "size_bytes": 45, "sha256": "cd"}}},
        ],
    }

    def test_publishes_a_terminal_result(self) -> None:
        self.write_request()
        tick = self.make(status_map={"status": "succeeded", "lane": "b70@bus4",
                                     "profile_version": "bf6-qsv-v1"},
                         receipt=self.RECEIPT).tick()
        self.assertEqual([CLIP], tick.reconciled)
        result = self.read(bridge.RESULT_SUFFIX)
        self.assertTrue(result["ok"])
        self.assertEqual("b70@bus4", result["lane"])
        self.assertEqual(2, len(result["variants"]))
        self.assertEqual(85.65, result["variants"][0]["bitrate_mbps"])

    def test_reconstructs_a_result_when_the_claim_survived_but_the_result_did_not(self) -> None:
        # The crash window: job terminal, publication never happened.
        self.write_request()
        (self.work / (CLIP + bridge.CLAIM_SUFFIX)).write_text(
            json.dumps({"clip_id": CLIP, "job_id": "job_1", "clip_revision": 1}),
            encoding="utf-8")
        tick = self.make(status_map={"status": "succeeded"},
                         receipt=self.RECEIPT).tick()
        self.assertEqual([CLIP], tick.reconciled)
        self.assertEqual([], self.submits, "must query the claimed job, not resubmit")
        self.assertIsNotNone(self.read(bridge.RESULT_SUFFIX))

    def test_a_lost_claim_resubmits_but_the_idempotency_key_prevents_a_duplicate(self) -> None:
        # Crash between submit and claim write. The bridge resubmits -- and the
        # deterministic key means the door returns the SAME job.
        self.write_request()
        b = self.make(status_map={"status": "running"})
        b.tick()
        (self.work / (CLIP + bridge.CLAIM_SUFFIX)).unlink()
        b.tick()
        self.assertEqual(2, len(self.submits))
        self.assertEqual(self.submits[0], self.submits[1],
                         "identical arguments -> identical idempotency key")

    def test_a_failed_job_still_publishes_a_result(self) -> None:
        self.write_request()
        receipt = {"variants": [
            {"variant": "horizontal", "promoted": False,
             "reason": "validation_failed",
             "validation": {"measured": {"bitrate_mbps": 111.54}}},
            {"variant": "vertical", "promoted": False,
             "reason": "withheld_sibling_failed", "validation": {}},
        ]}
        self.make(status_map={"status": "failed", "reason": "validation_failed"},
                  receipt=receipt).tick()
        result = self.read(bridge.RESULT_SUFFIX)
        self.assertFalse(result["ok"])
        self.assertEqual("validation_failed", result["reason"])
        self.assertFalse(any(v["promoted"] for v in result["variants"]))

    def test_the_request_sidecar_is_never_deleted(self) -> None:
        # AM4 owns its own files and retires them when its reconciler is done.
        path = self.write_request()
        self.make(status_map={"status": "succeeded"}, receipt=self.RECEIPT).tick()
        self.assertTrue(path.exists())


class ShareRobustnessTest(BridgeTestBase):
    def test_a_tmp_request_is_never_read(self) -> None:
        # A .tmp is a write in progress; acting on it means acting on half a
        # request.
        (self.work / (CLIP + bridge.REQUEST_SUFFIX + ".tmp")).write_text(
            "{}", encoding="utf-8")
        tick = self.make().tick()
        self.assertEqual([], tick.submitted)
        self.assertEqual([], self.submits)

    def test_stale_tmp_files_are_collected(self) -> None:
        stale = self.work / (CLIP + bridge.REQUEST_SUFFIX + ".tmp")
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - bridge.STALE_TMP_S - 60
        os.utime(stale, (old, old))
        self.assertIn(str(stale), bridge.collect_stale_tmp())
        self.assertFalse(stale.exists())

    def test_a_fresh_tmp_is_left_alone(self) -> None:
        fresh = self.work / (CLIP + bridge.REQUEST_SUFFIX + ".tmp")
        fresh.write_text("{}", encoding="utf-8")
        self.assertEqual([], bridge.collect_stale_tmp())
        self.assertTrue(fresh.exists())

    def test_an_unreadable_request_is_reported_not_fatal(self) -> None:
        (self.work / (CLIP + bridge.REQUEST_SUFFIX)).write_text("{bad", encoding="utf-8")
        tick = self.make().tick()
        self.assertTrue(tick.errors)
        self.assertEqual([], self.submits)

    def test_one_bad_clip_does_not_stop_the_pass(self) -> None:
        other = SESSION + "-aaaaaaaaaaaa"
        (self.work / (other + bridge.REQUEST_SUFFIX)).write_text("{bad", encoding="utf-8")
        self.write_request()
        tick = self.make().tick()
        self.assertEqual([CLIP], tick.submitted)
        self.assertTrue(tick.errors)

    def test_a_missing_share_yields_no_work_rather_than_an_exception(self) -> None:
        # The desired behaviour is convergence when the share returns, not
        # heroic recovery while it is gone.
        with patch.dict(os.environ, {media.MEDIA_ROOT_ENV: r"Z:\not-mounted"}):
            self.assertEqual([], bridge.list_requests())

    def test_result_writes_are_atomic(self) -> None:
        self.write_request()
        self.make(status_map={"status": "succeeded"}, receipt={"variants": []}).tick()
        self.assertEqual([], list(self.work.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()


class SupersedeTest(BridgeTestBase):
    """A new revision must never inherit, nor wait behind, an old claim.

    The window is real: AM4 bumps the authority and clears the exchange, and the
    bridge may be writing a claim for the OLD job at that moment. What arrives on
    disk afterwards is a new request beside a stale claim, and the bridge cannot
    tell that apart from a supersede -- so both take the same path.
    """

    def write_claim(self, *, job_id="job_old", revision=1) -> None:
        (self.work / (CLIP + bridge.CLAIM_SUFFIX)).write_text(json.dumps({
            "schema_version": 1, "clip_id": CLIP, "job_id": job_id,
            "clip_revision": revision, "idempotency_key": "old",
            "submitted_at": time.time(),
        }), encoding="utf-8")

    def test_a_claim_for_an_older_revision_is_not_inherited(self) -> None:
        self.write_request(revision=2)
        self.write_claim(job_id="job_old", revision=1)
        b = self.make(job_id="job_new")
        tick = b.tick()

        self.assertEqual([CLIP], tick.submitted, "revision 2 must get its own job")
        self.assertEqual(2, self.submits[0]["clip_revision"])
        claim = self.read(bridge.CLAIM_SUFFIX)
        self.assertEqual("job_new", claim["job_id"])
        self.assertEqual(2, claim["clip_revision"])

    def test_the_superseded_job_is_cancelled(self) -> None:
        self.write_request(revision=2)
        self.write_claim(job_id="job_old", revision=1)
        tick = self.make(job_id="job_new").tick()

        self.assertEqual(1, len(self.cancels))
        self.assertEqual("job_old", self.cancels[0][0])
        self.assertIn("superseded", self.cancels[0][1])
        self.assertEqual(1, len(tick.cancelled))

    def test_a_failed_cancel_still_lets_the_new_revision_proceed(self) -> None:
        # Cancellation is an optimisation. Correctness rests on the commit-time
        # revision check, so a cancel that does not land must not block anything.
        self.write_request(revision=2)
        self.write_claim(job_id="job_old", revision=1)

        def refuse(jid, reason):
            raise RuntimeError("door unreachable")

        b = self.make(job_id="job_new")
        b._cancel = refuse
        tick = b.tick()

        self.assertEqual([CLIP], tick.submitted)
        self.assertEqual("job_new", self.read(bridge.CLAIM_SUFFIX)["job_id"])
        self.assertTrue(any("not cancelled" in e for e in tick.errors))

    def test_a_claim_at_the_same_revision_is_still_never_resubmitted(self) -> None:
        # The guard must not fire on the ordinary path.
        self.write_request(revision=2)
        self.write_claim(job_id="job_old", revision=2)
        tick = self.make().tick()
        self.assertEqual([], tick.submitted)
        self.assertEqual([], self.cancels)
        self.assertEqual([CLIP], tick.waiting)

    def test_an_unlabelled_claim_is_resubmitted_but_never_cancelled(self) -> None:
        """No revision is proof of nothing -- it may be this revision's own job.

        Resubmitting is safe (the idempotency key returns the same job);
        cancelling on a guess would kill live work, so it is not done.
        """
        self.write_request(revision=1)
        (self.work / (CLIP + bridge.CLAIM_SUFFIX)).write_text(json.dumps({
            "schema_version": 1, "clip_id": CLIP, "job_id": "job_old",
        }), encoding="utf-8")
        tick = self.make(job_id="job_new").tick()
        self.assertEqual([CLIP], tick.submitted)
        self.assertEqual([], self.cancels, "must not cancel on a guess")
        self.assertEqual("job_new", self.read(bridge.CLAIM_SUFFIX)["job_id"])


class ResultRevisionTest(BridgeTestBase):
    def test_the_result_carries_the_revision_it_rendered(self) -> None:
        # Without this AM4's guard has nothing to compare and every result
        # passes -- the guard would exist but never fire.
        self.write_request(revision=3)
        self.make(status_map={"status": "succeeded", "lane": "b70@bus9"}).tick()
        result = self.read(bridge.RESULT_SUFFIX)
        self.assertEqual(3, result["clip_revision"])
        self.assertTrue(result["ok"])
