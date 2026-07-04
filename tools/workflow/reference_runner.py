from typing import Any, Dict, List, Optional
import json
import os
from pathlib import Path

from tools.workflow.project_findings import find_findings
from tools.workflow.project_policy import find_policy
from tools.workflow.project_associations import find_associations
from tools.workflow.project_coverage import find_coverage
from tools.workflow.project_experiments import find_experiments
from tools.workflow.project_capacity import find_capacity
from tools.workflow.project_economy import derive_economic_context, _compute_counterfactual

# ... (existing code) ...

def build_scheduler_decision(run_id: str, workflow_id: str, scenario: str, selection: Dict[str, Any], task_kind: str) -> Dict[str, Any]:
    """
    Build the scheduler-decision contract document.
    """
    decision = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "scenario": scenario,
        "task_kind": task_kind,
        "selected": selection["selected"],
        "decision_reason": selection["decision_reason"],
        "candidates_considered": selection["candidates_considered"],
        "candidates_blocked": selection["candidates_blocked"],
        "policy_influence": selection["policy_influence"],
        "capability_influence": selection["capability_influence"],
        "economy_influence": None  # Will be set below
    }

    if selection["selected"] is not None:
        selected_candidate = selection["selected"]
        # Derive economic context
        economic_context = derive_economic_context(selected_candidate)
        
        # Compute counterfactual
        counterfactual = _compute_counterfactual(selection["candidates_considered"], economic_context["objective"])
        
        # Build economy_influence
        decision["economy_influence"] = {
            "objective_selected": economic_context["objective"],
            "signals_read": economic_context["signals_read"],
            "reason": economic_context["reason"],
            "counterfactual": counterfactual
        }

    return decision

# ... (rest of file) ...