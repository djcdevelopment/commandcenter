import json
import math


def summarize_decision(record):
    # Check if record is None or not a dict
    if not isinstance(record, dict):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check for required keys
    if "judgments" not in record:
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    judgments = record["judgments"]

    # Check if judgments is a list
    if not isinstance(judgments, list):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check that judgments list has 1 to 4 entries
    if len(judgments) < 1 or len(judgments) > 4:
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check dispatched field
    dispatched = record.get("dispatched", False)
    if not isinstance(dispatched, bool):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check candidate_id
    candidate_id = record.get("candidate_id")
    if candidate_id is not None and (not isinstance(candidate_id, str) or len(candidate_id) > 80):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check quality_history_sha256
    quality_history_sha256 = record.get("quality_history_sha256")
    if quality_history_sha256 is not None and (not isinstance(quality_history_sha256, str) or len(quality_history_sha256) != 64 or not all(c in "0123456789abcdef" for c in quality_history_sha256)):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Validate each judgment
    valid_judgments = []
    eligible_candidates = set()

    for judgment in judgments:
        if not isinstance(judgment, dict):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        if "candidate_id" not in judgment:
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        cid = judgment["candidate_id"]
        if not isinstance(cid, str) or len(cid) > 80 or not all(c.isalnum() or c in "-_" for c in cid):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        if "score" not in judgment:
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        score = judgment["score"]
        if not isinstance(score, (int, float)) or not (0 <= score <= 3) or not math.isfinite(score):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        if "confidence" not in judgment:
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        confidence = judgment["confidence"]
        if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1) or not math.isfinite(confidence):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        if "ambiguity" not in judgment:
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        ambiguity = judgment["ambiguity"]
        if not isinstance(ambiguity, (int, float)) or not (0 <= ambiguity <= 1) or not math.isfinite(ambiguity):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        if "eligible" not in judgment:
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        eligible = judgment["eligible"]
        if not isinstance(eligible, bool):
            return {
                "available": False,
                "reason": "missing_or_malformed_decision"
            }

        # Store eligible candidates
        if eligible:
            eligible_candidates.add(cid)

        # Build cleaned judgment
        valid_judgments.append({
            "candidate_id": cid,
            "score": score,
            "confidence": confidence,
            "ambiguity": ambiguity,
            "eligible": eligible
        })

    # Check candidate_id consistency
    if candidate_id is not None and candidate_id not in eligible_candidates:
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Check dispatched requirement
    if dispatched and (candidate_id is None or candidate_id not in eligible_candidates):
        return {
            "available": False,
            "reason": "missing_or_malformed_decision"
        }

    # Determine state
    if dispatched:
        state = "dispatched"
    elif candidate_id is not None:
        state = "selected"
    else:
        state = "held"

    # Build result
    result = {
        "available": True,
        "state": state,
        "candidate_id": candidate_id,
        "quality_history_sha256": quality_history_sha256,
        "judgments": valid_judgments,
        "fit_is_correctness": False
    }

    return result