from typing import Dict, Any, List
from pydantic import BaseModel
from pathlib import Path
import json
import os

# Existing imports and definitions
# ... (existing code)

# New: ledger logging for batch completions

def record_batch_completion(plan_ids: List[str], task_class: str, success: bool, error: str = ""):
    """Record completion of a batch of tasks in the ledger, one per task."""
    # Load existing capacity.json
    capacity_path = Path("knowledge/capacity.json")
    if not capacity_path.exists():
        capacity = {}
    else:
        with open(capacity_path, 'r') as f:
            capacity = json.load(f)

    # Ensure task_class key exists
    if task_class not in capacity:
        capacity[task_class] = {
            "total": 0,
            "success": 0,
            "failure": 0
        }

    # Record each task completion
    for plan_id in plan_ids:
        # Update counters
        capacity[task_class]["total"] += 1
        if success:
            capacity[task_class]["success"] += 1
        else:
            capacity[task_class]["failure"] += 1

    # Write back to file
    with open(capacity_path, 'w') as f:
        json.dump(capacity, f, indent=2)

    # Also log to ledger (if needed)
    ledger_path = Path("knowledge/ledger.json")
    if not ledger_path.exists():
        ledger = []
    else:
        with open(ledger_path, 'r') as f:
            ledger = json.load(f)

    # Add event for each task
    for plan_id in plan_ids:
        event = {
            "type": "task_completion",
            "plan_id": plan_id,
            "task_class": task_class,
            "success": success,
            "error": error,
            "timestamp": os.times()[4]  # Approximate timestamp
        }
        ledger.append(event)

    # Write back to ledger
    with open(ledger_path, 'w') as f:
        json.dump(ledger, f, indent=2)

# Update existing record_event to support batch logging

def record_event(event_type: str, **kwargs):
    """Record an event in the ledger and update capacity."""
    # Existing implementation...
    if event_type == "task_completion":
        task_class = kwargs.get("task_class")
        success = kwargs.get("success", True)
        error = kwargs.get("error", "")
        plan_id = kwargs.get("plan_id")
        
        # If plan_id is a list, treat as batch
        if isinstance(plan_id, list):
            record_batch_completion(plan_id, task_class, success, error)
        else:
            # Regular single task
            record_batch_completion([plan_id], task_class, success, error)
    else:
        # Handle other event types
        pass

# Export for use in other modules
__all__ = ["record_event", "record_batch_completion"]