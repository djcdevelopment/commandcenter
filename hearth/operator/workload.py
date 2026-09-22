import hashlib
import json
from typing import Any, Dict, List, Optional

def workload_key(envelope: Dict[str, Any]) -> Optional[str]:
    """
    Return a deterministic lowercase SHA256 hex digest of a JSON projection containing exactly:
    - intent
    - acceptance_criteria
    - inputs
    - classification
    - max_context_tokens (taken from constraints)

    Uses sorted object keys, compact separators, ensure_ascii=True and allow_nan=False.
    Returns None if any validation fails.
    """
    # Validate envelope is a dict
    if not isinstance(envelope, dict):
        return None

    # Extract required fields
    intent = envelope.get("intent")
    acceptance_criteria = envelope.get("acceptance_criteria")
    inputs = envelope.get("inputs")
    classification = envelope.get("classification")
    constraints = envelope.get("constraints")

    # Validate intent is a nonempty string
    if not isinstance(intent, str) or not intent:
        return None

    # Validate acceptance_criteria is a nonempty list of nonempty strings
    if not isinstance(acceptance_criteria, list) or not acceptance_criteria:
        return None
    for ac in acceptance_criteria:
        if not isinstance(ac, str) or not ac:
            return None

    # Validate inputs
    if not isinstance(inputs, dict):
        return None
    repo = inputs.get("repo")
    base_commit = inputs.get("base_commit")
    paths = inputs.get("paths")
    files = inputs.get("files")

    if not isinstance(repo, str) or not repo:
        return None
    if not isinstance(base_commit, str) or len(base_commit) != 40 or not all(c in "0123456789abcdef" for c in base_commit):
        return None
    if not isinstance(paths, list):
        return None
    if not isinstance(files, list):
        return None
    for p in paths:
        if not isinstance(p, str):
            return None
    for f in files:
        if not isinstance(f, str):
            return None

    # Validate classification
    if not isinstance(classification, dict):
        return None
    required_fields = [
        "task_type", "repo_size", "language", "read_vs_reasoning",
        "context_continuity", "mutation_level", "risk_level"
    ]
    for field in required_fields:
        if not isinstance(classification.get(field), str) or not classification.get(field):
            return None

    # Validate constraints
    if not isinstance(constraints, dict):
        return None
    max_context_tokens = constraints.get("max_context_tokens")
    if type(max_context_tokens) is not int or max_context_tokens <= 0:
        return None

    # Build the JSON projection with only the required fields
    projection = {
        "intent": intent,
        "acceptance_criteria": acceptance_criteria,
        "inputs": inputs,
        "classification": classification,
        "max_context_tokens": max_context_tokens
    }

    # Serialize with sorted keys, compact separators, ensure_ascii=True, allow_nan=False
    try:
        json_str = json.dumps(projection, separators=(',', ':'), ensure_ascii=True, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError, OverflowError):
        return None

    # Compute SHA256 hash
    sha256_hash = hashlib.sha256(json_str.encode('utf-8')).hexdigest().lower()

    return sha256_hash
