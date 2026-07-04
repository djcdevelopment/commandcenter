from __future__ import annotations

from unittest import TestCase

from hearth.projection.am4_catalog import build_catalog

MODELS_JSON = {
    "safety": {
        "max_host_used_gb_preflight": 22.0,
        "telemetry_settle_sec": 10,
        "health_timeout_sec": 180,
        "verdict_gate": True,
    },
    "models": {
        "qwen2.5-14b-q4": {
            "file": "qwen2.5-14b-instruct-q4_K_M.gguf",
            "placement": "single",
            "visible_devices": "0",
            "alias": "qwen2.5-14b-q4",
            "expected_gen_tps": 46.81,
            "note": "8.4 GB single-card territory; canonical M1 parity model.",
        },
        "qwen3-30b-a3b-128k": {
            "file": "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
            "placement": "dual",
            "visible_devices": "0,1",
            "alias": "qwen3-30b-a3b-128k",
            "expected_gen_tps": 81.7,
            "note": "MoE 17.3 GB, 256k native. Dual layer-split across cards 0,1.",
        },
        "mystery-model": {
            "file": "mystery.gguf",
            "placement": "single",
            "visible_devices": "0",
            "alias": None,
            "expected_gen_tps": None,
            "note": "no parsable size here",
        },
    },
}


def _manifest(model_path: str, wall_ms) -> dict:
    doc = {"config_name": "run", "model_path": model_path}
    if wall_ms is not None:
        doc["warmup"] = {"wall_ms": wall_ms}
    return doc


class BuildCatalogTests(TestCase):
    def test_contract_shape_and_gates(self) -> None:
        catalog = build_catalog(MODELS_JSON, [], host="am4", gathered_at="2026-07-04T00:00:00+00:00")
        self.assertEqual(catalog["contract_version"], "am4-catalog.v1")
        self.assertEqual(catalog["host"], "am4")
        self.assertEqual(catalog["gathered_at"], "2026-07-04T00:00:00+00:00")
        self.assertEqual(catalog["gates"], {
            "max_host_used_gb_preflight": 22.0,
            "telemetry_settle_sec": 10,
            "health_timeout_sec": 180,
            "verdict_gate": True,
        })
        self.assertEqual(catalog["cards"], [
            {"index": 0, "vram_gb": 32.0},
            {"index": 1, "vram_gb": 32.0},
        ])
        self.assertEqual(len(catalog["models"]), 3)

    def test_placement_and_vram_parse_single(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        by_id = {m["model_id"]: m for m in catalog["models"]}
        m = by_id["qwen2.5-14b-q4"]
        self.assertEqual(m["placement"], "single")
        self.assertEqual(m["vram_gb"], 8.4)
        self.assertEqual(m["per_card_gb"], 8.4)
        self.assertEqual(m["visible_devices"], "0")
        self.assertEqual(m["alias"], "qwen2.5-14b-q4")
        self.assertEqual(m["expected_gen_tps"], 46.81)

    def test_placement_and_vram_parse_dual_splits_per_card(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        by_id = {m["model_id"]: m for m in catalog["models"]}
        m = by_id["qwen3-30b-a3b-128k"]
        self.assertEqual(m["placement"], "dual")
        self.assertEqual(m["vram_gb"], 17.3)
        self.assertAlmostEqual(m["per_card_gb"], 8.65)

    def test_unparseable_note_is_null_not_fatal(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        by_id = {m["model_id"]: m for m in catalog["models"]}
        m = by_id["mystery-model"]
        self.assertIsNone(m["vram_gb"])
        self.assertIsNone(m["per_card_gb"])
        self.assertIsNone(m["expected_gen_tps"])
        self.assertIsNone(m["alias"])

    def test_warmup_aggregation_p50_and_max(self) -> None:
        manifests = [
            _manifest("D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf", 1517),
            _manifest("D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf", 1524),
            _manifest("D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf", 28384),
        ]
        catalog = build_catalog(MODELS_JSON, manifests)
        by_id = {m["model_id"]: m for m in catalog["models"]}
        m = by_id["qwen2.5-14b-q4"]
        self.assertEqual(m["sample_count"], 3)
        self.assertEqual(m["warmup_ms_max"], 28384)
        self.assertEqual(m["warmup_ms_p50"], 1524.0)

    def test_manifest_missing_warmup_field_is_skipped(self) -> None:
        manifests = [
            {"config_name": "no-warmup", "model_path": "D:\\...\\qwen2.5-14b-instruct-q4_K_M.gguf"},
            _manifest("D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf", 1500),
        ]
        catalog = build_catalog(MODELS_JSON, manifests)
        by_id = {m["model_id"]: m for m in catalog["models"]}
        m = by_id["qwen2.5-14b-q4"]
        self.assertEqual(m["sample_count"], 1)
        self.assertEqual(m["warmup_ms_p50"], 1500)

    def test_no_manifests_at_all_yields_null_warmups(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        for m in catalog["models"]:
            self.assertIsNone(m["warmup_ms_p50"])
            self.assertIsNone(m["warmup_ms_max"])
            self.assertEqual(m["sample_count"], 0)

    def test_empty_models_json_is_tolerated(self) -> None:
        catalog = build_catalog({}, [])
        self.assertEqual(catalog["models"], [])
        self.assertEqual(catalog["gates"], {
            "max_host_used_gb_preflight": None,
            "telemetry_settle_sec": None,
            "health_timeout_sec": None,
            "verdict_gate": None,
        })

    def test_manifest_for_unknown_file_is_ignored(self) -> None:
        manifests = [_manifest("D:\\work\\battlemage\\models\\not-in-catalog.gguf", 999)]
        catalog = build_catalog(MODELS_JSON, manifests)
        for m in catalog["models"]:
            self.assertEqual(m["sample_count"], 0)
