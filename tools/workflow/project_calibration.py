import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from tools.workflow.project_findings import _findings_from_events
from tools.workflow.project_associations import evidence_watermark

# Traced constants with rationale
CALIBRATION_MIN_BUCKET_N = 5  # Minimum samples per bucket to report bias (D18 determinism, threshold stability)
CALIBRATION_MIN_TOTAL_PREDICTIONS = 10  # Minimum total scored predictions to propose calibration experiment

# Bucket boundaries: [0,0.2), [0.2,0.4), [0.4,0.6), [0.6,0.8), [0.8,1.0]
BUCKET_BOUNDS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def _as_of_findings(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replay findings on event prefix, deterministically, in evidence-time order."
    findings = []
    seen_subjects = {}
    for event in sorted(events, key=lambda e: e['timestamp']):
        # Update subject state
        subject = event['subject']
        if subject not in seen_subjects:
            seen_subjects[subject] = {'successes': 0, 'failures': 0}

        # Update count
        if event['outcome'] == 'success':
            seen_subjects[subject]['successes'] += 1
        else:
            seen_subjects[subject]['failures'] += 1

        # Compute confidence before this event
        total = seen_subjects[subject]['successes'] + seen_subjects[subject]['failures']
        if total == 0:
            confidence_score = 0.0
        else:
            confidence_score = round(total / (total + 2), 2)

        # Determine prediction direction
        if seen_subjects[subject]['successes'] > seen_subjects[subject]['failures']:
            predicted = 'success'
        elif seen_subjects[subject]['successes'] < seen_subjects[subject]['failures']:
            predicted = 'failure'
        else:
            predicted = 'unknown'

        # Only include findings with a clear prediction
        if predicted != 'unknown':
            findings.append({
                'subject': subject,
                'confidence_score': confidence_score,
                'predicted': predicted,
                'timestamp': event['timestamp'],
                'samples': total
            })
    return findings

def _bucket_index(confidence: float) -> int:
    """Return bucket index for confidence score."
    for i in range(len(BUCKET_BOUNDS) - 1):
        if BUCKET_BOUNDS[i] <= confidence < BUCKET_BOUNDS[i + 1]:
            return i
    return len(BUCKET_BOUNDS) - 2  # fallback to last bucket

def _compute_bias_report(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute calibration bias report from events."
    # Reconstruct findings before each observation
    findings = _as_of_findings(events)
    
    # Group by bucket
    buckets = [[] for _ in range(len(BUCKET_BOUNDS) - 1)]
    
    for finding in findings:
        # Only use findings that predict a direction
        if finding['predicted'] == 'unknown':
            continue
        
        # Find the next observation for this subject
        subject = finding['subject']
        next_obs = None
        for event in events:
            if event['subject'] == subject and event['timestamp'] > finding['timestamp']:
                next_obs = event
                break
        
        if next_obs is None:
            continue  # No next observation
        
        # Determine if prediction was correct
        correct = (next_obs['outcome'] == finding['predicted'])
        
        # Bucket the prediction
        bucket_idx = _bucket_index(finding['confidence_score'])
        buckets[bucket_idx].append({
            'correct': correct,
            'confidence_score': finding['confidence_score']
        })
    
    # Compute per-bucket stats
    results = []
    total_predictions = 0
    for i, bucket in enumerate(buckets):
        n = len(bucket)
        total_predictions += n
        if n < CALIBRATION_MIN_BUCKET_N:
            results.append({
                'bucket': f'[{BUCKET_BOUNDS[i]:.1f},{BUCKET_BOUNDS[i+1]:.1f})',
                'n': n,
                'observed_rate': None,
                'curve_implied_rate': None,
                'bias': None,
                'status': 'insufficient evidence'
            })
        else:
            observed_rate = sum(1 for x in bucket if x['correct']) / n
            curve_implied_rate = sum(x['confidence_score'] for x in bucket) / n
            bias = observed_rate - curve_implied_rate
            results.append({
                'bucket': f'[{BUCKET_BOUNDS[i]:.1f},{BUCKET_BOUNDS[i+1]:.1f})',
                'n': n,
                'observed_rate': round(observed_rate, 3),
                'curve_implied_rate': round(curve_implied_rate, 3),
                'bias': round(bias, 3),
                'status': 'valid'
            })
    
    # Add watermark
    watermark = evidence_watermark(events)
    
    return {
        'corpus_watermark': watermark,
        'total_predictions': total_predictions,
        'buckets': results
    }

def main(event_sources: List[str], out_path: str):
    """Main entry point for calibration projector."
    events = []
    for source in event_sources:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Event source not found: {source}")
        
        # Read all JSON files in source
        for file_path in source_path.iterdir():
            if file_path.suffix == '.json':
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        events.extend(data)
                    else:
                        events.append(data)
    
    # Compute report
    report = _compute_bias_report(events)
    
    # Write output
    out_dir = Path(out_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"Calibration report written to {out_path}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Project calibration report')
    parser.add_argument('event_sources', nargs='+', help='Directories containing event JSON files')
    parser.add_argument('--out', required=True, help='Output path for report')
    args = parser.parse_args()
    main(args.event_sources, args.out)