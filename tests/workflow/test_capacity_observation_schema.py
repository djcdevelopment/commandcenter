"""Structural validation for capacity-observation.v1 documents.

No test previously validated capacity-observation docs against the schema. jsonschema is
NOT installed in this repo (never pip install) — "validation" here is the house pattern:
hand-rolled structural set-inclusion checks against the loaded schema JSON (required ⊆ keys,
keys ⊆ properties under additionalProperties:false, enum membership, declared JSON types),
same style as test_dispatch_capability.py and test_project_associations.py.

Stream C1: observed.physical now carries four RAW model_* sensor facts a collector can report
without any threshold, while model_residency is a DERIVED classification projected downstream.
These tests assert the raw facts validate and the schema shape is correct.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "contracts" / "capacity-observation.v1.schema.json").read_text(encoding="utf-8")
)

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _type_ok(value, declared) -> bool:
    types = declared if isinstance(declared, list) else [declared]
    for t in types:
        py = _JSON_TYPES.get(t)
        if py is None:
            continue
        # bool is a subclass of int in Python; keep integer/number distinct from boolean.
        if t in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def _object_problems(doc: dict, node_schema: dict, where: str) -> list:
    """Return a list of structural problems; empty list == structurally valid."""
    problems: list = []
    props = node_schema.get("properties", {})
    required = node_schema.get("required", [])

    missing = set(required) - set(doc)
    if missing:
        problems.append(f"{where}: missing required {sorted(missing)}")

    if node_schema.get("additionalProperties") is False:
        extra = set(doc) - set(props)
        if extra:
            problems.append(f"{where}: unknown keys {sorted(extra)}")

    for key, value in doc.items():
        spec = props.get(key)
        if spec is None:
            continue
        if "const" in spec and value != spec["const"]:
            problems.append(f"{where}.{key}: {value!r} != const {spec['const']!r}")
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"{where}.{key}: {value!r} not in enum {spec['enum']}")
        if "type" in spec and not _type_ok(value, spec["type"]):
            problems.append(f"{where}.{key}: {value!r} not of declared type {spec['type']}")
        if isinstance(value, dict) and "properties" in spec:
            problems.extend(_object_problems(value, spec, f"{where}.{key}"))
    return problems


def structural_problems(doc: dict) -> list:
    return _object_problems(doc, SCHEMA, "capacity-observation")


def make_observation(physical=None) -> dict:
    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": "obs_c1_test_001",
        "workflow_id": "wf_c1_test",
        "run_id": "run_c1_test",
        "timestamp": "2026-07-03T00:00:00Z",
        "builder_id": "omen-worker-1",
        "outcome": "success",
        "observed": {"runtime_s": 12.0, "physical": physical},
    }


class CapacityObservationSchemaTests(TestCase):
    def setUp(self) -> None:
        self.physical_props = (
            SCHEMA["properties"]["observed"]["properties"]["physical"]["properties"]
        )

    # ---- the validator has teeth (guards against a vacuous pass) --------------------

    def test_minimal_valid_observation_passes(self) -> None:
        self.assertEqual(structural_problems(make_observation()), [])

    def test_missing_required_field_is_caught(self) -> None:
        doc = make_observation()
        del doc["run_id"]
        self.assertTrue(structural_problems(doc))

    # ---- raw model_* facts validate (the stream's cases a and b) --------------------

    def test_raw_facts_with_null_residency_is_valid(self) -> None:
        physical = {
            "model_loaded_at_start": True,
            "model_load_count": 1,
            "model_unload_count": 0,
            "model_load_s": 3.2,
            "model_residency": None,
        }
        self.assertEqual(structural_problems(make_observation(physical)), [])

    def test_only_raw_facts_is_valid(self) -> None:
        physical = {
            "model_loaded_at_start": False,
            "model_load_count": 2,
            "model_unload_count": 1,
            "model_load_s": 5.5,
        }
        self.assertEqual(structural_problems(make_observation(physical)), [])

    def test_bogus_residency_value_is_caught(self) -> None:
        physical = {"model_residency": "hot"}
        problems = structural_problems(make_observation(physical))
        self.assertTrue(any("model_residency" in p for p in problems))

    def test_unknown_physical_key_is_caught(self) -> None:
        physical = {"gpu_vendor": "intel"}  # additionalProperties: false
        problems = structural_problems(make_observation(physical))
        self.assertTrue(any("unknown keys" in p for p in problems))

    def test_wrong_typed_raw_fact_is_caught(self) -> None:
        physical = {"model_load_count": "two"}  # must be integer|null
        problems = structural_problems(make_observation(physical))
        self.assertTrue(any("model_load_count" in p for p in problems))

    def test_boolean_is_not_accepted_as_integer_count(self) -> None:
        physical = {"model_load_count": True}  # bool must not satisfy integer
        problems = structural_problems(make_observation(physical))
        self.assertTrue(any("model_load_count" in p for p in problems))

    # ---- the four new raw fields exist with the exact nullable types (case c) --------

    def test_new_raw_fields_declared_with_exact_types(self) -> None:
        self.assertEqual(self.physical_props["model_loaded_at_start"]["type"], ["boolean", "null"])
        self.assertEqual(self.physical_props["model_load_count"]["type"], ["integer", "null"])
        self.assertEqual(self.physical_props["model_unload_count"]["type"], ["integer", "null"])
        self.assertEqual(self.physical_props["model_load_s"]["type"], ["number", "null"])

    def test_model_residency_marked_derived_and_enum_preserved(self) -> None:
        residency = self.physical_props["model_residency"]
        self.assertIn("DERIVED", residency["description"])
        # enum preserved, including the null member (removal would be breaking).
        self.assertIn(None, residency["enum"])
        for state in ("cold_load", "warm_resident", "evicted_mid_run"):
            self.assertIn(state, residency["enum"])

    def test_physical_additive_only_no_required_introduced(self) -> None:
        physical_schema = SCHEMA["properties"]["observed"]["properties"]["physical"]
        self.assertFalse(physical_schema.get("required"))
        self.assertIs(physical_schema["additionalProperties"], False)
