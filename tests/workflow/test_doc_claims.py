import unittest
import os
import json
import tempfile
from unittest.mock import patch

from tools.workflow.check_doc_claims import load_claims, load_waivers, evaluate_check, is_waived, main

# Test data
TEST_CLAIMS = [
    {
        "claim_id": "roadmap-first-capability",
        "doc": "CAPABILITY-ROADMAP.html",
        "description": "S5b claims the first real capability exists",
        "check": {
            "file": "knowledge/capabilities.json",
            "path": "capability_count",
            "op": "gte",
            "value": 1
        }
    },
    {
        "claim_id": "candidates-present",
        "doc": "TWO-ECONOMIES-WIND-TUNNEL.html",
        "description": "the experiment funnel holds candidates",
        "check": {
            "file": "knowledge/experiment_candidates.json",
            "path": "candidates",
            "op": "gte",
            "value": 1
        }
    }
]

TEST_WAIVERS = [
    {
        "claim_id": "roadmap-first-capability",
        "reason": "capability lost in 2026-07-02 knowledge/ overwrite; re-derivation pending (see THREE-CHAIRS-PERSPECTIVES.html addendum)",
        "author": "derek",
        "created": "2026-07-02",
        "expires": null
    }
]

# Mock data for testing
MOCK_CAPABILITIES = {"capability_count": 0}
MOCK_CANDIDATES = {"candidates": []}

class TestDocClaims(unittest.TestCase):
    def setUp(self):
        # Create temporary files
        self.temp_dir = tempfile.mkdtemp()
        self.claims_path = os.path.join(self.temp_dir, "doc-claims.json")
        self.waivers_path = os.path.join(self.temp_dir, "doc-claims-waivers.json")
        self.capabilities_path = os.path.join(self.temp_dir, "capabilities.json")
        self.candidates_path = os.path.join(self.temp_dir, "candidates.json")

        # Write test data
        with open(self.claims_path, "w") as f:
            json.dump(TEST_CLAIMS, f, indent=2)

        with open(self.waivers_path, "w") as f:
            json.dump(TEST_WAIVERS, f, indent=2)

        with open(self.capabilities_path, "w") as f:
            json.dump(MOCK_CAPABILITIES, f, indent=2)

        with open(self.candidates_path, "w") as f:
            json.dump(MOCK_CANDIDATES, f, indent=2)

    def tearDown(self):
        # Clean up temporary files
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_load_claims(self):
        # Mock the load_claims function to use our temp file
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir):
            claims = load_claims()
            self.assertEqual(len(claims), 2)
            self.assertEqual(claims[0]["claim_id"], "roadmap-first-capability")

    def test_load_waivers(self):
        # Mock the load_waivers function to use our temp file
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir):
            waivers = load_waivers()
            self.assertEqual(len(waivers), 1)
            self.assertEqual(waivers["roadmap-first-capability"]["author"], "derek")

    def test_evaluate_check_success(self):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir):
            claim = {
                "check": {
                    "file": "capabilities.json",
                    "path": "capability_count",
                    "op": "gte",
                    "value": 0
                }
            }
            result, actual = evaluate_check(claim)
            self.assertTrue(result)
            self.assertEqual(actual, 0)

    def test_evaluate_check_failure(self):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir):
            claim = {
                "check": {
                    "file": "capabilities.json",
                    "path": "capability_count",
                    "op": "gte",
                    "value": 1
                }
            }
            result, actual = evaluate_check(claim)
            self.assertFalse(result)
            self.assertEqual(actual, 0)

    def test_evaluate_check_list_length(self):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir):
            claim = {
                "check": {
                    "file": "candidates.json",
                    "path": "candidates",
                    "op": "gte",
                    "value": 0
                }
            }
            result, actual = evaluate_check(claim)
            self.assertTrue(result)
            self.assertEqual(actual, 0)

    def test_is_waived_active(self):
        waivers = {"roadmap-first-capability": {"expires": None}}
        result, reason = is_waived("roadmap-first-capability", waivers)
        self.assertTrue(result)
        self.assertEqual(reason, "never expires")

    def test_is_waived_expired(self):
        waivers = {"roadmap-first-capability": {"expires": "2025-01-01"}}
        result, reason = is_waived("roadmap-first-capability", waivers)
        self.assertFalse(result)
        self.assertEqual(reason, "expired")

    @patch('sys.exit')
    def test_main_pass_with_waiver(self, mock_exit):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir), \
             patch('tools.workflow.check_doc_claims.load_claims', return_value=TEST_CLAIMS), \
             patch('tools.workflow.check_doc_claims.load_waivers', return_value={"roadmap-first-capability": {"expires": None}}), \
             patch('tools.workflow.check_doc_claims.evaluate_check', side_effect=[(False, 0), (True, 0)]), \
             patch('tools.workflow.check_doc_claims.is_waived', return_value=(True, "active")):
            main()
            mock_exit.assert_called_with(0)

    @patch('sys.exit')
    def test_main_fail_without_waiver(self, mock_exit):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir), \
             patch('tools.workflow.check_doc_claims.load_claims', return_value=TEST_CLAIMS), \
             patch('tools.workflow.check_doc_claims.load_waivers', return_value={}), \
             patch('tools.workflow.check_doc_claims.evaluate_check', side_effect=[(False, 0), (True, 0)]), \
             patch('tools.workflow.check_doc_claims.is_waived', return_value=(False, "not waived")):
            main()
            mock_exit.assert_called_with(1)

    @patch('sys.exit')
    def test_main_fail_expired_waiver(self, mock_exit):
        # Mock the file system
        with patch('tools.workflow.check_doc_claims.REPO_ROOT', self.temp_dir), \
             patch('tools.workflow.check_doc_claims.load_claims', return_value=TEST_CLAIMS), \
             patch('tools.workflow.check_doc_claims.load_waivers', return_value={"roadmap-first-capability": {"expires": "2025-01-01"}}), \
             patch('tools.workflow.check_doc_claims.evaluate_check', side_effect=[(False, 0), (True, 0)]), \
             patch('tools.workflow.check_doc_claims.is_waived', return_value=(False, "expired")):
            main()
            mock_exit.assert_called_with(1)

if __name__ == '__main__':
    unittest.main()