import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_capacity import extract_observations, extract_scheduler_decisions


def materialize_associations(observations: List[Dict[str, Any]], decisions: List[Dict[str, Any]], as_of: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Materialize associations from observations and decisions.

    Args:
        observations: List of observation events.
        decisions: List of decision events.
        as_of: Optional ISO-8601 timestamp. If provided, filter observations and decisions to those with timestamp <= as_of.

    Returns:
        Dictionary of associations by association_id.
    """
    # Filter if as_of is provided
    if as_of is not None:
        observations = [o for o in observations if o.get('timestamp', '') <= as_of]
        decisions = [d for d in decisions if d.get('timestamp', '') <= as_of]

    associations = {}
    for obs in observations:
        if 'association' in obs:
            aid = obs['association']['association_id']
            associations[aid] = {
                'association_id': aid,
                'capability_id': obs['association']['capability_id'],
                'qualification_status': obs['association']['qualification_status'],
                'confidence': obs['association']['confidence'],
                'confidence_score': obs['association']['confidence_score'],
                'details': obs['association'].get('details', {})
            }
    
    # Apply decisions
    for dec in decisions:
        if 'association' in dec and 'association_id' in dec['association']:
            aid = dec['association']['association_id']
            if aid in associations:
                associations[aid]['qualification_status'] = dec['association']['qualification_status']
                associations[aid]['confidence'] = dec['association']['confidence']
                associations[aid]['confidence_score'] = dec['association']['confidence_score']
    
    return associations