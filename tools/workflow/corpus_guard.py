import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple, Optional


class CorpusRegressionError(Exception):
    """Raised when a projection attempts to regress the corpus watermark or count."""

    def __init__(self, path: Path, old_watermark: Optional[str], new_watermark: Optional[str],
                 old_count: Optional[int], new_count: Optional[int]):
        self.path = path
        self.old_watermark = old_watermark
        self.new_watermark = new_watermark
        self.old_count = old_count
        self.new_count = new_count
        super().__init__(
            f"Corpus regression detected: {path} watermark {old_watermark} → {new_watermark} (older), count {old_count} → {new_count} (smaller)"
        )


def extract_findings(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from findings.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("observation_count")
    return watermark, count

def extract_capabilities(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from capabilities.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("capability_count")
    return watermark, count

def extract_associations(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from associations.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("association_count")
    return watermark, count

def extract_coverage(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from coverage.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("observation_count")
    return watermark, count

def extract_capacity_estimates(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from capacity_estimates.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("observation_count")
    return watermark, count

def extract_prediction_accuracy(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from prediction_accuracy.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("observation_count")
    return watermark, count

def extract_experiment_candidates(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from experiment_candidates.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("source_findings")
    return watermark, count

def extract_experiment_results(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from experiment_results.json."""
    watermark = doc.get("evidence_watermark")
    count = doc.get("plan_count")
    return watermark, count

def extract_known_good_models(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from known_good_models.json. Returns None for both."""
    return None, None

def extract_known_bad_models(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and count from known_bad_models.json. Returns None for both."""
    return None, None

def guard_write(path: Path, new_doc: Dict[str, Any], extract: callable) -> None:
    """Guard a write to a knowledge file against corpus regression.

    - Refuses write if new watermark is older than existing or new count is smaller.
    - If an override is active, permits the write and appends audit.
    - Override is deactivated after all files in its scope are written.
    """
    # Load existing file if it exists
    old_doc: Optional[Dict[str, Any]] = None
    if path.exists():
        with open(path, 'r') as f:
            old_doc = json.load(f)

    # Extract old and new watermark/count
    old_watermark, old_count = extract(old_doc) if old_doc else (None, None)
    new_watermark, new_count = extract(new_doc)

    # Check for regression
    regression = False
    if old_watermark is not None and new_watermark is not None:
        if new_watermark < old_watermark:
            regression = True
    if old_count is not None and new_count is not None:
        if new_count < old_count:
            regression = True

    # If regression detected and no override, raise
    if regression:
        override_path = path.parent / "corpus_regression_override.json"
        if override_path.exists():
            override = json.load(override_path)
            if override.get("active") is True:
                # Append audit
                audit_path = path.parent / "policy_audit.ndjson"
                with open(audit_path, 'a') as f:
                    audit_entry = {
                        "file": str(path.relative_to(path.parent)),
                        "old_watermark": old_watermark,
                        "new_watermark": new_watermark,
                        "old_count": old_count,
                        "new_count": new_count,
                        "reason": override.get("reason"),
                        "author": override.get("author"),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    f.write(json.dumps(audit_entry) + "\n")

                # Deactivate override
                override["active"] = False
                with open(override_path, 'w') as f:
                    json.dump(override, f, indent=2)
                return  # Permit write after audit
            else:
                # Override inactive, raise
                raise CorpusRegressionError(path, old_watermark, new_watermark, old_count, new_count)
        else:
            # No override, raise
            raise CorpusRegressionError(path, old_watermark, new_watermark, old_count, new_count)

    # No regression, permit write
    return

# Mapping of file to extractor
EXTRACTORS = {
    "findings.json": extract_findings,
    "capabilities.json": extract_capabilities,
    "associations.json": extract_associations,
    "coverage.json": extract_coverage,
    "capacity_estimates.json": extract_capacity_estimates,
    "prediction_accuracy.json": extract_prediction_accuracy,
    "experiment_candidates.json": extract_experiment_candidates,
    "experiment_results.json": extract_experiment_results,
    "known_good_models.json": extract_known_good_models,
    "known_bad_models.json": extract_known_bad_models,
}

# Default extractor for unknown files
DEFAULT_EXTRACTOR = extract_findings


def get_extractor(path: Path) -> callable:
    """Get the appropriate extractor for a given file."""
    return EXTRACTORS.get(path.name, DEFAULT_EXTRACTOR)
