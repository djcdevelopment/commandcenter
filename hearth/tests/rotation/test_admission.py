"""Admission gate (P4): bytes-per-card fit, commit floor, thermal; unknown telemetry refuses."""
from __future__ import annotations

import unittest

from hearth.rotation.admission import (AdmissionGates, CardState, admit_load, choose_target_bdf,
                                       eviction_advice)

G = AdmissionGates()
A = "0000:04:00.0"
B = "0000:09:00.0"


def cards(temp_a=52.0, temp_b=50.0, res_a=14.52, res_b=15.44, infer_a=False):
    return [CardState(A, 32.5, res_a, temp_a, infer_a), CardState(B, 32.5, res_b, temp_b)]


class AdmitTests(unittest.TestCase):
    def test_phi4_single_fits_beside_production_on_the_freer_card(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=cards(),
                         commit_free_gb=39.0, gates=G, model_id="phi4-vk1")
        self.assertTrue(out.ok, out.reasons)
        self.assertEqual(out.card_bdf, A)          # 32.5-14.52-0.5 = 17.48 free > 16.56 on B

    def test_27b_dual_fits_beside_production(self) -> None:
        out = admit_load(per_card_gb=None, vram_gb=17.67, placement="dual", cards=cards(),
                         commit_free_gb=39.0, gates=G, model_id="qwen38-27b-dual")
        self.assertTrue(out.ok, out.reasons)
        self.assertIsNone(out.card_bdf)
        self.assertAlmostEqual(out.numbers["per_card_need_gb"], 8.835)

    def test_27b_single_does_not_fit_beside_production(self) -> None:
        out = admit_load(per_card_gb=None, vram_gb=17.67, placement="single", cards=cards(),
                         commit_free_gb=39.0, gates=G)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason_code, "vram_fit")

    def test_commit_floor_refuses(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=cards(),
                         commit_free_gb=5.9, gates=G)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason_code, "commit_floor")

    def test_thermal_abort_refuses(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=cards(temp_a=96.0),
                         commit_free_gb=39.0, gates=G)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason_code, "thermal")

    def test_unknown_temperature_or_commit_refuses_fail_closed(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=cards(temp_b=None),
                         commit_free_gb=39.0, gates=G)
        self.assertEqual(out.reason_code, "telemetry_unknown")
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=cards(),
                         commit_free_gb=None, gates=G)
        self.assertEqual(out.reason_code, "telemetry_unknown")

    def test_unmeasured_model_is_not_admitted(self) -> None:
        out = admit_load(per_card_gb=None, vram_gb=None, placement="single", cards=cards(),
                         commit_free_gb=39.0, gates=G, model_id="mystery")
        self.assertEqual(out.reason_code, "model_unknown")

    def test_no_cards_refuses(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single", cards=[],
                         commit_free_gb=39.0, gates=G)
        self.assertEqual(out.reason_code, "cards_missing")

    def test_inferring_neighbour_is_reported_not_gated(self) -> None:
        out = admit_load(per_card_gb=8.3, vram_gb=None, placement="single",
                         cards=cards(res_a=14.52, res_b=15.44, infer_a=True),
                         commit_free_gb=39.0, gates=G)
        self.assertTrue(out.ok)
        self.assertEqual(out.card_bdf, A)
        self.assertTrue(any("inferring" in r for r in out.reasons))


class ChooseTests(unittest.TestCase):
    def test_ties_go_to_the_cooler_card(self) -> None:
        tie = [CardState(A, 32.5, 15.0, 60.0), CardState(B, 32.5, 15.0, 50.0)]
        self.assertEqual(choose_target_bdf(tie, 8.3, G), B)

    def test_none_when_nothing_fits(self) -> None:
        self.assertIsNone(choose_target_bdf(cards(), 20.0, G))


class EvictionAdviceTests(unittest.TestCase):
    def test_never_names_production_or_in_flight(self) -> None:
        resident = [("qwen3-30b-a3b", A, 14.52), ("phi4-vk1", A, 8.3), ("qwen14b-vk1", A, 8.4)]
        full = [CardState(A, 32.5, 14.52 + 8.3 + 8.4, 50.0), CardState(B, 32.5, 15.44, 50.0)]
        advice = eviction_advice(resident=resident, need_gb=13.3, placement="single", cards=full,
                                 gates=G, in_flight=frozenset({"qwen14b-vk1"}))
        names = [a.model_id for a in advice]
        self.assertNotIn("qwen3-30b-a3b", names)
        self.assertNotIn("qwen14b-vk1", names)
        self.assertEqual(names, ["phi4-vk1"])


if __name__ == "__main__":
    unittest.main()
