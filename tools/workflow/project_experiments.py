import json
from typing import List, Dict, Any, Optional

from tools.workflow.project_associations import evidence_watermark
from tools.workflow.project_calibration import CALIBRATION_MIN_TOTAL_PREDICTIONS

# Traced constants
EXPERIMENT_TYPES = (
    "capacity_assessment",
    "coverage_assessment",
    "experiment_plan",
    "scheduler_decision",
    "capacity_observation",
    "workflow_event",
    "confidence_calibration"  # Added per stream
)


def _candidate() -> Dict[str, Any]:
    """Template for a new experiment candidate. DO NOT MODIFY."""
    return {
        "candidate_id": "",
        "experiment_type": "",
        "subject": "",
        "question": "",
        "worth": "",
        "evidence_sought": "",
        "risk_accepted": "",
        "confidence": 0.0,
        "last_observed": ""
    }

def _coverage_candidates(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Synthesize coverage assessment candidates. Pattern for new types."""
    candidates = []
    # ... existing logic ...
    return candidates

def _confidence_calibration_candidate(observations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Synthesize a confidence_calibration experiment candidate when thresholds are met.
    Returns None if not applicable.
    """
    # Count total scored predictions from calibration replay
    # We simulate the replay to count predictions
    findings: Dict[str, Dict[str, Any]] = {}
    total_predictions = 0
    
    # Sort observations by timestamp
    sorted_obs = sorted(observations, key=lambda x: x["timestamp"])
    
    for i, obs in enumerate(sorted_obs):
        subject = obs["subject"]
        
        # Initialize subject if not seen
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
        
        # If not last observation, predict next
        if i < len(sorted_obs) - 1:
            next_obs = sorted_obs[i + 1]
            if next_obs["subject"] == subject:
                total_predictions += 1
    
    # Check total predictions threshold
    if total_predictions < CALIBRATION_MIN_TOTAL_PREDICTIONS:
        return None
    
    # Check if a calibration report exists with matching watermark
    watermark = evidence_watermark(observations)
    report_path = "runs/calibration/calibration_report.json"
    
    if os.path.exists(report_path):
        try:
            with open(report_path, "r") as f:
                report = json.load(f)
                if report.get("corpus_watermark") == watermark:
                    return None  # Report is current
        except Exception:
            pass  # Ignore if file is invalid
    
    # Create candidate
    candidate = _candidate()
    candidate["candidate_id"] = "calibration-1"
    candidate["experiment_type"] = "confidence_calibration"
    candidate["subject"] = "confidence_curve_calibration"
    candidate["question"] = "does samples/(samples+2) predict actual next-observation reliability?"
    candidate["worth"] = "AUTHORED-VALUE-REQUIRED: Derek must assign worth; see BUILD-NOTES-Gc.md"
    candidate["evidence_sought"] = "the per-bucket table from calibration report"
    candidate["risk_accepted"] = "low"
    candidate["confidence"] = 0.5
    candidate["last_observed"] = watermark
    
    return candidate

def _synthesize_candidates(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Synthesize all experiment candidates from available observations."""
    candidates = []
    
    # Add coverage candidates
    candidates.extend(_coverage_candidates(observations))
    
    # Add confidence calibration candidate if applicable
    calib_candidate = _confidence_calibration_candidate(observations)
    if calib_candidate:
        candidates.append(calib_candidate)
    
    return candidates

def run_experiments(event_sources: List[str]) -> List[Dict[str, Any]]:
    """Main entry point: load observations, synthesize candidates, return list."""
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
    
    return _synthesize_candidates(all_observations)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Synthesize experiment candidates")
    parser.add_argument("event_sources", nargs="+", help="Directories or files containing observations")
    args = parser.parse_args()
    candidates = run_experiments(args.event_sources)
    print(json.dumps(candidates, indent=2))

if __name__ == "__main__":
    main()
