import json
import os
import tempfile
import unittest
from corpus_guard import CorpusGuard

class TestCorpusGuard(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary findings file for testing
        self.temp_file = "temp_findings.json"
        self.guard = CorpusGuard(self.temp_file)
        
    def tearDown(self):
        # Clean up the temporary file
        if os.path.exists(self.temp_file):
            os.remove(self.temp_file)
    
    def test_valid_belief_storage(self):
        # Test storing a valid belief
        belief = {"belief": "The sky is blue"}
        result = self.guard.store_belief(belief)
        
        self.assertTrue(result)
        
        # Verify the belief was stored
        all_beliefs = self.guard.get_all_beliefs()
        self.assertEqual(len(all_beliefs), 1)
        self.assertEqual(all_beliefs[0]['belief'], "The sky is blue")
    
    def test_invalid_belief_rejection(self):
        # Test that empty belief is rejected
        result = self.guard.store_belief({"belief": ""})
        self.assertFalse(result)
        
        # Test that missing 'belief' key is rejected
        result = self.guard.store_belief({"invalid_key": "test"})
        self.assertFalse(result)
        
        # Verify no beliefs were stored
        all_beliefs = self.guard.get_all_beliefs()
        self.assertEqual(len(all_beliefs), 0)
    
    def test_multiple_beliefs(self):
        # Test storing multiple valid beliefs
        beliefs = [
            {"belief": "The sky is blue"},
            {"belief": "Water boils at 100 degrees Celsius"},
            {"belief": "Python is a programming language"}
        ]
        
        for belief in beliefs:
            self.assertTrue(self.guard.store_belief(belief))
        
        # Verify all beliefs were stored
        all_beliefs = self.guard.get_all_beliefs()
        self.assertEqual(len(all_beliefs), 3)
        
        for i, expected_belief in enumerate(beliefs):
            self.assertEqual(all_beliefs[i]['belief'], expected_belief['belief'])
    
    def test_clear_all_beliefs(self):
        # Store some beliefs
        self.guard.store_belief({"belief": "Test belief 1"})
        self.guard.store_belief({"belief": "Test belief 2"})
        
        # Clear all beliefs
        self.guard.clear_all_beliefs()
        
        # Verify no beliefs remain
        all_beliefs = self.guard.get_all_beliefs()
        self.assertEqual(len(all_beliefs), 0)

if __name__ == '__main__':
    unittest.main()