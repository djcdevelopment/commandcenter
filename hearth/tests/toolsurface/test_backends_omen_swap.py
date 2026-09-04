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
        for model in ("phi4-vk1", "phi4-vk0", "qwen14b-vk1", "qwen38-27b-dual"):
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


class PerMemberContextBudgetTests(unittest.TestCase):
    """Decided 2026-09-04: a pin is judged against its OWN member's budget.

    The rung MIN (14336, from the -c 4096 members) is honest arithmetic and the wrong
    policy on a multi-model rung -- it refused 5 of 8 doc-bench tasks for a phi4-vk1
    pin although phi-4 runs -c 8192. Undeclared members and every other rung keep the
    rung-level number, so this changes routing nowhere else.
    """

    def setUp(self) -> None:
        self.pool = load_pool()
        self.rung = self.pool.by_name("omen-swap")

    def test_declared_member_gets_its_own_budget(self) -> None:
        self.assertEqual(self.rung.context_bytes("phi4-vk1"), 28672)      # -c 8192
        self.assertEqual(self.rung.context_bytes("qwen14b-vk0"), 28672)
        self.assertEqual(self.rung.context_bytes("qwen38-27b-dual"), 114688)  # -c 32768
        self.assertEqual(self.rung.context_budget_scope("phi4-vk1"), "model")

    def test_small_members_keep_the_min(self) -> None:
        self.assertEqual(self.rung.context_bytes("gptoss20b-vk1"), 14336)  # -c 4096
        self.assertEqual(self.rung.context_bytes("mistral24b-vk0"), 14336)

    def test_undeclared_model_falls_back_to_the_rung_value(self) -> None:
        self.assertEqual(self.rung.context_bytes("not-a-member"), 14336)
        self.assertEqual(self.rung.context_budget_scope("not-a-member"), "rung")

    def test_no_model_named_is_still_the_rung_value(self) -> None:
        self.assertEqual(self.rung.context_bytes(), 14336)

    def test_other_rungs_are_unaffected_whether_or_not_a_model_is_named(self) -> None:
        arc = self.pool.by_name("omen-arc")
        self.assertEqual(arc.context_bytes(), arc.context_bytes("qwen3-30b-a3b"))
        self.assertEqual(arc.context_budget_scope("qwen3-30b-a3b"), "rung")

    def test_the_band_the_rung_min_used_to_refuse_is_now_admitted(self) -> None:
        # 28,000 B: over the rung MIN (14336), inside phi-4's own 28672. This is the
        # band that cost the M1 pour 5 of its 8 tasks.
        backend, reason, _occ = select_backend(
            self.pool, backend="omen-swap", model="phi4-vk1",
            payload_bytes=28_000, occupancy_check=None)
        self.assertEqual(backend.name, "omen-swap")
        self.assertEqual(reason, "pinned:omen-swap")

    def test_a_small_member_still_refuses_in_that_same_band(self) -> None:
        with self.assertRaises(BackendRoutingRefusal) as caught:
            select_backend(self.pool, backend="omen-swap", model="gptoss20b-vk1",
                           payload_bytes=28_000, occupancy_check=None)
        attempted = caught.exception.attempted[0]
        self.assertEqual(attempted["context_bytes"], 14336)
        self.assertEqual(attempted["budget_scope"], "model")
        self.assertEqual(attempted["model"], "gptoss20b-vk1")

    def test_refusal_receipt_names_which_budget_turned_it_away(self) -> None:
        with self.assertRaises(BackendRoutingRefusal) as caught:
            select_backend(self.pool, backend="omen-swap", model="phi4-vk1",
                           payload_bytes=40_000, occupancy_check=None)
        attempted = caught.exception.attempted[0]
        self.assertEqual(attempted["context_bytes"], 28672)
        self.assertEqual(attempted["budget_scope"], "model")

    def test_declared_budgets_match_the_yaml_command_lines(self) -> None:
        """The numbers are derived from omen.yaml -- drift there must not go unnoticed."""
        import re
        from pathlib import Path
        text = Path("fleet/arcserve/llama-swap/omen.yaml").read_text(encoding="utf-8")
        declared = self.rung.settings["context_bytes_by_model"]
        for entry, budget in declared.items():
            start = text.find(f'"{entry}":')
            self.assertNotEqual(start, -1, f"{entry} is declared but absent from omen.yaml")
            nxt = text.find('\n  "', start + 1)
            block = text[start:nxt if nxt != -1 else len(text)]
            ctx = re.search(r"-c (\d+)", block)
            self.assertIsNotNone(ctx, f"{entry} has no -c in omen.yaml")
            self.assertEqual(int(int(ctx.group(1)) * 3.5), budget,
                             f"{entry}: budget {budget} does not match -c {ctx.group(1)} x 3.5")
            self.assertNotIn('"', block[len(f'"{entry}":'):].split("cmd")[0],
                             f"{entry}: block parse overran into another entry")


class AuthFaultDoesNotEscalateTests(unittest.TestCase):
    """A missing local token must not buy cloud tokens.

    Flagged by an external audit 2026-09-04 and confirmed in the source: a NON-pinned
    dispatch whose local rung has no auth token fails, and A2 escalation then climbs to
    the next rung -- producing routed_by "escalation:omen-arc->gcp-gemini" and spending
    trial credit to paper over an unset env var in the caller's shell. A pin already
    errored cleanly; only the routed path leaked. `test_connection_failure_returns_
    result_not_exception` in test_inference.py had been passing BECAUSE of this climb.
    """

    def test_missing_token_is_classified_so_escalation_can_refuse_it(self) -> None:
        from hearth.toolsurface.inference import _Target, _generate_openai
        target = _Target(endpoint="http://127.0.0.1:8082", api="openai", auth_token=None,
                         auth_error=None, auth_env="OMEN_ARC_TOKEN", backend="omen-arc",
                         routed_by="default", occupancy="available", settings={})
        result = _generate_openai(target, "hi", "qwen3-30b-a3b", None, 8, 30)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "auth_not_configured")
        self.assertIn("with-gateway-env.cmd", result["error"])


if __name__ == "__main__":
    unittest.main()
