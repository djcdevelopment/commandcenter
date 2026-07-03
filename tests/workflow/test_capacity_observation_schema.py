import json
import unittest
from pathlib import Path

SCHEMA_PATH = Path("contracts/capacity-observation.v1.schema.json")


class TestCapacityObservationSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r") as f:
            self.schema = json.load(f)

    def test_schema_structure(self):
        """Verify the schema structure matches expected properties and types."""
        # Check top-level properties
        self.assertIn("properties", self.schema)
        self.assertIn("observed", self.schema["properties"])
        self.assertIn("physical", self.schema["properties"]["observed"]["properties"])

        physical_props = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]

        # Check required fields
        required = self.schema["properties"]["observed"]["properties"]["physical"].get("required", [])
        expected_required = ["model_residency", "model_loaded_at_start", "model_load_count", "model_unload_count", "model_load_s"]
        self.assertEqual(set(required), set(expected_required))

        # Check all expected fields are present
        expected_fields = {
            "model_residency", "model_loaded_at_start", "model_load_count", 
            "model_unload_count", "model_load_s"
        }
        actual_fields = set(physical_props.keys())
        self.assertEqual(actual_fields, expected_fields)

        # Check types
        self.assertEqual(physical_props["model_residency"]["type"], "string")
        self.assertEqual(physical_props["model_loaded_at_start"]["type"], ["boolean", "null"])
        self.assertEqual(physical_props["model_load_count"]["type"], ["integer", "null"])
        self.assertEqual(physical_props["model_unload_count"]["type"], ["integer", "null"])
        self.assertEqual(physical_props["model_load_s"]["type"], ["number", "null"])

        # Check enum for model_residency
        self.assertIn("enum", physical_props["model_residency"])
        expected_enum = [None, "cold_load", "warm_resident", "evicted_mid_run"]
        self.assertEqual(physical_props["model_residency"]["enum"], expected_enum)

    def test_valid_document_with_raw_fields_and_null_residency(self):
        """Test a document with raw model_* fields and model_residency set to null."""
        doc = {
            "observed": {
                "physical": {
                    "model_residency": None,
                    "model_loaded_at_start": True,
                    "model_load_count": 1,
                    "model_unload_count": 0,
                    "model_load_s": 2.5
                }
            }
        }
        self.validate_document(doc)

    def test_valid_document_with_only_raw_facts(self):
        """Test a document with only raw model_* fields (no model_residency)."""
        doc = {
            "observed": {
                "physical": {
                    "model_loaded_at_start": False,
                    "model_load_count": 3,
                    "model_unload_count": 1,
                    "model_load_s": 5.0
                }
            }
        }
        self.validate_document(doc)

    def validate_document(self, doc):
        """Check that the document satisfies the schema structure."""
        # Check required fields are present
        required_fields = ["model_residency", "model_loaded_at_start", "model_load_count", "model_unload_count", "model_load_s"]
        for field in required_fields:
            self.assertIn(field, doc["observed"]["physical"])

        # Check keys are subset of properties
        actual_keys = set(doc["observed"]["physical"].keys())
        expected_keys = set(self.schema["properties"]["observed"]["properties"]["physical"]["properties"].keys())
        self.assertTrue(actual_keys.issubset(expected_keys))

        # Check enum membership for model_residency
        if "model_residency" in doc["observed"]["physical"]:
            value = doc["observed"]["physical"]["model_residency"]
            enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"]
            self.assertIn(value, enum_values)

        # Check types
        self.assertIsInstance(doc["observed"]["physical"]["model_loaded_at_start"], (bool, type(None)))
        self.assertIsInstance(doc["observed"]["physical"]["model_load_count"], (int, type(None)))
        self.assertIsInstance(doc["observed"]["physical"]["model_unload_count"], (int, type(None)))
        self.assertIsInstance(doc["observed"]["physical"]["model_load_s"], (int, float, type(None)))

if __name__ == "__main__":
    unittest.main(verbosity=2)