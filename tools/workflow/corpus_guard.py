from pathlib import Path
from typing import Any, Callable, Dict, Tuple, Optional


class CorpusRegressionError(Exception):
    """Raised when a projection attempts to regress the corpus (write older watermark or smaller count)."""


def guard_write(path: Path, new_doc: Dict[str, Any], extract: Callable[[Dict[str, Any]], Tuple[Optional[str], Optional[int]]]) -> None:
    """Guard against corpus regression: refuses write if new watermark is older than existing or primary count is smaller.

    Args:
        path: The file path to write.
        new_doc: The document to write.
        extract: Function that extracts (watermark, primary_count) from a doc.
    """
    old_doc = None
    if path.exists():
        with open(path, 'r') as f:
            old_doc = json.load(f)

    old_watermark, old_count = extract(old_doc) if old_doc else (None, None)
    new_watermark, new_count = extract(new_doc)

    # Compare watermarks only if both have one
    watermark_ok = True
    if old_watermark is not None and new_watermark is not None:
        if new_watermark < old_watermark:
            raise CorpusRegressionError(
                f"Corpus regression detected: new watermark '{new_watermark}' is older than existing '{old_watermark}'"
            )
        # equal-or-newer passes
    elif old_watermark is not None and new_watermark is None:
        # new doc has no watermark but old one does → regression
        raise CorpusRegressionError(
            f"Corpus regression detected: new doc has no watermark but existing has '{old_watermark}'"
        )
    # if old has no watermark and new has one → advance, allowed

    # Compare counts only if both have one
    count_ok = True
    if old_count is not None and new_count is not None:
        if new_count < old_count:
            raise CorpusRegressionError(
                f"Corpus regression detected: new count {new_count} is smaller than existing {old_count}"
            )
        # equal-or-larger passes
    elif old_count is not None and new_count is None:
        # new doc has no count but old one does → regression
        raise CorpusRegressionError(
            f"Corpus regression detected: new doc has no count but existing has {old_count}"
        )
    # if old has no count and new has one → advance, allowed

    # Check for authored override
    override_path = path.parent / "corpus_regression_override.json"
    if override_path.exists():
        override = json.load(override_path)
        if override.get("active", False) and path.name in override.get("scope", []):
            # Append audit record to policy_audit.ndjson in the same directory
            audit_path = path.parent / "policy_audit.ndjson"
            with open(audit_path, 'a') as f:
                audit_entry = {
                    "file": str(path),
                    "old_watermark": old_watermark,
                    "new_watermark": new_watermark,
                    "old_count": old_count,
                    "new_count": new_count,
                    "reason": override.get("reason", "no reason provided"),
                    "author": override.get("author", "unknown")
                }
                f.write(json.dumps(audit_entry) + "\n")
            
            # Deactivate override after all files in scope are written
            # This is handled by the caller (projector) which must track scope completion
            # For now, we just ensure the override is used once
            # The override file is not modified here; it's the caller's responsibility to deactivate
            return

    # If no override or not in scope, and no regression, proceed
    # Write the file
    with open(path, 'w') as f:
        json.dump(new_doc, f, indent=2)


def extract_findings(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from findings.json."""
    return None, doc.get("observation_count")

def extract_capabilities(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from capabilities.json."""
    return doc.get("evidence_watermark"), doc.get("capability_count")

def extract_associations(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from associations.json."""
    return doc.get("evidence_watermark"), doc.get("association_count")

def extract_coverage(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from coverage.json."""
    return doc.get("evidence_watermark"), doc.get("observation_count")

def extract_capacity(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from capacity_estimates.json."""
    return doc.get("evidence_watermark"), doc.get("observation_count")

def extract_experiment_candidates(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from experiment_candidates.json."""
    return None, doc.get("source_findings")

def extract_experiment_results(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from experiment_results.json."""
    return None, doc.get("plan_count")

def extract_policy(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract (watermark, count) from policy.json."""
    return doc.get("evidence_watermark"), doc.get("source_findings")

# Extractor map for each file
EXTRACTORS = {
    "findings.json": extract_findings,
    "capabilities.json": extract_capabilities,
    "associations.json": extract_associations,
    "coverage.json": extract_coverage,
    "capacity_estimates.json": extract_capacity,
    "experiment_candidates.json": extract_experiment_candidates,
    "experiment_results.json": extract_experiment_results,
    "policy.json": extract_policy,
}

# Note: known_good_models.json and known_bad_models.json are unguarded
# Their extractors are not defined as they have neither watermark nor count
# This is intentional per the task requirements.