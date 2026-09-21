"""Artifact reference tracking, digest verification, and ingestion policy (WI-G2).

Every artifact referenced by history has an artifact-record.v1 row: content
hash, media type, size, retention class, durability status, ingestion record,
and recovery locations with verification times.

D-114 retention classes: required, supporting, disposable.

D-115 as amended (WI-G2a): provider reasoning fields are discarded and
model-specific think blocks are removed by a VERSIONED redaction policy declared
in `hearth/etc/operator.toml` — not by a pattern hard-coded here, so changing the
policy is a reviewable diff that moves `policy_version`. Hidden reasoning is
never stored, including in the private artifact store: the WI-G2 candidate
sanitized str and dict payloads but wrote `bytes` through untouched, so a
`<think>` block arriving as bytes reached the store verbatim. Removal metadata
records exactly four things and never the removed content: `reasoning_removed`,
`removed_bytes`, `removed_names`, `policy_version`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from hearth.operator import canonical, history, paths

RETENTION_CLASSES = frozenset({"required", "supporting", "disposable"})
DURABILITY_STATES = frozenset({"local_only", "backed_up", "verified"})


class ArtifactError(ValueError):
    """Raised when artifact handling or ingestion violates policy."""


def redaction_policy() -> dict:
    """The versioned D-115 redaction policy from hearth/etc/operator.toml."""
    try:
        config = paths.operator_config()["ingestion"]
    except (KeyError, OSError) as exc:
        raise ArtifactError(
            "no [ingestion] policy in hearth/etc/operator.toml; refusing to ingest "
            "without a versioned redaction policy (D-115)") from exc
    blocks = config.get("redact_blocks") or []
    policy = {
        "policy_version": str(config["policy_version"]),
        "discard_fields": tuple(str(name) for name in config.get("discard_fields", ())),
        "blocks": tuple((str(row["name"]), str(row["open"]), str(row["close"]))
                        for row in blocks),
    }
    if not policy["blocks"]:
        raise ArtifactError(
            "[ingestion].redact_blocks is empty; a redaction policy that removes "
            "nothing is not a policy (D-115)")
    return policy


def _block_patterns() -> list[tuple[str, re.Pattern]]:
    return [(name, re.compile(re.escape(opener) + ".*?" + re.escape(closer), re.DOTALL))
            for name, opener, closer in redaction_policy()["blocks"]]


def sanitize_text(text: str) -> tuple[str, int, list[str]]:
    """Remove every declared think block. Returns (clean, removed_bytes, names)."""
    removed_bytes = 0
    removed_names: list[str] = []
    for name, pattern in _block_patterns():
        matches = pattern.findall(text)
        if matches:
            removed_names.append(name)
            removed_bytes += sum(len(match.encode("utf-8", "surrogateescape"))
                                 for match in matches)
            text = pattern.sub("", text)
    return text, removed_bytes, removed_names


def sanitize_payload(payload: Any) -> tuple[Any, int, list[str]]:
    """Strip provider reasoning fields and think blocks from a structured payload."""
    discard = redaction_policy()["discard_fields"]
    removed_bytes = 0
    removed_names: list[str] = []

    if isinstance(payload, dict):
        cleaned = {}
        for key, value in payload.items():
            if key in discard:
                removed_names.append(key)
                try:
                    removed_bytes += len(json.dumps(value).encode("utf-8"))
                except (TypeError, ValueError):
                    removed_bytes += len(str(value).encode("utf-8"))
                continue
            sub_clean, sub_bytes, sub_names = sanitize_payload(value)
            cleaned[key] = sub_clean
            removed_bytes += sub_bytes
            removed_names.extend(sub_names)
        return cleaned, removed_bytes, removed_names
    if isinstance(payload, list):
        cleaned_list = []
        for item in payload:
            sub_clean, sub_bytes, sub_names = sanitize_payload(item)
            cleaned_list.append(sub_clean)
            removed_bytes += sub_bytes
            removed_names.extend(sub_names)
        return cleaned_list, removed_bytes, removed_names
    if isinstance(payload, str):
        return sanitize_text(payload)
    return payload, 0, []


def sanitize_bytes(data: bytes) -> tuple[bytes, int, list[str]]:
    """Redact think blocks inside raw bytes.

    Decoded with `surrogateescape` so arbitrary binary survives the round trip
    unchanged while text patterns are still removed: a reasoning block does not
    become invisible by arriving as bytes.
    """
    text = data.decode("utf-8", "surrogateescape")
    cleaned, removed_bytes, removed_names = sanitize_text(text)
    return cleaned.encode("utf-8", "surrogateescape"), removed_bytes, removed_names


def _ingestion_record(removed_bytes: int, removed_names: list[str]) -> dict:
    """Exactly four keys, none of which can carry removed content (D-115)."""
    return {
        "reasoning_removed": bool(removed_names),
        "removed_bytes": int(removed_bytes),
        "removed_names": sorted(set(removed_names)),
        "policy_version": redaction_policy()["policy_version"],
    }


def store_private_blob(data: bytes | str | dict | list) -> dict:
    """Redact, then write to the private content-addressed store.

    Returns ``{sha256, size, path, ingestion}``. Records no history event: the
    caller decides which event references the digest.
    """
    if isinstance(data, (dict, list)):
        cleaned, removed_bytes, removed_names = sanitize_payload(data)
        raw_bytes = json.dumps(cleaned, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
    elif isinstance(data, str):
        cleaned_str, removed_bytes, removed_names = sanitize_text(data)
        raw_bytes = cleaned_str.encode("utf-8")
    elif isinstance(data, bytes):
        raw_bytes, removed_bytes, removed_names = sanitize_bytes(data)
    else:
        raw_bytes, removed_bytes, removed_names = sanitize_bytes(str(data).encode("utf-8"))

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    store_dir = paths.raw_artifacts_dir()
    store_dir.mkdir(parents=True, exist_ok=True)
    blob_path = store_dir / sha256
    if not blob_path.exists():
        blob_path.write_bytes(raw_bytes)
    return {"sha256": sha256, "size": len(raw_bytes),
            "path": paths.repo_relative(blob_path),
            "ingestion": _ingestion_record(removed_bytes, removed_names)}


def ingest_artifact(
    run_id: str,
    data: bytes | str | dict,
    media_type: str,
    *,
    retention_class: str = "required",
    durability: str = "local_only",
    recovery_locations: Optional[list[dict]] = None,
) -> dict:
    """Ingest an artifact, enforce D-115 redaction, store it content-addressed,
    and record it in the run's artifact index plus one `artifact.produced` row."""
    if retention_class not in RETENTION_CLASSES:
        raise ArtifactError(f"invalid retention_class: {retention_class}")
    if durability not in DURABILITY_STATES:
        raise ArtifactError(f"invalid durability: {durability}")

    blob = store_private_blob(data)
    record = {
        "contract_version": "artifact-record.v1",
        "sha256": blob["sha256"],
        "media_type": media_type,
        "size": blob["size"],
        "retention_class": retention_class,
        "durability": durability,
        "created_at": canonical.rfc3339(canonical.utc_now()),
        "recovery_locations": list(recovery_locations or []),
        "ingestion": blob["ingestion"],
    }

    index_path = paths.run_artifacts_index_path(run_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ArtifactError(
                f"artifact index {index_path} is not valid JSON ({exc}); refusing to "
                "overwrite a damaged index") from exc

    index = [row for row in index if row.get("sha256") != blob["sha256"]]
    index.append(record)
    index.sort(key=lambda row: row["sha256"])
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    history.append(
        "artifact.produced",
        {
            "sha256": blob["sha256"],
            "media_type": media_type,
            "size": blob["size"],
            "retention_class": retention_class,
            "durability": durability,
            "reasoning_removed": blob["ingestion"]["reasoning_removed"],
            "removed_bytes": blob["ingestion"]["removed_bytes"],
            "redaction_policy_version": blob["ingestion"]["policy_version"],
        },
        refs={"artifact_store_path": blob["path"]},
        run_id=run_id,
    )
    return record


def load_artifact_index(run_id: str) -> list[dict]:
    index_path = paths.run_artifacts_index_path(run_id)
    if not index_path.is_file():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ArtifactError(f"artifact index {index_path} is not valid JSON ({exc})") from exc


def read_artifact_bytes(sha256: str) -> Optional[bytes]:
    blob_path = paths.raw_artifacts_dir() / sha256
    if blob_path.is_file():
        return blob_path.read_bytes()
    return None


def verify_digests(run_id: str) -> list[dict]:
    """Recompute every indexed artifact's digest against the stored blob.

    This is what lets a projection say "digest verified" and mean it: the
    WI-G2 candidate printed "hashes verified" where nothing had verified a hash.
    """
    report = []
    for record in load_artifact_index(run_id):
        sha256 = str(record.get("sha256", ""))
        blob = read_artifact_bytes(sha256)
        report.append({
            "sha256": sha256,
            "retention_class": record.get("retention_class"),
            "durability": record.get("durability"),
            "present": blob is not None,
            "digest_matches": blob is not None
            and hashlib.sha256(blob).hexdigest() == sha256,
            "recovery_locations": [loc for loc in record.get("recovery_locations", [])
                                   if loc.get("verified_at")],
        })
    return report
