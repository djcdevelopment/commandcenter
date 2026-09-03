"""Schema validation for MediaGen versioned contracts.

Enforces the Creator OS authority boundary: model output MUST validate against
a versioned JSON Schema before reaching any GPU execution runtime.  If validation
fails the job fails at the contract layer without touching hardware.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent
_CACHE: Dict[str, dict] = {}


def _load_schema(name: str) -> dict:
    """Load and cache a schema file from the schemas directory."""
    if name not in _CACHE:
        path = _SCHEMA_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"Schema not found: {path}")
        _CACHE[name] = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE[name]


# ---------------------------------------------------------------------------
# Schema registry — maps contract schema identifiers to file names
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, str] = {
    "mediagen.podcast-script.v1": "PodcastScript.v1.json",
    "mediagen.visual-storyboard.v1": "VisualStoryboard.v1.json",
    "mediagen.media-artifact.v1": "MediaArtifact.v1.json",
}


def validate(contract: dict, *, schema_id: Optional[str] = None) -> None:
    """Validate *contract* against the appropriate versioned schema.

    If *schema_id* is not provided it is read from ``contract["schema"]``.

    Raises:
        jsonschema.ValidationError: if the contract violates the schema.
        KeyError: if the schema identifier is unknown.
        FileNotFoundError: if the schema file is missing from disk.
    """
    if schema_id is None:
        schema_id = contract.get("schema")
        if not schema_id:
            raise KeyError("Contract has no 'schema' field and no schema_id was provided")

    filename = _REGISTRY.get(schema_id)
    if filename is None:
        raise KeyError(f"Unknown schema identifier: {schema_id!r}")

    schema = _load_schema(filename)
    jsonschema.validate(instance=contract, schema=schema)


def validate_file(path: str | Path, *, schema_id: Optional[str] = None) -> dict:
    """Load a JSON file and validate it.  Returns the parsed contract on success."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data, schema_id=schema_id)
    return data


def available_schemas() -> list[str]:
    """Return all registered schema identifiers."""
    return sorted(_REGISTRY.keys())
