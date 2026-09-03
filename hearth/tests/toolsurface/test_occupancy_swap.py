"""probe_omen_swap (P6, ADR-0045): the llama-swap rung's occupancy behind the tenancy fence."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hearth.toolsurface.occupancy import _PROBES, probe_omen_swap


def _no_session():
    return patch("hearth.execution.coordination.GpuTenancyStore",
                 lambda: SimpleNamespace(active_image_session=lambda resource: None))


class ProbeOmenSwapTests(unittest.TestCase):
    def test_registered_for_the_omen_swap_rung(self) -> None:
        self.assertIn("omen-swap", _PROBES)
        self.assertIs(_PROBES["omen-swap"], probe_omen_swap)

    def test_unknown_when_llama_swap_is_not_listening(self) -> None:
        with _no_session():
            result = probe_omen_swap(fetch=lambda url, t: (None, "URLError: refused"))
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("unreachable", result["detail"])

    def test_available_with_nothing_resident_is_load_on_demand_capacity(self) -> None:
        with _no_session():
            result = probe_omen_swap(fetch=lambda url, t: ({"running": []}, None))
        self.assertEqual(result["occupancy"], "available")
        self.assertEqual(result["running"], [])

    def test_available_lists_resident_models(self) -> None:
        payload = {"running": [{"model": "qwen3-30b-a3b", "state": "ready"},
                               {"model": "phi4-vk1", "state": "ready"}]}
        with _no_session():
            result = probe_omen_swap(fetch=lambda url, t: (payload, None))
        self.assertEqual(result["occupancy"], "available")
        self.assertEqual(result["running"], ["qwen3-30b-a3b", "phi4-vk1"])

    def test_unknown_while_a_model_is_still_loading(self) -> None:
        payload = {"running": [{"model": "phi4-vk1", "state": "starting"}]}
        with _no_session():
            result = probe_omen_swap(fetch=lambda url, t: (payload, None))
        self.assertEqual(result["occupancy"], "unknown")
        self.assertIn("loading", result["detail"])

    def test_unknown_on_unexpected_shape(self) -> None:
        with _no_session():
            result = probe_omen_swap(fetch=lambda url, t: ({"nope": 1}, None))
        self.assertEqual(result["occupancy"], "unknown")

    def test_probe_hits_running_on_the_given_base_url(self) -> None:
        seen = []

        def fetch(url, t):
            seen.append(url)
            return {"running": []}, None

        with _no_session():
            probe_omen_swap(fetch=fetch, base_url="http://127.0.0.1:18299/")
        self.assertEqual(seen, ["http://127.0.0.1:18299/running"])

    def test_active_image_session_holds_the_rung_closed_before_any_http(self) -> None:
        session = SimpleNamespace(session_id="imgsess_x", epoch=3, state="imagegen")
        calls = []
        with patch("hearth.execution.coordination.GpuTenancyStore",
                   lambda: SimpleNamespace(active_image_session=lambda resource: session)):
            result = probe_omen_swap(fetch=lambda url, t: calls.append(url) or ({"running": []}, None))
        self.assertEqual(result["occupancy"], "busy")
        self.assertTrue(result["exclusive"])
        self.assertEqual(result["exclusive_reason"], "image_session_active")
        self.assertEqual(calls, [])

    def test_unreadable_tenancy_store_fails_closed_and_says_so(self) -> None:
        def boom():
            raise OSError("locked")

        with patch("hearth.execution.coordination.GpuTenancyStore", boom):
            result = probe_omen_swap(fetch=lambda url, t: ({"running": []}, None))
        self.assertEqual(result["occupancy"], "unknown")
        self.assertTrue(result["exclusive"])
        self.assertEqual(result["exclusive_reason"], "tenancy_probe_failed")


if __name__ == "__main__":
    unittest.main()
