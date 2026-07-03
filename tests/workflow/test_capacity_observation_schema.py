import json
import unittest
from pathlib import Path

SCHEMA_PATH = Path("contracts/capacity-observation.v1.schema.json")


class TestCapacityObservationSchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH, "r") as f:
            self.schema = json.load(f)

    def test_required_keys_in_schema(self):
        required = set(self.schema["required"])
        expected_required = {"contract_version", "run_id", "timestamp", "observed"}
        self.assertEqual(required, expected_required)

    def test_observed_physical_properties(self):
        observed = self.schema["properties"]["observed"]
        physical = observed["properties"]["physical"]
        properties = set(physical["properties"].keys())
        expected_properties = {
            "gpu_temp_c_peak", "gpu_temp_c_sustained_avg", "power_w_avg", "power_w_peak",
            "fan_rpm_avg", "clock_mhz_avg", "model_residency", "model_loaded_at_start",
            "model_load_count", "model_unload_count", "model_load_s"
        }
        self.assertEqual(properties, expected_properties)

    def test_model_residency_enum(self):
        model_residency = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]
        self.assertIn("enum", model_residency)
        self.assertEqual(model_residency["enum"], [None, "cold_load", "warm_resident", "evicted_mid_run"])

    def test_model_loaded_at_start_type(self):
        field = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_loaded_at_start"]
        self.assertIn("type", field)
        self.assertEqual(field["type"], ["boolean", "null"])

    def test_model_load_count_type(self):
        field = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_load_count"]
        self.assertIn("type", field)
        self.assertEqual(field["type"], ["integer", "null"])

    def test_model_unload_count_type(self):
        field = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_unload_count"]
        self.assertIn("type", field)
        self.assertEqual(field["type"], ["integer", "null"])

    def test_model_load_s_type(self):
        field = self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_load_s"]
        self.assertIn("type", field)
        self.assertEqual(field["type"], ["number", "null"])

    def test_valid_document_with_raw_fields_and_null_residency(self):
        doc = {
            "contract_version": "capacity-observation.v1",
            "run_id": "test-run-1",
            "timestamp": "2026-07-02T06:55:00Z",
            "observed": {
                "physical": {
                    "gpu_temp_c_peak": 75.0,
                    "gpu_temp_c_sustained_avg": 70.0,
                    "power_w_avg": 250.0,
                    "power_w_peak": 300.0,
                    "fan_rpm_avg": 4500,
                    "clock_mhz_avg": 1500,
                    "model_residency": None,
                    "model_loaded_at_start": True,
                    "model_load_count": 1,
                    "model_unload_count": 0,
                    "model_load_s": 2.5
                }
            }
        }
        # Check required keys
        self.assertTrue(set(doc.keys()).issuperset(set(self.schema["required"])))
        # Check keys are subset of properties
        self.assertTrue(set(doc["observed"]["physical"].keys()).issubset(set(self.schema["properties"]["observed"]["properties"]["physical"]["properties"].keys())))
        # Check enum membership
        if doc["observed"]["physical"]["model_residency"] is not None:
            self.assertIn(doc["observed"]["physical"]["model_residency"], self.schema["properties"]["observed"]["properties"]["physical"]["properties"]["model_residency"]["enum"])

    def test_valid_document_with_only_raw_facts(self):
        doc = {
            "contract_version": "capacity-observation.v1",
            "run_id": "test-run-2",
            "timestamp": "2026-07-02T06:55:00Z",
            "observed": {
                "physical": {
                    "model_loaded_at_start": False,
                    "model_load_count": 0,
                    "model_unload_count": 0,
                    "model_load_s": 0.0
                }
            }
        }
        # Check required keys
        self.assertTrue(set(doc.keys()).issuperset(set(self.schema["required"])))
        # Check keys are subset of properties
        self.assertTrue(set(doc["observed"]["physical"].keys()).issubset(set(self.schema["properties"]["observed"]["properties"]["physical"]["properties"].keys())))
        # model_residency is not present, so no enum check needed

if __name__ == "__main__":
    unittest.main(verbosity=2)