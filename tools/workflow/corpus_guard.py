from pathlib import Path
import json
from typing import Any, Tuple, Optional


class CorpusRegressionError(Exception):
    """Raised when a projection attempts to regress the corpus."""
    pass

def extract_watermark_and_count(path: Path) -> Tuple[Optional[str], Optional[int]]:
    """Extract watermark and primary count from a JSON file.

    Returns:
        (watermark, primary_count) tuple. Both may be None.
    """
    if not path.exists():
        return None, None

    try:
        with open(path, 'r') as f:
            doc = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None, None

    # Extract watermark if present
    watermark = doc.get('evidence_watermark')
    if watermark is not None and not isinstance(watermark, str):
        watermark = None

    # Extract primary count based on file name
    primary_count = None
    if path.name == 'findings.json':
        primary_count = doc.get('observation_count')
    elif path.name == 'capabilities.json':
        primary_count = doc.get('capability_count')
    elif path.name == 'coverage.json':
        primary_count = doc.get('observation_count')
    elif path.name == 'capacity_estimates.json':
        primary_count = doc.get('observation_count')
    elif path.name == 'prediction_accuracy.json':
        primary_count = doc.get('observation_count')
    elif path.name == 'experiment_candidates.json':
        primary_count = doc.get('source_findings')
    elif path.name == 'experiment_results.json':
        primary_count = doc.get('plan_count')
    elif path.name == 'associations.json':
        primary_count = doc.get('association_count')

    # Ensure count is int or None
    if primary_count is not None and not isinstance(primary_count, int):
        primary_count = None

    return watermark, primary_count

def guard_write(path: Path, new_doc: Any, extract: callable) -> None:
    """Guard against regression in corpus projection.

    Args:
        path: Path to the output file.
        new_doc: The new document to write.
        extract: Function to extract (watermark, count) from a doc.

    Raises:
        CorpusRegressionError if the new doc regresses the corpus.
    """
    # Extract from old and new
    old_watermark, old_count = extract(path)
    new_watermark, new_count = extract(new_doc)

    # Check watermark regression: new < old
    if old_watermark is not None and new_watermark is not None:
        if new_watermark < old_watermark:
            raise CorpusRegressionError(
                f"Corpus regression detected: watermark {new_watermark} < {old_watermark}"
            )

    # Check count regression: new < old
    if old_count is not None and new_count is not None:
        if new_count < old_count:
            raise CorpusRegressionError(
                f"Corpus regression detected: count {new_count} < {old_count}"
            )

    # Check for override
    override_path = path.parent / "corpus_regression_override.json"
    if override_path.exists():
        try:
            with open(override_path, 'r') as f:
                override = json.load(f)
        except (json.JSONDecodeError, IOError):
            override = {}

        if override.get("active") is True:
            # Verify scope includes this file
            scope = override.get("scope", [])
            if path.name not in scope:
                raise CorpusRegressionError(
                    f"Override does not apply to {path.name}"
                )

            # Append audit record
            audit_path = path.parent / "policy_audit.ndjson"
            audit_entry = {
                "file": str(path.relative_to(path.parent)),
                "old_watermark": old_watermark,
                "new_watermark": new_watermark,
                "old_count": old_count,
                "new_count": new_count,
                "reason": override.get("reason", "no reason provided"),
                "author": override.get("author", "unknown")
            }
            try:
                with open(audit_path, 'a') as f:
                    f.write(json.dumps(audit_entry) + '\n')
            except (IOError, OSError):
                raise CorpusRegressionError("Failed to write audit record")

            # Deactivate override after batch
            override["active"] = False
            try:
                with open(override_path, 'w') as f:
                    json.dump(override, f, indent=2)
            except (IOError, OSError):
                raise CorpusRegressionError("Failed to deactivate override")

    # Write the file
    try:
        with open(path, 'w') as f:
            json.dump(new_doc, f, indent=2)
    except (IOError, OSError):
        raise CorpusRegressionError("Failed to write file")
