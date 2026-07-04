import json
import os
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from tools.workflow.project_findings import _confidence, _confidence_label, HIGH_CONFIDENCE_MIN_SAMPLES
from tools.workflow.project_associations import evidence_watermark

# Traced constants with rationale
CALIBRATION_MIN_BUCKET_N = 5  # Minimum samples per bucket to report bias; D18 determinism
CALIBRATION_MIN_TOTAL_PREDICTIONS = 10  # Minimum total scored predictions to propose calibration experiment

# Bucket boundaries: [0,0.2), [0.2,0.4), [0.4,0.6), [0.6,0.8), [0.8,1.0]
BUCKETS = [
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0)
]


def _bucket_index(confidence: float) -> Optional[int]:
    """Return the index of the bucket that confidence belongs to, or None if outside all."""
    for i, (low, high) in enumerate(BUCKETS):
        if low <= confidence < high:
            return i
    return None

def _evaluate_prediction(prediction: Dict[str, Any], next_observation: Dict[str, Any]) -> bool:
    """Evaluate if the next observation matches the prediction direction.
    Only known_good/known_bad findings are considered; others are excluded.
    Returns True if match, False otherwise.
    """
    if prediction.get("finding_type") not in ["known_good", "known_bad"]:
        return None  # Excluded
    
    expected_success = prediction["finding_type"] == "known_good"
    actual_success = next_observation.get("outcome") == "success"
    
    return expected_success == actual_success

def _compute_bucket_stats(predictions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute per-bucket statistics: n, observed_rate, curve_implied_rate, bias."""
    stats = []
    for i, (low, high) in enumerate(BUCKETS):
        bucket_predictions = [
            p for p in predictions 
            if _bucket_index(p["confidence_score"]) == i
        ]
        n = len(bucket_predictions)
        
        if n < CALIBRATION_MIN_BUCKET_N:
            stats.append({
                "bucket": f"[{low:.1f},{high:.1f})",
                "n": n,
                "observed_rate": None,
                "curve_implied_rate": None,
                "bias": None,
                "reason": "insufficient evidence"
            })
            continue
        
        # Compute observed rate: fraction of matches
        matches = 0
        total = 0
        for p in bucket_predictions:
            result = _evaluate_prediction(p, p["next_observation"])
            if result is not None:  # Only count valid predictions
                total += 1
                if result:
                    matches += 1
        observed_rate = matches / total if total > 0 else 0
        
        # Compute curve_implied_rate: mean confidence in bucket
        confidence_sum = sum(p["confidence_score"] for p in bucket_predictions)
        curve_implied_rate = confidence_sum / len(bucket_predictions)
        
        # Bias = observed_rate - curve_implied_rate
        bias = observed_rate - curve_implied_rate
        
        stats.append({
            "bucket": f"[{low:.1f},{high:.1f})",
            "n": n,
            "observed_rate": round(observed_rate, 3),
            "curve_implied_rate": round(curve_implied_rate, 3),
            "bias": round(bias, 3),
            "reason": None
        })
    
    return stats

def _replay_findings(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replay findings on the prefix of observations in evidence-time order.
    Returns list of predictions with confidence_score and next_observation.
    """
    # Sort by timestamp
    sorted_obs = sorted(observations, key=lambda x: x["timestamp"])
    
    # Track per-subject findings
    findings: Dict[str, Dict[str, Any]] = {}
    predictions: List[Dict[str, Any]] = []
    
    for i, obs in enumerate(sorted_obs):
        subject = obs["subject"]
        
        # Update subject's evidence
        if subject not in findings:
            findings[subject] = {
                "samples": 0,
                "successes": 0,
                "failures": 0,
                "last_observed": obs["timestamp"]
            }
        
        # Update counts
        findings[subject]["samples"] += 1
        if obs["outcome"] == "success":
            findings[subject]["successes"] += 1
        else:
            findings[subject]["failures"] += 1
        
        # Compute confidence
        confidence_score = _confidence(findings[subject]["samples"])
        
        # If this is not the last observation, predict next outcome
        if i < len(sorted_obs) - 1:
            next_obs = sorted_obs[i + 1]
            
            # Only predict if subject matches
            if next_obs["subject"] == subject:
                # Create prediction
                prediction = {
                    "subject": subject,
                    "confidence_score": confidence_score,
                    "finding_type": "known_good" if findings[subject]["successes"] > 0 else "known_bad",
                    "next_observation": next_obs,
                    "timestamp": obs["timestamp"]
                }
                predictions.append(prediction)
    
    return predictions

def run_calibration(event_sources: List[str], out_path: str) -> None:
    """Main entry point: load corpus, replay findings, compute calibration report, write to out_path.
    """
    # Load all observations from event sources
    all_observations: List[Dict[str, Any]] = []
    for source in event_sources:
        if os.path.isdir(source):
            for file in os.listdir(source):
                if file.endswith(".json"):
                    with open(os.path.join(source, file), "r") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_observations.extend(data)
                        else:
                            all_observations.append(data)
        else:
            with open(source, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    all_observations.extend(data)
                else:
                    all_observations.append(data)
    
    # Replay findings on prefix
    predictions = _replay_findings(all_observations)
    
    # Compute bucket stats
    stats = _compute_bucket_stats(predictions)
    
    # Get evidence watermark
    watermark = evidence_watermark(all_observations)
    
    # Build report
    report = {
        "corpus_watermark": watermark,
        "total_predictions": len(predictions),
        "buckets": stats
    }
    
    # Write to output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"Calibration report written to {out_path}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Project calibration report")
    parser.add_argument("event_sources", nargs="+", help="Directories or files containing observations")
    parser.add_argument("--out", required=True, help="Output path for calibration report")
    args = parser.parse_args()
    run_calibration(args.event_sources, args.out)

if __name__ == "__main__":
    main()
