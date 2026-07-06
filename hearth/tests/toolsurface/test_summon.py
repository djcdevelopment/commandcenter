from __future__ import annotations

from unittest import TestCase

from hearth.toolsurface.summon import checkpoint_vm, start_ollama, wake_am4


class SummonStubShapeTests(TestCase):
    """Stubs must carry the real command shapes now; H3 flips them live."""

    def _assert_stub_shape(self, result: dict) -> None:
        self.assertFalse(result["ok"])
        self.assertTrue(result["stub"])
        self.assertIsInstance(result["would_run"], str)
        self.assertTrue(result["would_run"].strip())

    def test_wake_am4_targets_oxen_backend_over_ssh(self) -> None:
        result = wake_am4()
        self._assert_stub_shape(result)
        self.assertIn("ssh derek@am4.tail8e749c.ts.net", result["would_run"])
        # the real units are systemd --user services b70-planner/b70-critic (no sudo)
        self.assertIn("systemctl --user", result["would_run"])
        self.assertIn("b70-planner", result["would_run"])
        self.assertIn("b70-critic", result["would_run"])
        self.assertIn("8090/health", result["verify"])

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
