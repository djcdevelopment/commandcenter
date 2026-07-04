import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_capacity import extract_observations, extract_scheduler_decisions


def materialize_coverage(observations: List[Dict[str, Any]], decisions: List[Dict[str, Any]], as_of: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Materialize coverage from observations and decisions.

    Args:
        observations: List of observation events.
        decisions: List of decision events.
        as_of: Optional ISO-8601 timestamp. If provided, filter observations and decisions to those with timestamp <= as_of.

    Returns:
        Dictionary of coverage gaps by gap_id.
    """
    # Filter if as_of is provided
    if as_of is not None:
        observations = [o for o in observations if o.get('timestamp', '') <= as_of]
        decisions = [d for d in decisions if d.get('timestamp', '') <= as_of]

    coverage = {}
    for obs in observations:
        if 'coverage' in obs:
            gid = obs['coverage']['gap_id']
            coverage[gid] = {
                'gap_id': gid,
                'status': obs['coverage']['status'],
                'details': obs['coverage'].get('details', {})
            }
    
    # Apply decisions
    for dec in decisions:
        if 'coverage' in dec and 'gap_id' in dec['coverage']:
            gid = dec['coverage']['gap_id']
            if gid in coverage:
                coverage[gid]['status'] = dec['coverage']['status']
    
    return coverage