from __future__ import annotations

from unittest import TestCase, mock

from hearth.toolsurface import summon
from hearth.toolsurface.summon import checkpoint_vm, start_ollama, wake_am4

HEALTH_UP = {"reachable": True, "backend_ok": True, "backend": {"ok": True, "status": 200}}
HEALTH_DOWN = {"reachable": True, "backend_ok": False, "backend": {"ok": False, "status": 503}}
HEALTH_FACADE_GONE = {"reachable": False, "backend_ok": False, "error": "URLError: timed out"}

OCC_FREE = {"occupancy": "available", "detail": "--- /dev/dri/renderD128"}
OCC_IMAGEGEN = {"occupancy": "busy",
                "detail": "COMMAND\nderek 4242 F.... python\nderek 4243 F.... ComfyUI"}
OCC_OWN_LLAMA = {"occupancy": "busy", "detail": "COMMAND\nderek 999 F.... llama-serve"}
OCC_UNKNOWN = {"occupancy": "unknown", "detail": "ssh exit 255: timeout"}

QUEUE_IDLE = {"active": False, "detail": "queue_running=0 queue_pending=0"}
QUEUE_BUSY = {"active": True, "detail": "queue_running=1 queue_pending=3"}
QUEUE_UNKNOWN = {"active": None, "detail": "empty response from :8188"}


class WakeAm4Tests(TestCase):
    """wake_am4 is LIVE: idempotent via facade serve-truth, imagegen-occupancy-gated,
    wakes the managed b70-planner unit (never nohup — runbook gotcha #2)."""

    def _wake(self, health_seq: list[dict], occupancy: dict = OCC_FREE,
              ssh: tuple = ("", None), queue: dict = QUEUE_IDLE,
              force: bool = False, wait_s: int = 10) -> tuple[dict, mock.Mock]:
        with mock.patch.object(summon, "_fetch_health", side_effect=health_seq), \
                mock.patch.object(summon, "_check_occupancy", return_value=occupancy), \
                mock.patch.object(summon, "_imagegen_active", return_value=queue), \
                mock.patch.object(summon, "_ssh", return_value=ssh) as ssh_mock, \
                mock.patch.object(summon, "_sleep"):
            return wake_am4(force=force, wait_s=wait_s), ssh_mock

    def test_backend_already_serving_is_a_no_op(self) -> None:
        result, ssh_mock = self._wake([HEALTH_UP])
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "already-serving")
        ssh_mock.assert_not_called()

    def test_in_flight_imagegen_job_refuses_the_wake(self) -> None:
        result, ssh_mock = self._wake([HEALTH_DOWN], occupancy=OCC_IMAGEGEN,
                                      queue=QUEUE_BUSY)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "refused")
        self.assertEqual(result["occupancy"], OCC_IMAGEGEN)
        self.assertEqual(result["comfyui_queue"], QUEUE_BUSY)
        ssh_mock.assert_not_called()

    def test_unverifiable_imagegen_queue_refuses_the_wake(self) -> None:
        result, ssh_mock = self._wake([HEALTH_DOWN], occupancy=OCC_IMAGEGEN,
                                      queue=QUEUE_UNKNOWN)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "refused")
        self.assertIn("unverifiable", result["reason"])
        ssh_mock.assert_not_called()

    def test_idle_comfyui_merely_resident_does_not_block(self) -> None:
        # ComfyUI holds the render nodes 24/7 — the steady state on AM4. Only a
        # non-empty queue is an in-flight job.
        result, ssh_mock = self._wake([HEALTH_DOWN, HEALTH_UP],
                                      occupancy=OCC_IMAGEGEN, queue=QUEUE_IDLE)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "woken")
        ssh_mock.assert_called_once()

    def test_force_overrides_in_flight_imagegen(self) -> None:
        result, ssh_mock = self._wake([HEALTH_DOWN, HEALTH_UP],
                                      occupancy=OCC_IMAGEGEN, queue=QUEUE_BUSY,
                                      force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "woken")
        ssh_mock.assert_called_once()

    def test_own_llama_slots_do_not_block_the_wake(self) -> None:
        # busy-with-only-llama = our own slot units (critic resident / planner
        # mid-load); starting the managed unit is idempotent, so proceed.
        result, ssh_mock = self._wake([HEALTH_DOWN, HEALTH_UP], occupancy=OCC_OWN_LLAMA)
        self.assertTrue(result["ok"])
        ssh_mock.assert_called_once()

    def test_unknown_occupancy_proceeds_pinned_semantics(self) -> None:
        result, _ = self._wake([HEALTH_DOWN, HEALTH_UP], occupancy=OCC_UNKNOWN)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "woken")

    def test_wake_starts_the_managed_planner_unit_no_sudo_no_nohup(self) -> None:
        _, ssh_mock = self._wake([HEALTH_DOWN, HEALTH_UP])
        command = ssh_mock.call_args[0][0]
        self.assertEqual(command, "systemctl --user start b70-planner.service")
        self.assertNotIn("sudo", command)
        self.assertNotIn("nohup", command)

    def test_ssh_failure_is_reported(self) -> None:
        result, _ = self._wake([HEALTH_DOWN], ssh=(None, "ssh exit 255: unreachable"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "ssh-failed")
        self.assertIn("unreachable", result["error"])

    def test_backend_never_ready_times_out_with_journal_hint(self) -> None:
        result, _ = self._wake([HEALTH_DOWN, HEALTH_DOWN, HEALTH_DOWN], wait_s=10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "started-not-ready")
        self.assertIn("journalctl --user -u b70-planner.service", result["note"])

    def test_facade_unreachable_still_attempts_wake_and_reports(self) -> None:
        result, ssh_mock = self._wake([HEALTH_FACADE_GONE, HEALTH_FACADE_GONE], wait_s=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "started-not-ready")
        ssh_mock.assert_called_once()

    def test_zero_wait_is_fire_and_forget(self) -> None:
        result, ssh_mock = self._wake([HEALTH_DOWN], wait_s=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "started-not-ready")
        ssh_mock.assert_called_once()

    def test_negative_wait_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            wake_am4(wait_s=-1)


class SummonStubShapeTests(TestCase):
    """Remaining stubs must carry the real command shapes; H3 flips them live."""

    def _assert_stub_shape(self, result: dict) -> None:
        self.assertFalse(result["ok"])
        self.assertTrue(result["stub"])
        self.assertIsInstance(result["would_run"], str)
        self.assertTrue(result["would_run"].strip())

    def test_start_ollama_defaults_to_resident_coder_model(self) -> None:
        result = start_ollama()
        self._assert_stub_shape(result)
        self.assertEqual(result["would_run"], "ollama serve")
        self.assertEqual(result["model"], "qwen3-coder:30b")
        self.assertIn("qwen3-coder:30b", result["then"])

    def test_start_ollama_carries_requested_model(self) -> None:
        result = start_ollama(model="mixtral:8x7b")
        self.assertIn("mixtral:8x7b", result["then"])

    def test_start_ollama_rejects_empty_model(self) -> None:
        with self.assertRaises(ValueError):
            start_ollama(model="  ")

    def test_checkpoint_vm_uses_hyperv_checkpoint_command(self) -> None:
        result = checkpoint_vm("claudefarm1")
        self._assert_stub_shape(result)
        self.assertIn("Checkpoint-VM -Name 'claudefarm1'", result["would_run"])
        self.assertIn("Export-VMSnapshot", result["export"])

    def test_checkpoint_vm_rejects_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            checkpoint_vm("")
