import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_capacity import extract_observations
from tools.workflow.project_experiments import extract_experiment_results


def synthesize_findings(observations: List[Dict[str, Any]], as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """Synthesize findings from observations, optionally filtered by as_of timestamp."""
    # Filter observations if as_of is provided
    if as_of:
        observations = [o for o in observations if o.get('timestamp', '') <= as_of]

    findings = []
    for obs in observations:
        if obs['type'] == 'finding':
            finding = {
                'finding_id': obs['finding_id'],
                'confidence': obs['confidence'],
                'confidence_score': obs['confidence_score'],
                'type': obs['type'],
                'timestamp': obs['timestamp']
            }
            findings.append(finding)
    return findings


def materialize_findings(event_files: List[str], as_of: Optional[str] = None, out_path: str = 'knowledge/findings.json') -> None:
    """Materialize findings from event files, optionally filtered by as_of timestamp."""
    observations = extract_observations(event_files)
    findings = synthesize_findings(observations, as_of)
    with open(out_path, 'w') as f:
        json.dump(findings, f, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Materialize findings from event files.')
    parser.add_argument('event_files', nargs='+', help='Event files or directories')
    parser.add_argument('--as_of', help='Filter observations up to this timestamp (ISO-8601)')
    parser.add_argument('--out', default='knowledge/findings.json', help='Output file path')
    args = parser.parse_args()
    materialize_findings(args.event_files, args.as_of, args.out)


if __name__ == '__main__':
    main()
