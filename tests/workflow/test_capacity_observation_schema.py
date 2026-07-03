import json
import unittest
from pathlib import Path

SCHEMA_PATH = Path("contracts/capacity-observation.v1.schema.json")

class TestCapacityObservationSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r") as f:
            self.schema = json.load(f)

    def test_schema_structure(self):
        """Verify the schema has correct structure: required ⊆ keys ⊆ properties, enum membership."""
        # Extract required fields and properties
        required = set(self.schema["required"])
        properties = set(self.schema["properties"])
        
        # Check required ⊆ properties
        self.assertTrue(required.issubset(properties), 
                        f"Required fields {required - properties} not in properties")
        
        # Check additionalProperties: false
        self.assertFalse(self.schema.get("additionalProperties", True), 
                        "Schema must have additionalProperties: false")
        
        # Check physical object structure
        physical_props = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]
        physical_required = set(self.schema["properties"]["observed"]["properties"]["physical"]["required"])
        
        # Verify required ⊆ properties for physical
        physical_prop_keys = set(physical_props.keys())
        self.assertTrue(physical_required.issubset(physical_prop_keys), 
                        f"Physical required fields {physical_required - physical_prop_keys} not in physical properties")
        
        # Check model_residency enum
        model_residency_enum = physical_props["model_residency"]["enum"]
        expected_enum = [None, "cold_load", "warm_resident", "evicted_mid_run"]
        self.assertEqual(model_residency_enum, expected_enum, 
                        f"model_residency enum mismatch: expected {expected_enum}, got {model_residency_enum}")
        
        # Check raw fields types
        raw_fields = [
            "model_loaded_at_start", 
            "model_load_count", 
            "model_unload_count", 
            "model_load_s"
        ]
        
        for field in raw_fields:
            field_type = physical_props[field]["type"]
            expected_type = ["boolean", "null"] if field == "model_loaded_at_start" else ["integer", "null"]
            if field == "model_load_s":
                expected_type = ["number", "null"]
            self.assertEqual(field_type, expected_type, 
                            f"{field} type mismatch: expected {expected_type}, got {field_type}")

    def test_valid_document_with_raw_fields_and_null_residency(self):
        """Test that a document with raw fields and model_residency null is valid."""
        doc = {
            "contract_version": "capacity-observation.v1",
            "run_id": "test-run-1",
            "timestamp": "2026-07-02T06:55:00Z",
            "observed": {
                "physical": {
                    "gpu_temp_c_peak": 75.0,
                    "model_loaded_at_start": True,
                    "model_load_count": 1,
                    "model_unload_count": 0,
                    "model_load_s": 2.5,
                    "model_residency": None
                }
            }
        }
        
        # Check required fields
        required = set(self.schema["required"])
        doc_keys = set(doc.keys())
        self.assertTrue(required.issubset(doc_keys), 
                        f"Missing required fields: {required - doc_keys}")
        
        # Check keys ⊆ properties
        doc_props = set(self.schema["properties"].keys())
        self.assertTrue(doc_keys.issubset(doc_props), 
                        f"Document has extra keys: {doc_keys - doc_props}")
        
        # Check physical properties
        physical_keys = set(doc["observed"]["physical"].keys())
        physical_prop_keys = set(self.schema["properties"]["observed"]["properties"]["physical"]["properties"].keys())
        self.assertTrue(physical_keys.issubset(physical_prop_keys), 
                        f"Physical keys not in schema: {physical_keys - physical_prop_keys}")
        
        # Check enum membership for model_residency
        residency = doc["observed"]["physical"]["model_residency"]
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"]
        self.assertIn(residency, enum_values, 
                     f"model_residency value {residency} not in enum {enum_values}")

    def test_valid_document_with_only_raw_fields(self):
        """Test that a document with only raw model_* fields is valid."""
        doc = {
            "contract_version": "capacity-observation.v1",
            "run_id": "test-run-2",
            "timestamp": "2026-07-02T06:55:00Z",
            "observed": {
                "physical": {
                    "model_loaded_at_start": False,
                    "model_load_count": 0,
                    "model_unload_count": 0,
                    "model_load_s": 0.0,
                    "model_residency": None
                }
            }
        }
        
        # Check required fields
        required = set(self.schema["required"])
        doc_keys = set(doc.keys())
        self.assertTrue(required.issubset(doc_keys), 
                        f"Missing required fields: {required - doc_keys}")
        
        # Check keys ⊆ properties
        doc_props = set(self.schema["properties"].keys())
        self.assertTrue(doc_keys.issubset(doc_props), 
                        f"Document has extra keys: {doc_keys - doc_props}")
        
        # Check physical properties
        physical_keys = set(doc["observed"]["physical"].keys())
        physical_prop_keys = set(self.schema["properties"]["observed"]["properties"]["physical"]["properties"].keys())
        self.assertTrue(physical_keys.issubset(physical_prop_keys), 
                        f"Physical keys not in schema: {physical_keys - physical_prop_keys}")
        
        # Check enum membership for model_residency
        residency = doc["observed"]["physical"]["model_residency"]
        enum_values = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"]
        self.assertIn(residency, enum_values, 
                     f"model_residency value {residency} not in enum {enum_values}")

if __name__ == "__main__":
    unittest.main(verbosity=2)