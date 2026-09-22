"""Compile an untrusted foreman proposal into a source-pinned workboard.

The foreman model is useful for decomposition and review hypotheses, but it is
not an authority on repository state.  This module is deliberately small and
has no provider, scheduler, or model dependency:

* Git supplies the committed source bytes and their manifest.
* The caller supplies repository identity and non-negotiable acceptance policy.
* A model may supply only bounded task proposals over that manifest.
* A JSON checkpoint, not a KV snapshot, is the durable source of truth.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from hearth.operator import canonical

SOURCE_MANIFEST_SCHEMA = "foreman-source-manifest.v1"
PROPOSAL_SCHEMA = "foreman-proposal.v1"
WORKBOARD_SCHEMA = "foreman-workboard.v1"
CHECKPOINT_SCHEMA = "foreman-checkpoint.v1"
COMPILER_VERSION = "foreman-compiler.v1"

LANE_CONTEXT_LIMITS = {"fast": 65_536, "deep": 131_072}
ARTIFACT_KINDS = frozenset({"markdown", "json", "whole_file", "unified_diff"})
FORBIDDEN_PROPOSAL_FIELDS = frozenset({
    "repo", "base_commit", "source_pack", "source_manifest", "checkpoint",
    "workboard", "route_profile", "provider", "model", "verdict",
})


class ForemanProposalError(ValueError):
    """A proposal, source manifest, or checkpoint failed a fail-closed check."""


def _sha256(data: bytes) -> str:
    return canonical.sha256_hex(data)


def _canonical_digest(document: Mapping[str, Any]) -> str:
    return _sha256(canonical.canonical_json(dict(document)))


def _clean_repo_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ForemanProposalError(f"{label} must be a non-empty repository-relative path")
    if "\\" in value or ":" in value:
        raise ForemanProposalError(f"{label} must use a portable slash-separated repository-relative path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ForemanProposalError(f"{label} escapes the repository: {value!r}")
    normalized = candidate.as_posix()
    if normalized != value:
        raise ForemanProposalError(f"{label} is not normalized: {value!r}")
    return normalized


def _git(repo: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = "git executable unavailable" if isinstance(exc, OSError) else exc.stderr.decode(
            "utf-8", "replace").strip()
        raise ForemanProposalError(f"cannot read pinned Git source: {detail or 'git failed'}") from exc
    return completed.stdout


def _resolve_commit(repo: Path, base_commit: str) -> str:
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise ForemanProposalError("base_commit must be a non-empty Git revision")
    return _git(repo, "rev-parse", "--verify", f"{base_commit}^{{commit}}").decode("ascii").strip()


def build_source_manifest(repo: Path | str, base_commit: str,
                          files: Iterable[str]) -> dict[str, Any]:
    """Return a digestable manifest of blobs from a named Git commit.

    No worktree read is used after repository resolution.  A dirty checkout
    therefore cannot alter source identity or silently change a model briefing.
    """
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ForemanProposalError(f"repository does not exist: {root}")
    commit = _resolve_commit(root, base_commit)
    cleaned = sorted({_clean_repo_path(path, label="source file") for path in files})
    if not cleaned:
        raise ForemanProposalError("source manifest requires at least one file")

    records: list[dict[str, Any]] = []
    for path in cleaned:
        blob = _git(root, "show", f"{commit}:{path}")
        records.append({"path": path, "sha256": _sha256(blob), "size": len(blob)})

    binding = {"base_commit": commit, "files": records}
    return {
        "schema": SOURCE_MANIFEST_SCHEMA,
        **binding,
        "sha256": _canonical_digest(binding),
    }


def _verify_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != SOURCE_MANIFEST_SCHEMA:
        raise ForemanProposalError("source manifest has an unsupported schema")
    commit = manifest.get("base_commit")
    entries = manifest.get("files")
    declared = manifest.get("sha256")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ForemanProposalError("source manifest requires a resolved 40-character base_commit")
    if not isinstance(entries, list) or not entries:
        raise ForemanProposalError("source manifest requires a non-empty files list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ForemanProposalError(f"source manifest files[{index}] must be an object")
        path = _clean_repo_path(entry.get("path"), label=f"source manifest files[{index}].path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if path in seen:
            raise ForemanProposalError(f"source manifest repeats {path!r}")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ForemanProposalError(f"source manifest has invalid sha256 for {path!r}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ForemanProposalError(f"source manifest has invalid size for {path!r}")
        seen.add(path)
        normalized.append({"path": path, "sha256": digest, "size": size})
    binding = {"base_commit": commit, "files": sorted(normalized, key=lambda row: row["path"])}
    computed = _canonical_digest(binding)
    if declared != computed:
        raise ForemanProposalError(
            f"source manifest sha256 mismatch: declared {declared!r}, computed {computed}")
    return {"schema": SOURCE_MANIFEST_SCHEMA, **binding, "sha256": computed}


def _nonempty_strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ForemanProposalError(f"{label} must be a non-empty list of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ForemanProposalError(f"{label} must contain non-empty strings only")
    return [item.strip() for item in value]


def _compile_envelope(raw: Any, *, available_files: set[str],
                      mandatory_criteria: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ForemanProposalError("each task envelope must be an object")
    required = {
        "id", "intent", "artifact_kind", "files", "acceptance_criteria",
        "requested_lane", "max_input_tokens", "output_reserve_tokens", "reviewer_notes",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ForemanProposalError(f"task envelope is missing required field(s): {', '.join(missing)}")
    identifier = raw["id"]
    if (not isinstance(identifier, str) or not identifier or len(identifier) > 64
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in identifier)):
        raise ForemanProposalError("task envelope id must be a lowercase stable identifier")
    intent = raw["intent"]
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 4000:
        raise ForemanProposalError(f"task envelope {identifier!r} has an invalid intent")
    kind = raw["artifact_kind"]
    if kind not in ARTIFACT_KINDS:
        raise ForemanProposalError(f"task envelope {identifier!r} has unsupported artifact_kind {kind!r}")
    lane = raw["requested_lane"]
    if lane not in LANE_CONTEXT_LIMITS:
        raise ForemanProposalError(f"task envelope {identifier!r} has unsupported lane {lane!r}")
    max_input = raw["max_input_tokens"]
    reserve = raw["output_reserve_tokens"]
    if (not isinstance(max_input, int) or isinstance(max_input, bool) or max_input < 1
            or not isinstance(reserve, int) or isinstance(reserve, bool) or reserve < 1
            or max_input + reserve > LANE_CONTEXT_LIMITS[lane]):
        raise ForemanProposalError(f"task envelope {identifier!r} does not fit its exact lane context")
    files = [_clean_repo_path(value, label=f"task envelope {identifier!r}.files")
             for value in _nonempty_strings(raw["files"], label=f"task envelope {identifier!r}.files")]
    if len(files) != len(set(files)):
        raise ForemanProposalError(f"task envelope {identifier!r} repeats a source file")
    missing_files = sorted(set(files) - available_files)
    if missing_files:
        raise ForemanProposalError(
            f"task envelope {identifier!r} names files outside the pinned manifest: {', '.join(missing_files)}")
    criteria = _nonempty_strings(raw["acceptance_criteria"], label=f"task envelope {identifier!r}.acceptance_criteria")
    if len(criteria) != len(set(criteria)):
        raise ForemanProposalError(f"task envelope {identifier!r} repeats an acceptance criterion")
    required_criteria = set(mandatory_criteria.get(identifier, ()))
    absent = sorted(required_criteria - set(criteria))
    if absent:
        raise ForemanProposalError(
            f"task envelope {identifier!r} omits mandatory criterion: {absent[0]}")
    reviewer_notes = raw["reviewer_notes"]
    if not isinstance(reviewer_notes, str) or not reviewer_notes.strip() or len(reviewer_notes) > 4000:
        raise ForemanProposalError(f"task envelope {identifier!r} has invalid reviewer_notes")
    target = raw.get("target_path")
    if target is not None:
        target = _clean_repo_path(target, label=f"task envelope {identifier!r}.target_path")
    if kind in {"whole_file", "unified_diff"} and target is None:
        raise ForemanProposalError(f"task envelope {identifier!r} requires target_path for {kind}")
    return {
        "id": identifier,
        "intent": intent.strip(),
        "artifact_kind": kind,
        "target_path": target,
        "files": files,
        "acceptance_criteria": criteria,
        "requested_lane": lane,
        "max_input_tokens": max_input,
        "output_reserve_tokens": reserve,
        "reviewer_notes": reviewer_notes.strip(),
    }


def compile_workboard(proposal: Mapping[str, Any], *, repo: Path | str,
                      source_manifest: Mapping[str, Any], goal: str,
                      mandatory_criteria: Mapping[str, Iterable[str]] | None = None) -> dict[str, Any]:
    """Validate a model proposal and bind it to caller-owned source identity.

    The returned workboard never copies a model-provided repository, commit, or
    source digest.  Their presence in a proposal is a refusal, not a hint.
    """
    if not isinstance(proposal, Mapping) or proposal.get("schema") != PROPOSAL_SCHEMA:
        raise ForemanProposalError(f"proposal must declare schema {PROPOSAL_SCHEMA!r}")
    forbidden = sorted(FORBIDDEN_PROPOSAL_FIELDS & set(proposal))
    if forbidden:
        raise ForemanProposalError(
            f"proposal attempts to author control-plane field(s): {', '.join(forbidden)}")
    session_id = proposal.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise ForemanProposalError("proposal requires a bounded non-empty session_id")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
        raise ForemanProposalError("caller-owned goal must be a bounded non-empty string")
    manifest = _verify_manifest(source_manifest)
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ForemanProposalError(f"repository does not exist: {root}")
    raw_envelopes = proposal.get("task_envelopes")
    if not isinstance(raw_envelopes, list) or not 1 <= len(raw_envelopes) <= 8:
        raise ForemanProposalError("proposal requires one to eight task_envelopes")
    policy = mandatory_criteria or {}
    available = {entry["path"] for entry in manifest["files"]}
    envelopes = [_compile_envelope(item, available_files=available, mandatory_criteria=policy)
                 for item in raw_envelopes]
    identifiers = [item["id"] for item in envelopes]
    if len(identifiers) != len(set(identifiers)):
        raise ForemanProposalError("proposal repeats a task envelope id")
    write_targets = [item["target_path"] for item in envelopes if item["target_path"]]
    if len(write_targets) != len(set(write_targets)):
        raise ForemanProposalError("proposal has overlapping write targets")
    reviewer_notes = proposal.get("reviewer_notes", "")
    if not isinstance(reviewer_notes, str) or len(reviewer_notes) > 4000:
        raise ForemanProposalError("proposal reviewer_notes must be a bounded string")
    next_decision = proposal.get("next_decision", "")
    if not isinstance(next_decision, str) or not next_decision.strip() or len(next_decision) > 4000:
        raise ForemanProposalError("proposal requires bounded non-empty next_decision")

    proposal_copy = copy.deepcopy(dict(proposal))
    proposal_sha256 = _canonical_digest(proposal_copy)
    return {
        "schema": WORKBOARD_SCHEMA,
        "compiler_version": COMPILER_VERSION,
        "proposal_sha256": proposal_sha256,
        "session_id": session_id.strip(),
        "goal": goal.strip(),
        "repo": str(root),
        "base_commit": manifest["base_commit"],
        "source_pack": {
            "sha256": manifest["sha256"],
            "files": copy.deepcopy(manifest["files"]),
        },
        "task_envelopes": envelopes,
        "reviewer_notes": reviewer_notes.strip(),
        "next_decision": next_decision.strip(),
    }


def _verify_compiled_workboard(workboard: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse a forged workboard before it can become a durable checkpoint."""
    if not isinstance(workboard, Mapping) or workboard.get("schema") != WORKBOARD_SCHEMA:
        raise ForemanProposalError("checkpoint requires a compiled foreman-workboard.v1")
    if workboard.get("compiler_version") != COMPILER_VERSION:
        raise ForemanProposalError("compiled workboard has an unsupported compiler_version")
    source_pack = workboard.get("source_pack")
    if not isinstance(source_pack, Mapping):
        raise ForemanProposalError("compiled workboard has no source_pack")
    manifest = _verify_manifest({
        "schema": SOURCE_MANIFEST_SCHEMA,
        "base_commit": workboard.get("base_commit"),
        "files": source_pack.get("files"),
        "sha256": source_pack.get("sha256"),
    })
    raw_envelopes = workboard.get("task_envelopes")
    if not isinstance(raw_envelopes, list) or not raw_envelopes:
        raise ForemanProposalError("compiled workboard has no checkpointable task envelopes")
    identifiers = [item.get("id") for item in raw_envelopes if isinstance(item, Mapping)]
    if len(identifiers) != len(raw_envelopes) or any(not isinstance(item, str) for item in identifiers):
        raise ForemanProposalError("compiled workboard has malformed task envelope ids")
    if len(identifiers) != len(set(identifiers)):
        raise ForemanProposalError("compiled workboard repeats a task envelope id")
    normalized = copy.deepcopy(dict(workboard))
    normalized["base_commit"] = manifest["base_commit"]
    normalized["source_pack"] = {"sha256": manifest["sha256"], "files": manifest["files"]}
    return normalized


