from __future__ import annotations

from unittest import TestCase

from fleet.mode_arbiter import (
    GIB, MIB, SAFE_ENVELOPE_BYTES, SAFETY_BUFFER_BYTES,
    Card, admit, estimate_vram_bytes, select_card,
)


class EstimatorTests(TestCase):
    def test_counter_prompt_cfg_doubles_the_conditioning_term(self):
        # The whole point: negative prompt + CFG runs a 2nd conditioning pass.
        off = estimate_vram_bytes(6 * GIB, 1024, 1024, cfg_enabled=False)
        on = estimate_vram_bytes(6 * GIB, 1024, 1024, cfg_enabled=True)
        # exactly one extra conditioning pass more with CFG on
        from fleet.mode_arbiter import VramModel
        self.assertEqual(on - off, VramModel().conditioning_bytes)
        self.assertGreater(on, off)

    def test_bigger_resolution_costs_more(self):
        small = estimate_vram_bytes(6 * GIB, 512, 512, cfg_enabled=True)
        big = estimate_vram_bytes(6 * GIB, 1536, 1536, cfg_enabled=True)
        self.assertGreater(big, small)

    def test_weights_are_included(self):
        light = estimate_vram_bytes(2 * GIB, 1024, 1024, cfg_enabled=False)
        heavy = estimate_vram_bytes(12 * GIB, 1024, 1024, cfg_enabled=False)
        self.assertEqual(heavy - light, 10 * GIB)

    def test_invalid_dims_rejected(self):
        with self.assertRaises(ValueError):
            estimate_vram_bytes(6 * GIB, 0, 1024, cfg_enabled=True)


class AdmitTests(TestCase):
    def _card(self, free_gib, known=True):
        return Card(luid="luid_A", free_dedicated_bytes=int(free_gib * GIB), known_physical=known)

    def test_admits_when_fits_with_buffer(self):
        a = admit(8 * GIB, self._card(9))
        self.assertTrue(a.ok)
        self.assertEqual(a.card_luid, "luid_A")

    def test_rejects_over_safe_envelope(self):
        a = admit(SAFE_ENVELOPE_BYTES + 1, self._card(64))
        self.assertFalse(a.ok)
        self.assertIn("safe envelope", a.reason)

    def test_rejects_insufficient_headroom_incl_buffer(self):
        # estimate 8 GiB needs 8 GiB + 512 MiB; card has only 8 GiB free
        a = admit(8 * GIB, self._card(8))
        self.assertFalse(a.ok)
        self.assertIn("headroom", a.reason)

    def test_rejects_unknown_physical_card(self):
        a = admit(4 * GIB, self._card(64, known=False))
        self.assertFalse(a.ok)
        self.assertIn("not a known physical card", a.reason)

    def test_blind_telemetry_fails_closed(self):
        a = admit(4 * GIB, Card(luid="x", free_dedicated_bytes=-1))
        self.assertFalse(a.ok)
        self.assertIn("fail closed", a.reason)

    def test_insane_estimate_rejected(self):
        self.assertFalse(admit(0, self._card(64)).ok)


class SelectCardTests(TestCase):
    def test_picks_card_with_most_headroom_after(self):
        # oxen holds card A tightly; card B is emptier -> B wins (worst-fit, safest)
        a = Card("luid_A", free_dedicated_bytes=9 * GIB)   # after 8+0.5 -> 0.5 GiB left
        b = Card("luid_B", free_dedicated_bytes=14 * GIB)  # after 8+0.5 -> 5.5 GiB left
        res = select_card([a, b], 8 * GIB)
        self.assertTrue(res.ok)
        self.assertEqual(res.card_luid, "luid_B")

    def test_rejects_when_no_card_fits(self):
        a = Card("luid_A", free_dedicated_bytes=2 * GIB)
        b = Card("luid_B", free_dedicated_bytes=3 * GIB)
        res = select_card([a, b], 8 * GIB)
        self.assertFalse(res.ok)
        self.assertIn("no card has budget", res.reason)

    def test_selects_the_only_fitting_card(self):
        full = Card("luid_A", free_dedicated_bytes=1 * GIB)      # oxen-loaded
        free = Card("luid_B", free_dedicated_bytes=12 * GIB)
        res = select_card([full, free], 8 * GIB)
        self.assertTrue(res.ok)
        self.assertEqual(res.card_luid, "luid_B")

    def test_over_envelope_rejected_even_if_card_huge(self):
        huge = Card("luid_A", free_dedicated_bytes=64 * GIB)
        res = select_card([huge], SAFE_ENVELOPE_BYTES + GIB)
        self.assertFalse(res.ok)


class PlanModeTests(TestCase):
    def _cards(self):
        # card A holds a resident LLM (oxen critic ~9 GiB) -> ~7 GiB free-dedicated;
        # card B is idle -> ~14 GiB free-dedicated (of the 16 GiB envelope).
        return [Card("luid_A", 7 * GIB), Card("luid_B", 14 * GIB)]

    def test_art_mode_coexists_with_pinned_oxen(self):
        from fleet.mode_arbiter import Mode, Workload, plan_mode
        mode = Mode("art", (
            Workload("oxen-critic", per_card_estimate=2 * GIB, pinned_luid="luid_A"),
            Workload("art-sd35", per_card_estimate=8 * GIB),  # arbiter places it
        ))
        plan = plan_mode(mode, self._cards())
        self.assertTrue(plan["admissible"])
        by_name = {p["workload"]: p for p in plan["placements"]}
        self.assertEqual(by_name["oxen-critic"]["card"], "luid_A")   # stayed pinned
        self.assertEqual(by_name["art-sd35"]["card"], "luid_B")      # placed off the busy card

    def test_mode_that_does_not_fit_is_inadmissible(self):
        from fleet.mode_arbiter import Mode, Workload, plan_mode
        mode = Mode("greedy", (
            Workload("a", per_card_estimate=12 * GIB),
            Workload("b", per_card_estimate=12 * GIB),
            Workload("c", per_card_estimate=12 * GIB),  # only two cards -> c fails
        ))
        plan = plan_mode(mode, self._cards())
        self.assertFalse(plan["admissible"])
        self.assertTrue(any(not p["ok"] for p in plan["placements"]))

    def test_pin_to_unknown_card_fails_closed(self):
        from fleet.mode_arbiter import Mode, Workload, plan_mode
        mode = Mode("bad", (Workload("x", 2 * GIB, pinned_luid="luid_ZZ"),))
        plan = plan_mode(mode, self._cards())
        self.assertFalse(plan["admissible"])
        self.assertIn("unknown", plan["placements"][0]["reason"])
