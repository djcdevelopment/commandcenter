from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.coordination import GpuTenancyStore
from hearth.imagegen import handoff
from hearth.imagegen.handoff import AgentStatus
from hearth.imagegen.session import ImageSessionController


class _Gate:
    def __init__(self, active: bool) -> None:
        self.active = active

    def to_dict(self) -> dict:
        return {"active": self.active, "reason": "test media gate"}


class ImageSessionPrecheckTest(unittest.TestCase):
    def test_refuses_before_fencing_when_interactive_agent_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            store = GpuTenancyStore(Path(temporary) / "coordination.sqlite")
            starts = []
            controller = ImageSessionController(
                store=store, autostart=False,
                agent_status=lambda: AgentStatus(False, None, "missing", None),
                agent_start=lambda: starts.append(True) or AgentStatus(
                    False, None, "launch failed", None
                ),
                gate_probe=lambda: _Gate(False),
            )
            try:
                result = controller.start()
                self.assertFalse(result["ok"])
                self.assertEqual([True], starts)
                self.assertIsNone(store.get("omen-b70-pool"))
            finally:
                controller.close()

    def test_media_gate_refuses_without_starting_device_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            store = GpuTenancyStore(Path(temporary) / "coordination.sqlite")
            starts = []
            controller = ImageSessionController(
                store=store, autostart=False,
                agent_status=lambda: AgentStatus(False, None, "missing", None),
                agent_start=lambda: starts.append(True) or AgentStatus(
                    True, 0, "ready", {"ready": True}
                ),
                gate_probe=lambda: _Gate(True),
            )
            try:
                result = controller.start()
                self.assertFalse(result["ok"])
                self.assertEqual([], starts)
                self.assertIn("BF6", result["error"])
                self.assertIsNone(store.get("omen-b70-pool"))
            finally:
                controller.close()

    def test_refuses_before_fencing_when_bf6_or_obs_gate_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            store = GpuTenancyStore(Path(temporary) / "coordination.sqlite")
            controller = ImageSessionController(
                store=store, autostart=False,
                agent_status=lambda: AgentStatus(True, 0, "ready", {"ready": True}),
                gate_probe=lambda: _Gate(True),
            )
            try:
                result = controller.start()
                self.assertFalse(result["ok"])
                self.assertIn("BF6", result["error"])
                self.assertIsNone(store.get("omen-b70-pool"))
            finally:
                controller.close()

    def test_forced_stop_uses_ledger_aware_bulk_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            store = GpuTenancyStore(Path(temporary) / "coordination.sqlite")
            snapshot = store.acquire(
                resource="omen-b70-pool", session_id="session_a",
                ttl_seconds=180, state="imagegen",
            )
            reasons = []
            controller = ImageSessionController(
                store=store, autostart=False,
                cancel_all=lambda reason: reasons.append(reason),
            )
            try:
                with patch.object(controller, "_restore") as restore:
                    controller._stop_transition(snapshot, True, "test restore")
                self.assertEqual(1, len(reasons))
                self.assertIn("forced session stop", reasons[0])
                restore.assert_called_once()
            finally:
                controller.close()


class AbandonedClaimTest(unittest.TestCase):
    """One stuck claim used to wedge ArcServe down with no automated escape.

    The fault path refused to restore while claims existed, and the scheduled fx99 recovery
    deferred for the same reason. Two guards, each correct alone, that together had no exit.
    """

    def _controller(self, temporary: str):
        store = GpuTenancyStore(Path(temporary) / "coordination.sqlite")
        snapshot = store.acquire(
            resource="omen-b70-pool", session_id="session_wedge",
            ttl_seconds=180, state="imagegen",
        )
        controller = ImageSessionController(store=store, autostart=False)
        return store, snapshot, controller

    def test_a_claim_with_a_live_agent_still_protects_arcserve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            handoff.ensure_dirs()
            (handoff.root() / "claims" / "job_live.json").write_text("{}", encoding="utf-8")
            _store, snapshot, controller = self._controller(temporary)
            try:
                with patch.object(handoff, "agent_status",
                                  lambda **kw: AgentStatus(True, 0.0, "ready", {"ready": True})):
                    self.assertFalse(controller._reap_abandoned_claims(snapshot))
                self.assertEqual(1, len(handoff.list_claims()))
            finally:
                controller.close()

    def test_a_claim_no_worker_can_finish_is_reaped_so_arcserve_can_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            handoff.ensure_dirs()
            claim = handoff.root() / "claims" / "job_stuck.json"
            claim.write_text("{}", encoding="utf-8")
            stale = time.time() - (handoff.AGENT_STALE_SECONDS + 60)
            os.utime(claim, (stale, stale))
            _store, snapshot, controller = self._controller(temporary)
            try:
                with patch.object(handoff, "agent_status",
                                  lambda **kw: AgentStatus(False, None, "gone", None)):
                    self.assertTrue(controller._reap_abandoned_claims(snapshot))
                self.assertEqual([], handoff.list_claims())
            finally:
                controller.close()

    def test_a_recent_claim_is_not_reaped_even_with_a_dead_agent(self) -> None:
        """The worker may have only just died; give it the full heartbeat window."""
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HEARTH_IMAGEGEN_HANDOFF": temporary}
        ):
            handoff.ensure_dirs()
            (handoff.root() / "claims" / "job_fresh.json").write_text("{}", encoding="utf-8")
            _store, snapshot, controller = self._controller(temporary)
            try:
                with patch.object(handoff, "agent_status",
                                  lambda **kw: AgentStatus(False, None, "gone", None)):
                    self.assertFalse(controller._reap_abandoned_claims(snapshot))
                self.assertEqual(1, len(handoff.list_claims()))
            finally:
                controller.close()


if __name__ == "__main__":
    unittest.main()
