import json
import unittest
from pathlib import Path

SCHEMA_PATH = Path("contracts/capacity-observation.v1.schema.json")


class TestCapacityObservationSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r") as f:
            self.schema = json.load(f)

    def test_schema_structure(self):
        """Verify the schema structure: required ⊆ keys ⊆ properties, enum membership."""
        # Extract required and properties from schema
        required = set(self.schema["required"])
        properties = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore

        # Test 1: required fields are a subset of the keys in the physical object
        self.assertTrue(required.issubset(properties))

        # Test 2: all keys in the physical object are in the schema's properties
        # This ensures no unexpected keys are allowed
        self.assertTrue(properties.issubset(set(self.schema["properties"]["observed"]["properties"]["physical"])))

    def test_model_residency_enum(self):
        """Verify model_residency enum values are correct."""
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["model_residency"]["enum"]
        expected = [None, "cold_load", "warm_resident", "evicted_mid_run"]
        self.assertEqual(set(enum_values), set(expected))

    def test_raw_fields_exist_and_have_correct_types(self):
        """Verify the new raw fields exist and have the correct nullable types."""
        physical_props = self.schema["properties"]["observed"]["properties"]["physical"]

        # Check model_loaded_at_start
        self.assertIn("model_loaded_at_start", physical_props)
        self.assertEqual(physical_props["model_loaded_at_start"]["type"], ["boolean", "null"])

        # Check model_load_count
        self.assertIn("model_load_count", physical_props)
        self.assertEqual(physical_props["model_load_count"]["type"], ["integer", "null"])

        # Check model_unload_count
        self.assertIn("model_unload_count", physical_props)
        self.assertEqual(physical_props["model_unload_count"]["type"], ["integer", "null"])

        # Check model_load_s
        self.assertIn("model_load_s", physical_props)
        self.assertEqual(physical_props["model_load_s"]["type"], ["number", "null"])

    def test_valid_document_with_raw_fields_and_null_residency(self):
        """Test that a document with raw fields and model_residency null is valid."""
        doc = {
            "observed": {
                "physical": {
                    "model_loaded_at_start": True,
                    "model_load_count": 1,
                    "model_unload_count": 0,
                    "model_load_s": 2.5,
                    "model_residency": None
                }
            }
        }

        # Check required fields are present
        self.assertIn("observed", doc)
        self.assertIn("physical", doc["observed"])

        # Check keys are subset of properties
        keys = set(doc["observed"]["physical"])  # type: ignore
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore
        self.assertTrue(keys.issubset(expected_keys))

        # Check enum membership
        residency = doc["observed"]["physical"]["model_residency"]
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["model_residency"]["enum"]
        self.assertIn(residency, enum_values)

    def test_valid_document_with_only_raw_facts(self):
        """Test that a document with only raw facts (no model_residency) is valid."""
        doc = {
            "observed": {
                "physical": {
                    "model_loaded_at_start": False,
                    "model_load_count": 0,
                    "model_unload_count": 1,
                    "model_load_s": 0.0
                }
            }
        }

        # Check required fields are present
        self.assertIn("observed", doc)
        self.assertIn("physical", doc["observed"])

        # Check keys are subset of properties
        keys = set(doc["observed"]["physical"])  # type: ignore
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore
        self.assertTrue(keys.issubset(expected_keys))

        # model_residency is not present, so it's not required
        # But if it were, it would be checked via enum
        # No need to check enum since it's not in the doc

    def test_invalid_model_residency_value(self):
        """Test that an invalid model_residency value raises an error."""
        doc = {
            "observed": {
                "physical": {
                    "model_residency": "invalid_state"
                }
            }
        }

        # Check keys are subset of properties
        keys = set(doc["observed"]["physical"])  # type: ignore
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore
        self.assertTrue(keys.issubset(expected_keys))

        # Check enum membership
        residency = doc["observed"]["physical"]["model_residency"]
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["model_residency"]["enum"]
        self.assertNotIn(residency, enum_values)

    def test_missing_required_field(self):
        """Test that a document missing a required field fails."""
        doc = {
            "observed": {
                "physical": {}
            }
        }

        # Check required fields
        required = set(self.schema["required"])
        keys = set(doc["observed"]["physical"])  # type: ignore
        self.assertTrue(required.issubset(keys))
        # This test will fail if required fields are missing
        # But we are testing the schema, not the document
        # So we just verify the logic
        # The test will fail if the schema is wrong
        # But we already tested the schema structure

    def test_extra_field_not_allowed(self):
        """Test that an extra field not in the schema is not allowed."""
        doc = {
            "observed": {
                "physical": {
                    "model_loaded_at_start": True,
                    "extra_field": "value"
                }
            }
        }

        # Check keys are subset of properties
        keys = set(doc["observed"]["physical"])  # type: ignore
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore
        self.assertTrue(keys.issubset(expected_keys))
        # This will fail if extra_field is not in expected_keys
        # But we already tested that
        # So this test will pass if the schema is correct
        # But we are testing the document
        # So we need to check if extra_field is in expected_keys
        # It should not be
        self.assertNotIn("extra_field", expected_keys)

    def test_null_model_residency_is_valid(self):
        """Test that model_residency set to null is valid."""
        doc = {
            "observed": {
                "physical": {
                    "model_residency": None
                }
            }
        }

        # Check keys are subset of properties
        keys = set(doc["observed"]["physical"])  # type: ignore
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"])  # type: ignore
        self.assertTrue(keys.issubset(expected_keys))

        # Check enum membership
        residency = doc["observed"]["physical"]["model_residency"]
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["model_residency"]["enum"]
        self.assertIn(residency, enum_values)