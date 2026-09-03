from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.coordination import GpuTenancyStore
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


if __name__ == "__main__":
    unittest.main()