def make_checkpoint(workboard: Mapping[str, Any], *,
                    accepted_artifacts: Iterable[Mapping[str, Any]] = (),
                    rejected_artifacts: Iterable[Mapping[str, Any]] = (),
                    evidence_pointers: Iterable[str] = (),
                    next_decision: str | None = None,
                    kv_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a durable JSON checkpoint; KV state is explicitly cache-only."""
    verified_workboard = _verify_compiled_workboard(workboard)
    source_pack = verified_workboard["source_pack"]
    pending = [item.get("id") for item in verified_workboard.get("task_envelopes", [])
               if isinstance(item, Mapping) and isinstance(item.get("id"), str)]
    pointers = [_clean_repo_path(pointer, label="evidence pointer")
                for pointer in evidence_pointers]
    if len(pointers) != len(set(pointers)):
        raise ForemanProposalError("checkpoint repeats an evidence pointer")
    decision = next_decision if next_decision is not None else verified_workboard.get("next_decision")
    if not isinstance(decision, str) or not decision.strip():
        raise ForemanProposalError("checkpoint requires a non-empty next_decision")
    state = {"status": "not_saved", "source_of_truth": "json_checkpoint"}
    if kv_state is not None:
        state.update(copy.deepcopy(dict(kv_state)))
        state["source_of_truth"] = "json_checkpoint"
    binding = {
        "workboard_sha256": _canonical_digest(verified_workboard),
        "goal": verified_workboard.get("goal"),
        "base_commit": verified_workboard.get("base_commit"),
        "source_pack_sha256": source_pack["sha256"],
        "accepted_artifacts": [copy.deepcopy(dict(item)) for item in accepted_artifacts],
        "rejected_artifacts": [copy.deepcopy(dict(item)) for item in rejected_artifacts],
        "pending_envelopes": pending,
        "evidence_pointers": pointers,
        "next_decision": decision.strip(),
        "kv_state": state,
    }
    return {"schema": CHECKPOINT_SCHEMA, **binding, "checkpoint_sha256": _canonical_digest(binding)}


def write_checkpoint(path: Path | str, checkpoint: Mapping[str, Any]) -> Path:
    """Atomically write the caller-owned JSON source of truth for a foreman run."""
    if not isinstance(checkpoint, Mapping) or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise ForemanProposalError("cannot persist a non-checkpoint document")
    binding = {key: value for key, value in checkpoint.items() if key not in {"schema", "checkpoint_sha256"}}
    if checkpoint.get("checkpoint_sha256") != _canonical_digest(binding):
        raise ForemanProposalError("checkpoint_sha256 does not match checkpoint content")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".foreman-", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(checkpoint, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
