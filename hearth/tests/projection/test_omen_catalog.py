"""P1: the OMEN catalog validates against its frozen contract and every measured
number carries a receipt. Also proves the un-generalized am4 loader ignores it."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

import jsonschema

from hearth.scheduler.ontology import load_am4_catalog

REPO = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO / "hearth" / "contracts" / "omen-catalog.v1.schema.json"
CATALOG_PATH = REPO / "knowledge" / "omen_catalog.json"

MEASURED_FIELDS = (
    "load_s_steady",
    "load_s_first_in_window",
    "expected_gen_tps",
    "per_card_gb",
    "vram_gb",
    "kv_save_s",
    "kv_restore_s",
    "prefill_tps",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class OmenCatalogContractTests(TestCase):
    def setUp(self) -> None:
        self.schema = _load(SCHEMA_PATH)
        self.catalog = _load(CATALOG_PATH)
        self.validator = jsonschema.Draft7Validator(self.schema)

    def test_schema_is_draft07_and_self_consistent(self) -> None:
        jsonschema.Draft7Validator.check_schema(self.schema)
        self.assertEqual(self.schema["properties"]["contract_version"], {"const": "omen-catalog.v1"})
        self.assertEqual(
            set(self.schema["required"]),
            {"contract_version", "gathered_at", "host", "gates", "cards", "models"},
        )

    def test_committed_catalog_validates(self) -> None:
        errors = sorted(self.validator.iter_errors(self.catalog), key=lambda e: list(e.path))
        self.assertEqual(errors, [], "\n".join(e.message for e in errors))
        self.assertEqual(self.catalog["contract_version"], "omen-catalog.v1")
        self.assertEqual(self.catalog["host"], "omen")
        self.assertEqual(len(self.catalog["cards"]), 2)
        self.assertEqual(len(self.catalog["models"]), 6)

    def test_every_measured_number_has_a_receipt(self) -> None:
        for model in self.catalog["models"]:
            receipts = model.get("receipts") or {}
            for field in MEASURED_FIELDS:
                value = model.get(field)
                if value is None:
                    continue
                self.assertIn(
                    field, receipts,
                    f"{model['model_id']}.{field}={value} has no receipt",
                )
                self.assertTrue(receipts[field].strip(), f"{model['model_id']}.{field} receipt is blank")

    def test_no_llama_bench_numbers(self) -> None:
        blob = json.dumps(self.catalog).lower()
        self.assertNotIn("llama-bench", blob)
        self.assertNotIn("llama_bench", blob)

    def test_wrong_contract_fails_validation(self) -> None:
        bad = dict(self.catalog)
        bad["contract_version"] = "am4-catalog.v1"
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(bad)

    def test_unknown_top_level_key_fails_validation(self) -> None:
        bad = dict(self.catalog)
        bad["surprise"] = 1
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(bad)

    def test_visible_devices_must_be_null(self) -> None:
        bad = json.loads(json.dumps(self.catalog))
        bad["models"][0]["visible_devices"] = "0,1"
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(bad)

    def test_am4_loader_degrades_to_empty_on_omen_contract(self) -> None:
        loaded = load_am4_catalog(str(CATALOG_PATH))
        self.assertEqual(loaded, {"models": {}, "gates": None, "cards": None})

    def test_am4_loader_would_read_it_under_its_own_contract(self) -> None:
        # Sanity: the shape is loader-compatible; only the contract string gates it.
        doc = json.loads(json.dumps(self.catalog))
        doc["contract_version"] = "am4-catalog.v1"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "omen_catalog.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            loaded = load_am4_catalog(str(p))
        self.assertIn("qwen3-30b-a3b", loaded["models"])
        self.assertEqual(loaded["models"]["qwen3-30b-a3b"].placement, "dual")
