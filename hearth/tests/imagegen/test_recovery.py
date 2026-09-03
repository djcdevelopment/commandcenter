"""Branch table for the scheduled fx99 recovery.

This module force-kills llama-server on the production rung, unattended, every five
minutes. It had no tests at all, and 104/104 live invocations had taken the same
short-circuit -- so neither terminal path had ever executed. These pin the guards that keep
it from acting, and the self-limiting behaviour that keeps a failure from becoming a loop.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.coordination import GpuTenancyStore
from hearth.imagegen import handoff, recovery
from hearth.imagegen.handoff import AgentStatus
from hearth.imagegen.session import ImageSessionController

POOL = "omen-b70-pool"
LIVE_AGENT = AgentStatus(True, 0.0, "ready", {"ready": True})
DEAD_AGENT = AgentStatus(False, None, "no interactive agent heartbeat", None)


class _RecoveryHarness(unittest.TestCase):
    """Temp handoff root, temp tenancy DB, temp sentinel, and a resolvable token."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.sentinel = self.root / "arc-maintenance.stop"
        self.store = GpuTenancyStore(self.root / "coordination.sqlite")

        patches = [
            patch.dict(os.environ, {
                "HEARTH_IMAGEGEN_HANDOFF": str(self.root),
                # Without this the recovery refuses outright (see the blocked-path test).
                "OMEN_ARC_TOKEN": "sk-test",
            }),
            patch.object(recovery, "ARC_SENTINEL", self.sentinel),
            patch.object(recovery, "GpuTenancyStore", lambda *a, **k: self.store),
            patch.object(recovery, "_backend_listening", lambda: None),
            patch.object(handoff, "agent_status", lambda **kw: DEAD_AGENT),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        handoff.ensure_dirs()

    def acquire(self, *, session_id: str = "imgsess_test", ttl: float = 180.0,
                age: float = 0.0, state: str = "imagegen"):
        """Acquire the pool as it looked `age` seconds ago."""
        return self.store.acquire(
            resource=POOL, session_id=session_id, ttl_seconds=ttl,
            state=state, reason="test", now=time.time() - age,
        )

    def stale_expired(self):
        """Owned by imagegen, fence expired and untouched well past STALE_SECONDS."""
        return self.acquire(ttl=180.0, age=1000.0)


class RecoveryGuardTest(_RecoveryHarness):
    """The five ways recovery correctly declines to touch anything."""

    def test_no_image_session_is_a_no_op_and_clears_banked_attempts(self) -> None:
        recovery._save_state({"session_id": "old", "epoch": 1, "attempts": 2})
        result = recovery.recover()
        self.assertTrue(result["ok"])
        self.assertEqual("none", result["action"])
        self.assertIn("ArcServe owns the pool", result["reason"])
        self.assertEqual({}, recovery._load_state())

    def test_live_and_recently_renewed_fence_is_left_alone(self) -> None:
        self.acquire(ttl=180.0, age=0.0)
        result = recovery.recover()
        self.assertTrue(result["ok"])
        self.assertEqual("none", result["action"])
        self.assertIn("renewed", result["reason"])

    def test_healthy_agent_on_a_live_fence_is_left_alone(self) -> None:
        # Stale updated_at but the fence has not expired, and the worker is answering.
        self.acquire(ttl=2000.0, age=500.0)
        with patch.object(handoff, "agent_status", lambda **kw: LIVE_AGENT):
            result = recovery.recover()
        self.assertTrue(result["ok"])
        self.assertEqual("none", result["action"])
        self.assertIn("interactive agent is healthy", result["reason"])

    def test_outstanding_claims_defer_instead_of_restarting(self) -> None:
        self.stale_expired()
        (handoff.root() / "claims" / "job_a.json").write_text("{}", encoding="utf-8")
        result = recovery.recover()
        self.assertFalse(result["ok"])
        self.assertEqual("deferred", result["action"])
        self.assertEqual(["job_a"], result["claims"])

    def test_listening_image_backend_defers_instead_of_restarting(self) -> None:
        self.stale_expired()
        with patch.object(recovery, "_backend_listening", lambda: 18188):
            result = recovery.recover()
        self.assertFalse(result["ok"])
        self.assertEqual("deferred", result["action"])
        self.assertEqual(18188, result["port"])


class RecoveryFenceOwnershipTest(_RecoveryHarness):
    def test_refuses_when_the_maintenance_sentinel_belongs_to_someone_else(self) -> None:
        """A human maintenance window must not be ended by the imagegen timer."""
        self.stale_expired()
        self.sentinel.write_text("campaign maintenance -- hands off\n", encoding="utf-8")
        with patch.object(ImageSessionController, "restart_arcserve") as restart:
            result = recovery.recover()
        restart.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual("none", result["action"])
        self.assertIn("not owned by this image session", result["reason"])
        self.assertTrue(self.sentinel.exists())

    def test_acts_when_the_sentinel_names_this_session(self) -> None:
        snapshot = self.stale_expired()
        self.sentinel.write_text(
            "owned by %s epoch %d\n" % (snapshot.session_id, snapshot.epoch),
            encoding="utf-8",
        )
        with patch.object(ImageSessionController, "restart_arcserve"), \
                patch.object(ImageSessionController, "verify_arcserve", return_value=True):
            result = recovery.recover(verify_timeout=5)
        self.assertTrue(result["ok"])
        self.assertEqual("restored", result["action"])
        self.assertFalse(self.sentinel.exists())


class RecoveryPreconditionTest(_RecoveryHarness):
    def test_missing_arc_token_blocks_before_anything_destructive(self) -> None:
        """The D1 regression guard.

        Without OMEN_ARC_TOKEN every ArcServe probe 401s, so verify_arcserve can never
        pass. Acting anyway meant: kill llama-server, fail the verify, never release the
        fence, repeat in five minutes. Refuse before the sentinel is touched instead.
        """
        self.stale_expired()
        self.sentinel.write_text("owned by imgsess_test epoch 1\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OMEN_ARC_TOKEN", None)
            with patch.object(ImageSessionController, "restart_arcserve") as restart:
                result = recovery.recover()
        restart.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual("blocked", result["action"])
        self.assertIn("OMEN_ARC_TOKEN", result["reason"])
        self.assertTrue(self.sentinel.exists(), "sentinel must survive a blocked recovery")


class RecoverySelfLimitTest(_RecoveryHarness):
    def test_successful_recovery_releases_the_fence_and_clears_state(self) -> None:
        snapshot = self.stale_expired()
        with patch.object(ImageSessionController, "restart_arcserve") as restart, \
                patch.object(ImageSessionController, "verify_arcserve", return_value=True):
            result = recovery.recover(verify_timeout=5)
        restart.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual("restored", result["action"])
        self.assertEqual(snapshot.session_id, result["session_id"])
        released = self.store.get(POOL)
        self.assertIsNotNone(released)
        self.assertNotEqual("imagegen", released.owner)
        self.assertEqual({}, recovery._load_state())

    def test_failed_verify_banks_the_attempt(self) -> None:
        snapshot = self.stale_expired()
        with patch.object(ImageSessionController, "restart_arcserve"), \
                patch.object(ImageSessionController, "verify_arcserve", return_value=False), \
                patch.object(recovery.time, "sleep", lambda _s: None):
            result = recovery.recover(verify_timeout=0.01)
        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["action"])
        self.assertEqual(1, result["attempts"])
        state = recovery._load_state()
        self.assertEqual(snapshot.session_id, state["session_id"])
        self.assertEqual(1, state["attempts"])

    def test_attempt_is_banked_before_the_destructive_step(self) -> None:
        """An interrupted run must still count.

        The SSH transport can cut this off after the force-kill and before the release. If
        the counter only advanced on completion, that cut would make the loop unbounded --
        which is exactly the failure mode being guarded.
        """
        snapshot = self.stale_expired()
        banked = {}

        def _explode() -> None:
            banked.update(recovery._load_state())
            raise RuntimeError("ssh timeout, mid-restart")

        with patch.object(ImageSessionController, "restart_arcserve", side_effect=_explode):
            with self.assertRaises(RuntimeError):
                recovery.recover(verify_timeout=5)
        self.assertEqual(1, banked.get("attempts"),
                         "attempt must be persisted BEFORE ArcServe is bounced")
        self.assertEqual(snapshot.session_id, banked.get("session_id"))

    def test_escalates_instead_of_bouncing_arcserve_forever(self) -> None:
        snapshot = self.stale_expired()
        recovery._save_state({
            "session_id": snapshot.session_id, "epoch": snapshot.epoch,
            "attempts": recovery.MAX_ATTEMPTS,
        })
        with patch.object(ImageSessionController, "restart_arcserve") as restart:
            result = recovery.recover()
        restart.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual("escalated", result["action"])
        self.assertEqual(recovery.MAX_ATTEMPTS, result["attempts"])
        # The fence stays held on purpose: a rung that will not come back belongs OUT of
        # rotation, which is far cheaper than killing it on a five-minute loop.
        held = self.store.get(POOL)
        self.assertIsNotNone(held)
        self.assertEqual("imagegen", held.owner)

    def test_a_new_session_gets_a_fresh_attempt_budget(self) -> None:
        recovery._save_state({
            "session_id": "imgsess_previous", "epoch": 1,
            "attempts": recovery.MAX_ATTEMPTS,
        })
        self.stale_expired(),
        with patch.object(ImageSessionController, "restart_arcserve") as restart, \
                patch.object(ImageSessionController, "verify_arcserve", return_value=True):
            result = recovery.recover(verify_timeout=5)
        restart.assert_called_once()
        self.assertEqual("restored", result["action"])


class RecoveryContractTest(unittest.TestCase):
    def test_recovery_only_uses_the_public_controller_surface(self) -> None:
        """recovery.py runs out-of-process; a rename here breaks it with nothing to catch.

        These three names are the contract between this repo and fx99's timer.
        """
        for name in ("restart_arcserve", "verify_arcserve", "record_event", "close"):
            self.assertTrue(callable(getattr(ImageSessionController, name, None)),
                            "ImageSessionController.%s is part of the recovery contract" % name)

    def test_verify_timeout_default_stays_below_the_ssh_budget(self) -> None:
        """The two used to be 240 here against a 180 s SSH timeout on fx99.

        That guaranteed the only branch doing real work was cut off before it could verify.
        fleet/fx99-keepalive/recover-omen-imagegen.sh now allows 240 s per host.
        """
        self.assertLess(recovery.DEFAULT_VERIFY_TIMEOUT, 240.0)


if __name__ == "__main__":
    unittest.main()
