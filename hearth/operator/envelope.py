"""TaskEnvelope normalization, hashing, and storage (WI-G2).

An orchestrator-neutral task specification. Identical task content submitted
by distinct callers or sessions produces the exact same `envelope_id`. Contains
no orchestrator identity.

D-115 as amended (WI-G2a): the frozen envelope keeps a concise, user-visible
`intent` capped at 4,000 characters — short enough for tracked storage and
readable by a cold agent. The COMPLETE raw prompt, when retention is permitted,
is a private content-addressed artifact referenced only by its digest, and
intent prose never appears in a history event payload. The WI-G2 candidate
copied the whole intent into the `task.received` payload, against the history
module's own stated rule; `store_envelope` now records the envelope identity,
the criteria count and the intent's digest instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hearth.operator import canonical, history, paths

CONTRACT_VERSION = "task-envelope.v1"
FORBIDDEN_REASONING_KEYS = frozenset({"thinking", "reasoning", "chain_of_thought"})

# D-115: "a concise, user-visible intent field capped at 4,000 characters".
MAX_INTENT_CHARS = 4000


class EnvelopeError(ValueError):
    """Raised when a task envelope is invalid or cannot be normalized."""


def _check_no_reasoning(data: Any, path: str = "$") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in FORBIDDEN_REASONING_KEYS:
                raise EnvelopeError(f"{path}: reasoning field {key!r} is forbidden (D-115)")
            _check_no_reasoning(value, f"{path}.{key}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _check_no_reasoning(item, f"{path}[{index}]")


def _enforce_contract(document: dict, *, label: str) -> dict:
    try:
        return canonical.validate_contract(document, CONTRACT_VERSION, label=label)
    except canonical.CanonicalError as exc:
        raise EnvelopeError(str(exc)) from exc


def build_envelope(
    intent: str,
    acceptance_criteria: list[str],
    inputs: dict[str, Any],
    classification: dict[str, Any],
    constraints: dict[str, Any],
    supersedes: Optional[str] = None,
    submitted_by: Optional[str] = None,
    submission_source: Optional[str] = None,
    submitted_at: Optional[str] = None,
) -> dict:
    """Build, validate, and stamp a caller-neutral TaskEnvelope."""
    if not isinstance(intent, str) or not intent.strip():
        raise EnvelopeError("intent must be a non-empty string")
    if len(intent) > MAX_INTENT_CHARS:
        raise EnvelopeError(
            f"intent is {len(intent)} characters; D-115 caps the frozen envelope's "
            f"user-visible intent at {MAX_INTENT_CHARS}. Keep the concise intent here "
            "and retain the complete prompt as a private content-addressed artifact "
            "(`store_envelope(..., raw_prompt=...)`), referenced by digest.")
    if not isinstance(acceptance_criteria, list) or not acceptance_criteria:
        raise EnvelopeError("acceptance_criteria must be a non-empty list of strings")
    if not isinstance(inputs, dict):
        raise EnvelopeError("inputs must be an object")
    if not isinstance(classification, dict):
        raise EnvelopeError("classification must be an object")
    if not isinstance(constraints, dict):
        raise EnvelopeError("constraints must be an object")

    _check_no_reasoning({
        "intent": intent,
        "acceptance_criteria": acceptance_criteria,
        "inputs": inputs,
        "classification": classification,
        "constraints": constraints,
    })

    now_ts = submitted_at or canonical.rfc3339(canonical.utc_now())

    raw_doc = {
        "contract_version": CONTRACT_VERSION,
        "intent": str(intent),
        "acceptance_criteria": [str(c) for c in acceptance_criteria],
        "inputs": {
            "repo": str(inputs.get("repo", "")),
            "base_commit": str(inputs.get("base_commit", "")),
            "paths": [str(p) for p in inputs.get("paths", [])],
            "files": [str(f) for f in inputs.get("files", [])],
        },
        "classification": {
            "task_type": str(classification.get("task_type", "engineering")),
            "repo_size": str(classification.get("repo_size", "standard")),
            "language": str(classification.get("language", "python")),
            "read_vs_reasoning": str(classification.get("read_vs_reasoning", "balanced")),
            "context_continuity": str(classification.get("context_continuity", "session")),
            "mutation_level": str(classification.get("mutation_level", "worktree")),
            "risk_level": str(classification.get("risk_level", "low")),
        },
        "constraints": {
            "deadline_s": int(constraints.get("deadline_s", 3600)),
            "max_attempts": int(constraints.get("max_attempts", 3)),
            "max_context_tokens": int(constraints.get("max_context_tokens", 32768)),
            "budget": constraints.get("budget"),
        },
        "supersedes": supersedes,
        "submitted_by": submitted_by,
        "submission_source": submission_source,
        "submitted_at": now_ts,
    }

    stamped = canonical.stamp_identity(raw_doc, "envelope_id")
    return _enforce_contract(stamped, label="built envelope")


def load_envelope(path: Path | str) -> dict:
    """Read an envelope document, enforcing its contract before its identity.

    Gate the contract version first: a `task-envelope.v2` document whose id
    happens to recompute is still a document this control plane cannot read.
    """
    target = Path(path)
    if not target.is_file():
        raise EnvelopeError(f"no envelope file at {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EnvelopeError(f"envelope at {target} is not valid JSON: {exc}") from exc
    _check_no_reasoning(data)
    _enforce_contract(data, label=str(target))
    if len(str(data.get("intent", ""))) > MAX_INTENT_CHARS:
        raise EnvelopeError(
            f"{target}: intent exceeds the D-115 cap of {MAX_INTENT_CHARS} characters")
    computed = canonical.identity_of(data, "envelope_id")
    declared = data.get("envelope_id")
    if declared != computed:
        raise EnvelopeError(f"envelope_id mismatch: declared {declared}, computed {computed}")
    return data


def store_envelope(envelope: dict, run_id: str, *,
                   raw_prompt: Optional[str | bytes] = None) -> Path:
    """Write the envelope to runs/operator/<run_id>/refs/ and record the event.

    The `task.received` payload carries identities, a count and digests only —
    never the intent prose. A raw prompt, when the caller retains one, is
    redacted under the D-115 policy, stored in the private content-addressed
    artifact store, and referenced by digest.
    """
    _enforce_contract(envelope, label="stored envelope")
    computed = canonical.identity_of(envelope, "envelope_id")
    if envelope.get("envelope_id") != computed:
        raise EnvelopeError(
            f"envelope_id mismatch: declared {envelope.get('envelope_id')}, "
            f"computed {computed}; refusing to store a document whose identity does "
            "not match its content")

    refs_dir = paths.run_refs_dir(run_id)
    refs_dir.mkdir(parents=True, exist_ok=True)
    target = refs_dir / "envelope.json"
    target.write_text(json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")

    payload = {
        "envelope_id": envelope["envelope_id"],
        "intent_sha256": canonical.sha256_hex(str(envelope["intent"]).encode("utf-8")),
        "intent_chars": len(str(envelope["intent"])),
        "criteria_count": len(envelope["acceptance_criteria"]),
        "submitted_by": envelope.get("submitted_by"),
    }
    refs = {"envelope_path": paths.repo_relative(target)}
    if raw_prompt is not None:
        from hearth.operator import artifacts

        blob = artifacts.store_private_blob(raw_prompt)
        payload["raw_prompt_sha256"] = blob["sha256"]
        payload["raw_prompt_bytes"] = blob["size"]
        payload["raw_prompt_reasoning_removed"] = blob["ingestion"]["reasoning_removed"]
        refs["raw_prompt_path"] = blob["path"]

    history.append("task.received", payload, refs=refs, run_id=run_id,
                   envelope_id=envelope["envelope_id"])
    return target
