import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_capacity import extract_observations


def synthesize_associations(observations: List[Dict[str, Any]], as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """Synthesize associations from observations, optionally filtered by as_of timestamp."""
    # Filter observations if as_of is provided
    if as_of:
        observations = [o for o in observations if o.get('timestamp', '') <= as_of]

    associations = []
    for obs in observations:
        if obs['type'] == 'association':
            association = {
                'association_id': obs['association_id'],
                'capability_id': obs['capability_id'],
                'timestamp': obs['timestamp']
            }
            associations.append(association)
    return associations


def materialize_associations(event_files: List[str], as_of: Optional[str] = None, out_path: str = 'knowledge/associations.json') -> None:
    """Materialize associations from event files, optionally filtered by as_of timestamp."""
    observations = extract_observations(event_files)
    associations = synthesize_associations(observations, as_of)
    with open(out_path, 'w') as f:
        json.dump(associations, f, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Materialize associations from event files.')
    parser.add_argument('event_files', nargs='+', help='Event files or directories')
    parser.add_argument('--as_of', help='Filter observations up to this timestamp (ISO-8601)')
    parser.add_argument('--out', default='knowledge/associations.json', help='Output file path')
    args = parser.parse_args()
    materialize_associations(args.event_files, args.as_of, args.out)


if __name__ == '__main__':
    main()
