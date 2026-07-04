import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_capacity import extract_observations, extract_scheduler_decisions


def materialize_findings(observations: List[Dict[str, Any]], decisions: List[Dict[str, Any]], as_of: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Materialize findings from observations and decisions.

    Args:
        observations: List of observation events.
        decisions: List of decision events.
        as_of: Optional ISO-8601 timestamp. If provided, filter observations and decisions to those with timestamp <= as_of.

    Returns:
        Dictionary of findings by finding_id.
    """
    # Filter if as_of is provided
    if as_of is not None:
        observations = [o for o in observations if o.get('timestamp', '') <= as_of]
        decisions = [d for d in decisions if d.get('timestamp', '') <= as_of]

    findings = {}
    for obs in observations:
        if 'finding' in obs:
            fid = obs['finding']['finding_id']
            findings[fid] = {
                'finding_id': fid,
                'confidence': obs['finding']['confidence'],
                'confidence_score': obs['finding']['confidence_score'],
                'type': obs['finding']['type'],
                'details': obs['finding'].get('details', {})
            }
    
    # Apply decisions
    for dec in decisions:
        if 'finding' in dec and 'finding_id' in dec['finding']:
            fid = dec['finding']['finding_id']
            if fid in findings:
                findings[fid]['confidence'] = dec['finding']['confidence']
                findings[fid]['confidence_score'] = dec['finding']['confidence_score']
    
    return findings