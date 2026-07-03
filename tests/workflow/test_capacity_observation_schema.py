import json
import unittest
from pathlib import Path

SCHEMA_PATH = Path("contracts/capacity-observation.v1.schema.json")

# Map JSON schema types to Python types
TYPE_MAP = {
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "string": str,
    "array": list,
    "object": dict,
    "null": None
}

class TestCapacityObservationSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r") as f:
            self.schema = json.load(f)

    def test_required_keys(self):
        """Ensure required keys are present in the schema."""
        required = self.schema["required"]
        self.assertIn("observed", required)

    def test_observed_physical_properties(self):
        """Test that observed.physical has the expected properties."""
        physical_props = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]
        expected_keys = {
            "model_residency", "model_loaded_at_start", "model_load_count", 
            "model_unload_count", "model_load_s"
        }
        self.assertEqual(set(physical_props.keys()), expected_keys)

    def test_model_residency_enum(self):
        """Test that model_residency has the correct enum values."""
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"]
        expected_enum = [None, "cold_load", "warm_resident", "evicted_mid_run"]
        self.assertEqual(enum_values, expected_enum)

    def test_model_loaded_at_start_type(self):
        """Test that model_loaded_at_start has the correct type."""
        type_ = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_loaded_at_start"]["type"]
        expected_type = ["boolean", "null"]
        self.assertEqual(type_, expected_type)

    def test_model_load_count_type(self):
        """Test that model_load_count has the correct type."""
        type_ = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_load_count"]["type"]
        expected_type = ["integer", "null"]
        self.assertEqual(type_, expected_type)

    def test_model_unload_count_type(self):
        """Test that model_unload_count has the correct type."""
        type_ = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_unload_count"]["type"]
        expected_type = ["integer", "null"]
        self.assertEqual(type_, expected_type)

    def test_model_load_s_type(self):
        """Test that model_load_s has the correct type."""
        type_ = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_load_s"]["type"]
        expected_type = ["number", "null"]
        self.assertEqual(type_, expected_type)

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
        self.assertTrue(self._is_valid(doc))

    def test_valid_document_with_only_raw_fields(self):
        """Test that a document with only raw fields is valid."""
        doc = {
            "observed": {
                "physical": {
                    "model_loaded_at_start": True,
                    "model_load_count": 1,
                    "model_unload_count": 0,
                    "model_load_s": 2.5
                }
            }
        }
        self.assertTrue(self._is_valid(doc))

    def _is_valid(self, doc):
        """Check if a document conforms to the schema using structural checks."""
        # Check required keys
        if "observed" not in doc:
            return False

        # Check observed.physical
        if "physical" not in doc["observed"]:
            return False

        physical = doc["observed"]["physical"]
        schema_props = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]

        # Check keys
        for key in physical.keys():
            if key not in schema_props:
                return False

        # Check types
        for key, value in physical.items():
            if key not in schema_props:
                return False
            prop_type = schema_props[key]["type"]
            if value is None:
                continue
            # Map JSON type strings to Python types
            python_types = []
            for t in prop_type:
                if t == "null":
                    continue
                if t in TYPE_MAP:
                    python_types.append(TYPE_MAP[t])
                else:
                    # Handle 'number' as (int, float)
                    if t == "number":
                        python_types.append((int, float))
            if not python_types:
                return False
            if not isinstance(value, tuple(python_types)):
                return False

        # Check enum for model_residency
        if "model_residency" in physical:
            if physical["model_residency"] is not None:
                if physical["model_residency"] not in self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"]:
                    return False

        return True

if __name__ == "__main__":
    unittest.main(verbosity=2)