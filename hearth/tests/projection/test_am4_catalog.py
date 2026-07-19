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


# Mirrors the real ~/baseline/serve-moe.sh flags (2026-07-18-oxen-moe-gpt-oss-120b.md);
# deliberately includes --metrics / -ctk / --n-cpu-moe so the short-flag regexes
# prove they don't false-match inside longer flags.
SERVE_SCRIPT = (
    "llama-server -m /mnt/data4tb/models/gpt-oss-120b-MXFP4.gguf --alias gpt-oss-120b "
    "-ngl 99 -dev SYCL0,SYCL1 -sm layer -ts 1,1 --n-cpu-moe 4 "
    "-fa on -fit off -ctk q8_0 -ctv q8_0 -c 65536 -np 4 "
    "--slots --metrics --jinja --host 0.0.0.0 --port 8082 --api-key $AM4_OXEN_TOKEN"
)

MODELS_API = {"object": "list", "data": [{"id": "gpt-oss-120b", "object": "model"}]}

CAPACITY_FACTS = {
    "generated": "2026-07-18",
    "facts": [
        {"backend": "am4-moe", "metric": "aggregate_decode_ceiling", "value": 26, "unit": "tok/s"},
        {"backend": "am4-moe", "metric": "solo_decode_rate", "value": 29, "unit": "tok/s"},
        {"backend": "am4-moe", "metric": "prefill_rate", "value": 190, "unit": "tok/s"},
        {"backend": "am4-moe", "metric": "cold_load_time", "value": 3.2, "unit": "min"},
        {"backend": "am4-host", "metric": "vram_per_card", "value": 31023, "unit": "MiB"},
    ],
}

LLAMA_SERVER = {
    "unit_text": "# b70-moe.service\nExecStart=%h/baseline/serve-moe.sh\n",
    "serve_script": SERVE_SCRIPT,
    "unit_active": "active",
    "models_api": MODELS_API,
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


class LlamaServerModelsTests(TestCase):
    def _entry(self, catalog: dict) -> dict:
        by_id = {m["model_id"]: m for m in catalog["models"]}
        return by_id["gpt-oss-120b"]

    def test_no_llama_server_signals_changes_nothing(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        self.assertNotIn("gpt-oss-120b", {m["model_id"] for m in catalog["models"]})
        self.assertEqual(len(catalog["models"]), 3)

    def test_vllama_entries_are_tagged_with_source(self) -> None:
        catalog = build_catalog(MODELS_JSON, [])
        for m in catalog["models"]:
            self.assertEqual(m["served_by"], "vllama")

    def test_full_signals_yield_v1_entry_with_extras(self) -> None:
        catalog = build_catalog(MODELS_JSON, [], llama_server=LLAMA_SERVER,
                                capacity_facts=CAPACITY_FACTS)
        m = self._entry(catalog)
        self.assertEqual(m["alias"], "gpt-oss-120b")
        self.assertEqual(m["placement"], "dual")
        self.assertEqual(m["visible_devices"], "0,1")
        self.assertEqual(m["per_card_gb"], 30.3)
        self.assertEqual(m["vram_gb"], 60.6)
        self.assertEqual(m["expected_gen_tps"], 29.0)
        self.assertEqual(m["warmup_ms_p50"], 192000.0)
        self.assertEqual(m["warmup_ms_max"], 192000.0)
        self.assertEqual(m["sample_count"], 1)
        self.assertEqual(m["served_by"], "llama-server")
        self.assertEqual(m["port"], 8082)
        self.assertTrue(m["serving"])
        self.assertEqual(m["slots"], 4)
        self.assertEqual(m["n_ctx"], 65536)
        self.assertEqual(m["prefill_tps"], 190.0)
        self.assertEqual(m["goodput_tps"], 26.0)
        # every frozen-required v1 field must be present
        for field in ("model_id", "alias", "placement", "visible_devices", "vram_gb",
                      "per_card_gb", "expected_gen_tps", "warmup_ms_p50",
                      "warmup_ms_max", "sample_count", "notes"):
            self.assertIn(field, m)

    def test_models_api_alone_is_enough(self) -> None:
        catalog = build_catalog(MODELS_JSON, [],
                                llama_server={"models_api": MODELS_API})
        m = self._entry(catalog)
        self.assertEqual(m["placement"], "dual")
        self.assertTrue(m["serving"])
        self.assertIsNone(m["expected_gen_tps"])
        self.assertEqual(m["sample_count"], 0)

    def test_inactive_unit_without_api_reports_not_serving(self) -> None:
        catalog = build_catalog(MODELS_JSON, [], llama_server={
            "serve_script": SERVE_SCRIPT, "unit_active": "inactive", "models_api": None})
        m = self._entry(catalog)
        self.assertIs(m["serving"], False)

    def test_no_facts_leaves_perf_null(self) -> None:
        catalog = build_catalog(MODELS_JSON, [], llama_server=LLAMA_SERVER)
        m = self._entry(catalog)
        self.assertIsNone(m["expected_gen_tps"])
        self.assertIsNone(m["prefill_tps"])
        self.assertIsNone(m["goodput_tps"])
        self.assertIsNone(m["per_card_gb"])
        self.assertIsNone(m["warmup_ms_p50"])

    def test_single_device_parses_as_single_placement(self) -> None:
        script = SERVE_SCRIPT.replace("-dev SYCL0,SYCL1", "-dev SYCL1")
        catalog = build_catalog(MODELS_JSON, [],
                                llama_server={"serve_script": script},
                                capacity_facts=CAPACITY_FACTS)
        m = self._entry(catalog)
        self.assertEqual(m["placement"], "single")
        self.assertEqual(m["visible_devices"], "1")
        self.assertEqual(m["vram_gb"], 30.3)
