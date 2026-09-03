"""The omen-swap rung as declared in the real hearth/etc/backends.toml (P6, ADR-0045)."""
from __future__ import annotations

import unittest

from hearth.toolsurface.backends import load_pool, select_backend, BackendRoutingRefusal


class OmenSwapRungTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = load_pool()
        self.rung = self.pool.by_name("omen-swap")

    def test_declared_pin_only_on_loopback_8081(self) -> None:
        self.assertEqual(self.rung.endpoint, "http://127.0.0.1:8081")
        self.assertEqual(self.rung.api, "openai")
        self.assertEqual(self.rung.tags, ())
        self.assertFalse(self.rung.retired)
        self.assertEqual(self.rung.settings.get("node"), "omen")
        self.assertEqual(self.rung.settings.get("lifecycle"), "llama-swap")

    def test_context_bytes_is_the_minimum_member_arithmetic(self) -> None:
        # ADR-0031: MIN over members = -c 4096 x -np 1 x 3.5 B/token.
        self.assertEqual(self.rung.context_bytes(), 14336)

    def test_one_endpoint_serves_several_model_ids(self) -> None:
        """The ADR-0027 gate-2 shape: a second model_id on one rung."""
        self.assertGreaterEqual(len(self.rung.models), 2)
        for model in ("phi4-vk1", "phi4-vk2", "qwen14b-vk1", "qwen38-27b-dual"):
            self.assertIn(model, self.rung.models)
            self.assertEqual([b.name for b in self.pool.by_model(model)], ["omen-swap"])

    def test_production_rung_is_untouched(self) -> None:
        arc = self.pool.by_name("omen-arc")
        self.assertEqual(arc.endpoint, "http://127.0.0.1:8082")
        self.assertEqual(self.pool.default, "omen-arc")

    def test_pin_with_a_declared_model_resolves_without_an_occupancy_probe(self) -> None:
        backend, reason, _occ = select_backend(
            self.pool, backend="omen-swap", model="qwen14b-vk1", occupancy_check=None)
        self.assertEqual(backend.name, "omen-swap")
        self.assertEqual(reason, "pinned:omen-swap")

    def test_pin_over_budget_is_refused_at_the_door(self) -> None:
        with self.assertRaises(BackendRoutingRefusal):
            select_backend(self.pool, backend="omen-swap", model="phi4-vk1",
                           payload_bytes=40_000, occupancy_check=None)


if __name__ == "__main__":
    unittest.main()
