import json
import os
from typing import Dict, Any, List, Optional

class CorpusGuard:
    """
    A corpus guard that manages beliefs and prevents invalid ones from being stored.
    
    This class implements the belief system described in the instructions:
    - Beliefs are stored in findings.json
    - Only valid beliefs (those with proper structure) can be stored
    - Invalid beliefs are rejected and logged
    """
    
    def __init__(self, findings_file: str = "findings.json"):
        self.findings_file = findings_file
        self.findings = self._load_findings()
    
    def _load_findings(self) -> List[Dict[str, Any]]:
        """
        Load findings from the JSON file.
        
        Returns:
            List[Dict[str, Any]]: The list of findings
        """
        if os.path.exists(self.findings_file):
            try:
                with open(self.findings_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # If file is corrupted or unreadable, start fresh
                return []
        else:
            # Create the file if it doesn't exist
            self._save_findings([])
            return []
    
    def _save_findings(self, findings: List[Dict[str, Any]]) -> None:
        """
        Save findings to the JSON file.
        
        Args:
            findings (List[Dict[str, Any]]): The list of findings to save
        """
        try:
            with open(self.findings_file, 'w') as f:
                json.dump(findings, f, indent=2)
        except IOError as e:
            raise Exception(f"Failed to save findings: {e}")
    
    def is_valid_belief(self, belief: Dict[str, Any]) -> bool:
        """
        Check if a belief is valid according to the corpus guard rules.
        
        Args:
            belief (Dict[str, Any]): The belief to validate
            
        Returns:
            bool: True if the belief is valid, False otherwise
        """
        # A valid belief must have at least a 'belief' key with content
        if not isinstance(belief, dict):
            return False
        
        if 'belief' not in belief or not belief['belief']:
            return False
        
        # Additional validation rules can be added here
        # For now, we'll accept any belief that has a non-empty 'belief' field
        return True
    
    def store_belief(self, belief: Dict[str, Any]) -> bool:
        """
        Store a belief if it's valid.
        
        Args:
            belief (Dict[str, Any]): The belief to store
            
        Returns:
            bool: True if the belief was stored, False otherwise
        """
        if not self.is_valid_belief(belief):
            print(f"Invalid belief rejected: {belief}")
            return False
        
        # Add timestamp or other metadata if needed
        if 'timestamp' not in belief:
            import datetime
            belief['timestamp'] = datetime.datetime.now().isoformat()
        
        self.findings.append(belief)
        self._save_findings(self.findings)
        return True
    
    def get_all_beliefs(self) -> List[Dict[str, Any]]:
        """
        Get all stored beliefs.
        
        Returns:
            List[Dict[str, Any]]: All stored beliefs
        """
        return self.findings
    
    def get_belief_by_id(self, belief_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific belief by its index.
        
        Args:
            belief_id (int): The index of the belief to retrieve
            
        Returns:
            Optional[Dict[str, Any]]: The belief if found, None otherwise
        """
        if 0 <= belief_id < len(self.findings):
            return self.findings[belief_id]
        return None
    
    def clear_all_beliefs(self) -> None:
        """
        Clear all stored beliefs.
        """
        self.findings = []
        self._save_findings([])

# Example usage
if __name__ == "__main__":
    # Create a corpus guard instance
    guard = CorpusGuard()
    
    # Test storing valid beliefs
    guard.store_belief({"belief": "The sky is blue"})
    guard.store_belief({"belief": "Water boils at 100 degrees Celsius"})
    
    # Test storing invalid belief (should be rejected)
    guard.store_belief({"belief": ""})  # Empty belief
    guard.store_belief({"invalid_key": "This will be rejected"})  # Missing 'belief' key
    
    # Retrieve all beliefs
    all_beliefs = guard.get_all_beliefs()
    print("All stored beliefs:")
    for i, belief in enumerate(all_beliefs):
        print(f"{i}: {belief['belief']}")
