"""hearth.toolsurface.rotation (P9): refusals, window gating, sibling retry, provider contract."""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.rotation.swapclient import LoadOutcome, RunningModel
from hearth.rotation.telemetry import CardTelemetry, HostTelemetry
from hearth.toolsurface import rotation as R

A = "0000:04:00.0"
B = "0000:09:00.0"
GOOD = """
[phi4-vk1] llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics) - 16000 MiB free
[phi4-vk1] load_tensors: offloaded 40/40 layers to GPU
[phi4-vk1] load_tensors:      Vulkan1 model buffer size =  8300.00 MiB
"""


class _Client:
    def __init__(self, running=(), logs="", ready=True):
        self._running = list(running)
        self._logs = logs
        self.ready = ready
        self.unloaded = []
        self.loads = []

    def health(self):
        return True

    def running(self):
        return list(self._running)

    def wait_ready(self, entry, deadline_s=300.0):
        self.loads.append(entry)
        return LoadOutcome(self.ready, 8.2, 200, {"predicted_n": 1}, None, 1)

    def logs(self):
        return self._logs

    def unload(self, entry):
        self.unloaded.append(entry)
        return True


def _telemetry(local_a=14.52):
    return HostTelemetry("t", 39.0, (
        CardTelemetry(A, "Intel(R) Arc(TM) Pro B70 Graphics", "x", 32.5, local_a, 0.0, 52.0, 52.0),
        CardTelemetry(B, "Intel(R) Arc(TM) Pro B70 Graphics", "y", 32.5, 15.44, 0.0, 50.0, 50.0)))


class RotationToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = _Client(running=[RunningModel("qwen3-30b-a3b", "ready")], logs=GOOD)
        snaps = iter([_telemetry(), _telemetry(local_a=14.52 + 8.4), _telemetry(), _telemetry()])
        self.patches = [
            patch.object(R, "VAR_DIR", Path(self.tmp.name)),
            patch.object(R, "_client_factory", lambda endpoint: self.client),
            patch.object(R, "_snapshot_fn", lambda: next(snaps)),
            patch.object(R, "_fence_fn", lambda: None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_provider_contract_shape(self) -> None:
        tools = R.get_tools()
        self.assertEqual([t.__name__ for t in tools],
                         ["rotation_status", "recommend_rung", "rotation_window", "rotation_load",
                          "rotation_unload", "rotation_kv_save", "rotation_kv_restore"])
        for tool in tools:
            self.assertTrue(tool.__doc__, tool.__name__)
            sig = inspect.signature(tool)
            for name, param in sig.parameters.items():
                self.assertIsNot(param.annotation, inspect.Parameter.empty, f"{tool.__name__}.{name}")
            self.assertIsNot(sig.return_annotation, inspect.Signature.empty, tool.__name__)
        src = Path(R.__file__).read_text(encoding="utf-8")
        self.assertNotIn("hearth.kernel", src)

    def test_actuators_refuse_without_an_open_window(self) -> None:
        for out in (R.rotation_load("phi4-vk1", "nope"), R.rotation_unload("phi4-vk1", "nope"),
                    R.rotation_kv_save("phi4-vk1", 0, "p", "nope"), R.rotation_kv_restore("phi4-vk1", 0, "p", "nope")):
            self.assertFalse(out["ok"])
            self.assertEqual(out["error_code"], "no_open_window")
        self.assertEqual(self.client.loads, [])

    def test_actuators_refuse_production_ports(self) -> None:
        for port in (8082, 8083, 8084):
            out = R.rotation_load("phi4-vk1", "w", endpoint=f"http://127.0.0.1:{port}")
            self.assertEqual(out["error_code"], "production_port")

    def test_window_open_close_round_trip(self) -> None:
        opened = R.rotation_window("open", "rot-test-1", "unit", ["phi4-vk1"])
        self.assertTrue(opened["ok"])
        self.assertEqual(opened["event"]["event_type"], "assay.started")
        self.assertEqual(R.rotation_window("open", "rot-test-1")["ok"], False)
        status = R.rotation_status()
        self.assertEqual(status["open_windows"], ["rot-test-1"])
        closed = R.rotation_window("close", "rot-test-1", outcome="passed")
        self.assertTrue(closed["ok"])
        self.assertEqual(closed["event"]["event_type"], "assay.passed")
        self.assertEqual(R.rotation_status()["open_windows"], [])
        self.assertEqual(R.rotation_window("close", "rot-test-1")["error_code"], "no_open_window")

    def test_load_happy_path_writes_the_receipt_and_records_the_card(self) -> None:
        R.rotation_window("open", "rot-test-2", "unit", ["phi4-vk1"])
        out = R.rotation_load("phi4-vk1", "rot-test-2")
        self.assertTrue(out["ok"], out["reason"])
        self.assertEqual(out["entry_used"], "phi4-vk1")
        self.assertEqual(out["card_bdf"], A)
        self.assertEqual(out["placement"]["b70_with_weights"], 1)
        self.assertEqual(R.rotation_status()["last_load"]["entry_used"], "phi4-vk1")
        # production's per-card residency was charged on both cards before admission
        self.assertEqual(out["admission"]["numbers"]["cards"][B]["free_gb"], round(32.5 - 15.44 - 0.5, 3))

    def test_load_refuses_undeclared_and_production_models(self) -> None:
        R.rotation_window("open", "rot-test-3", "unit", [])
        self.assertEqual(R.rotation_load("not-a-model", "rot-test-3")["error_code"], "model_not_declared")
        out = R.rotation_load("qwen3-30b-a3b", "rot-test-3")
        self.assertIn(out["error_code"], ("model_not_declared", "production_model"))

    def test_load_refused_under_an_image_session(self) -> None:
        R.rotation_window("open", "rot-test-4", "unit", [])
        with patch.object(R, "_fence_fn", lambda: "imgsess_z"):
            out = R.rotation_load("phi4-vk1", "rot-test-4")
        self.assertFalse(out["ok"])
        self.assertIn("imgsess_z", out["reason"])
        self.assertEqual(self.client.loads, [])

    def test_unload_never_touches_production(self) -> None:
        R.rotation_window("open", "rot-test-5", "unit", [])
        self.assertEqual(R.rotation_unload("qwen3-30b-a3b", "rot-test-5")["error_code"], "production_model")
        out = R.rotation_unload("phi4-vk1", "rot-test-5")
        self.assertTrue(out["ok"])
        self.assertEqual(self.client.unloaded, ["phi4-vk1"])

    def test_status_is_never_an_exception(self) -> None:
        class Boom:
            def health(self):
                raise OSError("down")

        with patch.object(R, "_client_factory", lambda endpoint: Boom()):
            status = R.rotation_status()
        self.assertFalse(status["reachable"])
        self.assertIn("production", status)

    def test_recommend_rung_degrades_honestly_without_families(self) -> None:
        out = R.recommend_rung("quote_retrieval", 40_000)
        self.assertIn("ok", out)
        if not out["ok"]:
            self.assertIn("families", out["error"])
        else:
            self.assertEqual(out["task_family"], "quote_retrieval")


if __name__ == "__main__":
    unittest.main()
