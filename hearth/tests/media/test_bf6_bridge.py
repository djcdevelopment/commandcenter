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

        return bridge.Bf6Bridge(submit=submit, status=status,
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
            json.dumps({"clip_id": CLIP, "job_id": "job_1"}), encoding="utf-8")
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
