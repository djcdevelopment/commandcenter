'''
Test suite for check_doc_claims.py

Tests:
- registry parses correctly
- checker passes with current state (waived claim)
- synthetic failing claim without waiver → nonzero exit
- expired waiver → FAIL
- list-valued path compares length
'''

import unittest
import subprocess
import json
import os
from datetime import datetime

# Test data
TEST_CLAIMS = [
    {
        "claim_id": "test-claim-pass",
        "doc": "test-doc",
        "description": "test pass",
        "check": {
            "file": "knowledge/capabilities.json",
            "path": "capability_count",
            "op": "gte",
            "value": 0
        }
    },
    {
        "claim_id": "test-claim-fail",
        "doc": "test-doc",
        "description": "test fail",
        "check": {
            "file": "knowledge/capabilities.json",
            "path": "capability_count",
            "op": "gte",
            "value": 2
        }
    }
]

TEST_WAIVERS = [
    {
        "claim_id": "test-claim-fail",
        "reason": "test",
        "author": "test",
        "created": "2025-01-01",
        "expires": "2025-01-02"
    }
]

class TestDocClaims(unittest.TestCase):
    def setUp(self):
        # Create test files
        with open("docs/doc-claims.json", "w") as f:
            json.dump(TEST_CLAIMS, f, indent=2)
        
        with open("docs/doc-claims-waivers.json", "w") as f:
            json.dump(TEST_WAIVERS, f, indent=2)

    def tearDown(self):
        # Clean up
        if os.path.exists("docs/doc-claims.json"):
            os.remove("docs/doc-claims.json")
        if os.path.exists("docs/doc-claims-waivers.json"):
            os.remove("docs/doc-claims-waivers.json")

    def test_registry_parsing(self):
        """Test that the claims registry parses correctly"""
        # Run checker
        result = subprocess.run(
            ["python3", "tools/workflow/check_doc_claims.py"],
            capture_output=True,
            text=True
        )
        
        # Check that it runs without error
        self.assertEqual(result.returncode, 0)
        
        # Check output contains expected claims
        output = result.stdout + result.stderr
        self.assertIn("test-claim-pass", output)
        self.assertIn("test-claim-fail", output)
        
    def test_checker_with_waiver(self):
        """Test that checker passes when claim is waived"""
        # Run checker
        result = subprocess.run(
            ["python3", "tools/workflow/check_doc_claims.py"],
            capture_output=True,
            text=True
        )
        
        # Should pass (waived claim)
        self.assertEqual(result.returncode, 0)
        
    def test_synthetic_failing_claim(self):
        """Test that synthetic failing claim without waiver causes nonzero exit"""
        # Modify claims to have a failing claim without waiver
        failing_claims = [
            {
                "claim_id": "test-claim-fail-no-waiver",
                "doc": "test-doc",
                "description": "test fail no waiver",
                "check": {
                    "file": "knowledge/capabilities.json",
                    "path": "capability_count",
                    "op": "gte",
                    "value": 2
                }
            }
        ]
        
        # Write new claims
        with open("docs/doc-claims.json", "w") as f:
            json.dump(failing_claims, f, indent=2)
        
        # Run checker
        result = subprocess.run(
            ["python3", "tools/workflow/check_doc_claims.py"],
            capture_output=True,
            text=True
        )
        
        # Should fail
        self.assertNotEqual(result.returncode, 0)
        
    def test_expired_waiver(self):
        """Test that expired waiver results in FAIL"""
        # Set today to be after waiver expiry
        # Create a new waiver with expiry in the past
        expired_waivers = [
            {
                "claim_id": "test-claim-fail",
                "reason": "test",
                "author": "test",
                "created": "2025-01-01",
                "expires": "2025-01-02"
            }
        ]
        
        # Write new waivers
        with open("docs/doc-claims-waivers.json", "w") as f:
            json.dump(expired_waivers, f, indent=2)
        
        # Run checker
        result = subprocess.run(
            ["python3", "tools/workflow/check_doc_claims.py"],
            capture_output=True,
            text=True
        )
        
        # Should fail (expired waiver)
        self.assertNotEqual(result.returncode, 0)
        
    def test_list_length_comparison(self):
        """Test that list-valued path compares length"""
        # Create a test file with a list
        test_data = {
            "contract_version": "test.v1",
            "evidence_watermark": "2026-07-02T06:55:00Z",
            "candidates": [
                {"id": 1},
                {"id": 2},
                {"id": 3}
            ]
        }
        
        # Write test data
        with open("knowledge/test_candidates.json", "w") as f:
            json.dump(test_data, f, indent=2)
        
        # Update claims to check list length
        length_claims = [
            {
                "claim_id": "test-list-length",
                "doc": "test-doc",
                "description": "test list length comparison",
                "check": {
                    "file": "knowledge/test_candidates.json",
                    "path": "candidates",
                    "op": "gte",
                    "value": 3
                }
            }
        ]
        
        # Write claims
        with open("docs/doc-claims.json", "w") as f:
            json.dump(length_claims, f, indent=2)
        
        # Run checker
        result = subprocess.run(
            ["python3", "tools/workflow/check_doc_claims.py"],
            capture_output=True,
            text=True
        )
        
        # Should pass (3 >= 3)
        self.assertEqual(result.returncode, 0)
        
        # Clean up
        os.remove("knowledge/test_candidates.json")

if __name__ == "__main__":
    unittest.main()