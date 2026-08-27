#!/usr/bin/env python3
"""Resumable, stdlib-only runner for the Qwen 3.8 HEARTH campaign.

The module is intentionally usable both as a CLI and from unit tests. It does
not start or stop the live HEARTH rung; PowerShell lifecycle scripts own that
operator boundary.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterable


SOURCE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_ROOT.parents[1]
CONFIG_PATH = SOURCE_ROOT / "config" / "campaign.json"
ARTIFACTS_PATH = SOURCE_ROOT / "config" / "artifacts.json"
TASKS_PATH = SOURCE_ROOT / "assay" / "tasks.json"
SUPPORTED_VALIDATORS = {
    "exact_text",
    "json_equal",
    "max_words_contains",
    "tool_call",
}
INVALID_TOKEN_RE = re.compile(r"<\|(?:im_start|im_end|endoftext|assistant|user)\|>", re.I)
FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.I | re.S)


def load_json(path: Path) -> Any:
    # Windows PowerShell 5.1 emits a UTF-8 BOM from -Encoding UTF8. Campaign
    # receipts cross that boundary, while source-controlled JSON is BOM-free.
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def campaign_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


def runtime_root(config: dict[str, Any] | None = None) -> Path:
    config = config or campaign_config()
    return Path(config["runtime_root"])


def _ensure_hearth_imports() -> None:
    """Make the command-center package importable from either source or its E: snapshot."""
    commandcenter = Path(campaign_config()["commandcenter_root"])
    if str(commandcenter) not in sys.path:
        sys.path.insert(0, str(commandcenter))


def _git_value(checkout: Path, *args: str) -> str | None:
    if not checkout.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _part_paths(artifact: dict[str, Any]) -> list[Path]:
    first = Path(artifact["path"])
    parts = int(artifact.get("parts", 1))
    if parts == 1:
        return [first]
    match = re.search(r"00001-of-(\d{5})", first.name)
    if not match or int(match.group(1)) != parts:
        raise ValueError(f"artifact {artifact['id']} has invalid split filename: {first.name}")
    return [
        first.with_name(first.name.replace(f"00001-of-{parts:05d}", f"{index:05d}-of-{parts:05d}"))
        for index in range(1, parts + 1)
    ]


def validate_sources() -> list[str]:
    errors: list[str] = []
    config = campaign_config()
    artifacts_doc = load_json(ARTIFACTS_PATH)
    tasks_doc = load_json(TASKS_PATH)

    if config.get("contract_version") != "qwen38-campaign.v1":
        errors.append("unexpected campaign contract_version")
    if config.get("device_filter") != "1,2":
        errors.append("device filter must remain 1,2 on OMEN")
    if float(config.get("safety", {}).get("commit_min_free_gb", 0)) < 6:
        errors.append("commit headroom floor must be at least 6 GB")
    if float(config.get("safety", {}).get("shared_growth_abort_gb", 99)) > 2:
        errors.append("shared-memory growth gate may not exceed 2 GB")
    safety = config.get("safety", {})
    abort_temperature = float(safety.get("vram_temperature_abort_c", 0))
    resume_temperature = float(safety.get("temperature_resume_below_c", math.inf))
    resume_samples = int(safety.get("temperature_resume_consecutive_samples", 0))
    resume_timeout = int(safety.get("temperature_resume_timeout_s", 0))
    sample_interval = int(safety.get("sample_interval_s", 0))
    if abort_temperature > 95:
        errors.append("VRAM temperature abort line may not exceed 95 C")
    if resume_temperature >= abort_temperature:
        errors.append("thermal resume line must remain below the abort line")
    if resume_samples < 2:
        errors.append("thermal resume requires at least two consecutive cool samples")
    if resume_timeout < sample_interval * resume_samples:
        errors.append("thermal resume timeout is too short for the configured samples")
    if config.get("matrix", {}).get("concurrency") != [1, 2, 4, 8, 12, 16, 24]:
        errors.append("performance concurrency ladder must remain 1,2,4,8,12,16,24")
    if config.get("matrix", {}).get("vision_concurrency") != [1, 4, 8]:
        errors.append("vision concurrency ladder must remain 1,4,8")
    frontier = config.get("frontier_reference", {})
    if frontier.get("backend") != "gcp-gemini-pro" or not str(frontier.get("model", "")).startswith("gemini-3.1-pro"):
        errors.append("frontier reference must remain pinned to the Gemini 3.1 Pro HEARTH rung")
    if int(frontier.get("max_output_tokens", 0)) < 16384:
        errors.append("Gemini 3.1 Pro needs the HEARTH-proven 16384-token reasoning headroom")
    for revision_name in ("support_revision", "mmv_patch_revision"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(config.get("engine", {}).get(revision_name, ""))):
            errors.append(f"engine {revision_name} must be a full Git SHA")

    artifact_ids: set[str] = set()
    artifacts = artifacts_doc.get("artifacts", [])
    for artifact in artifacts:
        artifact_id = artifact.get("id")
        if not artifact_id or artifact_id in artifact_ids:
            errors.append(f"missing or duplicate artifact id: {artifact_id!r}")
        artifact_ids.add(artifact_id)
        if not artifact.get("path") or not artifact.get("role"):
            errors.append(f"artifact {artifact_id!r} lacks path or role")
        if not artifact.get("quant"):
            errors.append(f"artifact {artifact_id!r} lacks an explicit quant/precision")
        if artifact.get("repo") and not re.fullmatch(r"[0-9a-f]{40}", str(artifact.get("revision", ""))):
            errors.append(f"artifact {artifact_id!r} must pin a full repository revision")
        try:
            _part_paths(artifact)
        except ValueError as exc:
            errors.append(str(exc))

    for name, topology in config.get("topologies", {}).items():
        if topology.get("candidate") not in artifact_ids:
            errors.append(f"topology {name} names unknown candidate {topology.get('candidate')}")
        ports: set[int] = set()
        for server in topology.get("servers", []):
            port = int(server.get("port", 0))
            if port <= 0 or port in ports:
                errors.append(f"topology {name} has invalid or duplicate port {port}")
            ports.add(port)
            if int(server.get("parallel", 0)) < 1 or int(server.get("slot_depth", 0)) < 1:
                errors.append(f"topology {name} has invalid parallel/slot_depth")

    expected_families = config.get("quality", {}).get("families", [])
    expected_per_family = int(config.get("quality", {}).get("tasks_per_family", 0))
    tasks = tasks_doc.get("tasks", [])
    ids: set[str] = set()
    counts = {family: 0 for family in expected_families}
    for task in tasks:
        task_id = task.get("id")
        family = task.get("family")
        if not task_id or task_id in ids:
            errors.append(f"missing or duplicate task id: {task_id!r}")
        ids.add(task_id)
        if family not in counts:
            errors.append(f"task {task_id} has unknown family {family!r}")
        else:
            counts[family] += 1
        validator = task.get("validator", {})
        if validator.get("type") not in SUPPORTED_VALIDATORS:
            errors.append(f"task {task_id} has unsupported validator {validator.get('type')!r}")
        if task.get("messages"):
            messages = task["messages"]
            if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
                errors.append(f"task {task_id} has an invalid multi-turn transcript")
            elif messages[-1].get("content") != task.get("prompt"):
                errors.append(f"task {task_id} prompt must match its final user turn")
        if task.get("image"):
            asset = TASKS_PATH.parent / task["image"]
            if not asset.exists():
                errors.append(f"task {task_id} image is missing: {asset}")
        for tool_name in task.get("tools", []):
            if tool_name not in tasks_doc.get("tools", {}):
                errors.append(f"task {task_id} references unknown tool {tool_name}")
    for family, count in counts.items():
        if count != expected_per_family:
            errors.append(f"family {family} has {count} tasks; expected {expected_per_family}")
    if len(tasks) != len(expected_families) * expected_per_family:
        errors.append(f"assay has {len(tasks)} tasks; expected {len(expected_families) * expected_per_family}")
    repeat_ids = set(config.get("quality", {}).get("repeat_task_ids", []))
    if not repeat_ids.issubset(ids):
        errors.append(f"repeat_task_ids missing from assay: {sorted(repeat_ids - ids)}")
    if not any(task.get("messages") for task in tasks):
        errors.append("assay must retain at least one multi-turn compatibility task")
    return errors


def init_runtime(force: bool = False) -> Path:
    errors = validate_sources()
    if errors:
        raise ValueError("source validation failed:\n- " + "\n- ".join(errors))
    config = campaign_config()
    root = runtime_root(config)
    for relative in (
        "control/config",
        "control/schema",
        "control/assay/assets",
        "control/scripts",
        "results/requests",
        "results/telemetry",
        "results/serverlogs",
        "results/quarantine",
        "results/receipts",
        "results/summaries",
        "state",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    copies = {
        CONFIG_PATH: root / "control/config/campaign.json",
        ARTIFACTS_PATH: root / "control/config/artifacts.json",
        TASKS_PATH: root / "control/assay/tasks.json",
        SOURCE_ROOT / "qwen38_campaign.py": root / "control/qwen38_campaign.py",
        SOURCE_ROOT / "README.md": root / "control/README.md",
        SOURCE_ROOT / "schema" / "request-row.v1.json": root / "control/schema/request-row.v1.json",
    }
    copies.update(
        {
            asset: root / "control/assay/assets" / asset.name
            for asset in (SOURCE_ROOT / "assay" / "assets").iterdir()
            if asset.is_file()
        }
    )
    copies.update(
        {
            script: root / "control/scripts" / script.name
            for script in (SOURCE_ROOT / "scripts").glob("*.ps1")
        }
    )
    for source, destination in copies.items():
        if destination.exists() and not force:
            if sha256_file(destination) != sha256_file(source):
                raise FileExistsError(
                    f"runtime control file differs: {destination}; rerun init --force to refresh control files"
                )
        shutil.copy2(source, destination)

    source_receipt = {
        "contract_version": "qwen38-source-receipt.v1",
        "created_at": utc_now(),
        "campaign_id": config["campaign_id"],
        "source_root": str(SOURCE_ROOT),
        "commandcenter_revision": _git_value(Path(config["commandcenter_root"]), "rev-parse", "HEAD"),
        "commandcenter_dirty": bool(_git_value(Path(config["commandcenter_root"]), "status", "--porcelain")),
        "campaign_config_sha256": sha256_file(CONFIG_PATH),
        "artifacts_config_sha256": sha256_file(ARTIFACTS_PATH),
        "task_set_sha256": sha256_file(TASKS_PATH),
        "source_tree_sha256": sha256_tree(SOURCE_ROOT),
    }
    (root / "state/source-receipt.json").write_text(
        json.dumps(source_receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return root


def lock_artifacts(allow_missing_optional: bool = True) -> dict[str, Any]:
    config = campaign_config()
    root = init_runtime(force=True)
    artifacts = load_json(ARTIFACTS_PATH)["artifacts"]
    locked: list[dict[str, Any]] = []
    missing_required: list[str] = []
    for artifact in artifacts:
        paths = _part_paths(artifact)
        missing = [path for path in paths if not path.is_file()]
        if missing:
            if artifact.get("required"):
                missing_required.extend(str(path) for path in missing)
            if artifact.get("required") or not allow_missing_optional:
                state = "missing"
            else:
                state = "optional-missing"
            locked.append({**artifact, "state": state, "missing": [str(path) for path in missing]})
            continue
        part_rows = [
            {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in paths
        ]
        locked.append(
            {
                **artifact,
                "state": "locked",
                "size_bytes": sum(row["size_bytes"] for row in part_rows),
                "parts_locked": part_rows,
            }
        )

    engine = config["engine"]
    engine_rows = []
    for role in ("campaign", "production"):
        checkout = Path(engine[f"{role}_checkout"])
        binary = Path(engine[f"{role}_binary"])
        if not binary.is_file():
            missing_required.append(str(binary))
        engine_rows.append(
            {
                "role": role,
                "checkout": str(checkout),
                "revision": _git_value(checkout, "rev-parse", "HEAD"),
                "dirty": bool(_git_value(checkout, "status", "--porcelain")),
                "binary": str(binary),
                "binary_sha256": sha256_file(binary) if binary.is_file() else None,
                "binary_size_bytes": binary.stat().st_size if binary.is_file() else None,
            }
        )
    if missing_required:
        raise FileNotFoundError("required campaign inputs are missing:\n- " + "\n- ".join(missing_required))

    manifest = {
        "contract_version": "qwen38-run-manifest.v1",
        "campaign_id": config["campaign_id"],
        "locked_at": utc_now(),
        "host": platform.node(),
        "platform": config["platform"],
        "hardware_profile_id": config["hardware_profile_id"],
        "frontier_reference": config["frontier_reference"],
        "python": sys.version,
        "os": platform.platform(),
        "machine": platform.machine(),
        "campaign_config_sha256": sha256_file(CONFIG_PATH),
        "artifacts_config_sha256": sha256_file(ARTIFACTS_PATH),
        "task_set_sha256": sha256_file(TASKS_PATH),
        "source_tree_sha256": sha256_tree(SOURCE_ROOT),
        "engines": engine_rows,
        "engine_receipt": (
            load_json(root / "state/engine-receipt.json")
            if (root / "state/engine-receipt.json").is_file()
            else None
        ),
        "artifacts": locked,
    }
    path = root / "state/run-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(*, rehash_artifacts: bool = False) -> list[str]:
    config = campaign_config()
    path = runtime_root(config) / "state/run-manifest.json"
    if not path.is_file():
        return [f"run manifest is missing: {path}"]
    manifest = load_json(path)
    errors: list[str] = []
    expected = {
        "campaign_config_sha256": sha256_file(CONFIG_PATH),
        "artifacts_config_sha256": sha256_file(ARTIFACTS_PATH),
        "task_set_sha256": sha256_file(TASKS_PATH),
        "source_tree_sha256": sha256_tree(SOURCE_ROOT),
    }
    for field, actual in expected.items():
        if manifest.get(field) != actual:
            errors.append(f"{field} drifted since lock")
    for engine in manifest.get("engines", []):
        checkout = Path(engine["checkout"])
        binary = Path(engine["binary"])
        if _git_value(checkout, "rev-parse", "HEAD") != engine.get("revision"):
            errors.append(f"{engine.get('role')} engine revision drifted")
        if not binary.is_file() or sha256_file(binary) != engine.get("binary_sha256"):
            errors.append(f"{engine.get('role')} engine binary drifted")
    for artifact in manifest.get("artifacts", []):
        if artifact.get("required") and artifact.get("state") != "locked":
            errors.append(f"required artifact {artifact.get('id')} was not locked")
        for part in artifact.get("parts_locked", []):
            part_path = Path(part["path"])
            if not part_path.is_file():
                errors.append(f"locked artifact part is missing: {part_path}")
                continue
            if part_path.stat().st_size != int(part["size_bytes"]):
                errors.append(f"locked artifact size drifted: {part_path}")
            elif rehash_artifacts and sha256_file(part_path) != part["sha256"]:
                errors.append(f"locked artifact bytes drifted: {part_path}")
    return errors


def _locked_input_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the model/engine identity that must not change across a resume patch."""
    engines = {
        str(row.get("role")): {
            "revision": row.get("revision"),
            "binary_sha256": row.get("binary_sha256"),
            "binary_size_bytes": row.get("binary_size_bytes"),
        }
        for row in manifest.get("engines", [])
    }
    artifacts = {
        str(row.get("id")): {
            "state": row.get("state"),
            "revision": row.get("revision"),
            "size_bytes": row.get("size_bytes"),
            "parts": [
                {
                    "path": part.get("path"),
                    "size_bytes": part.get("size_bytes"),
                    "sha256": part.get("sha256"),
                }
                for part in row.get("parts_locked", [])
            ],
        }
        for row in manifest.get("artifacts", [])
    }
    return {"engines": engines, "artifacts": artifacts}


GATE_BLOCK_KEYS = ("promotion", "quality", "generation")

# Legs whose watchdog abort may authorize a thermal quarantine. Both were reached
# on 2026-08-27: replica-per-card at a light cell, then the deep dual-context tier.
THERMAL_ABORT_STAGES = (
    "qwen27-replica-production-mtp-off-p512-c4",
    "dual-context-d131072-c1",
)


def _parse_iso_instant(value: str) -> dt.datetime:
    """Parse an ISO-8601 stamp to an absolute instant.

    PowerShell writes local time with an offset ('...-07:00') while Python writes
    UTC ('...Z'), and some stamps carry 7 fractional digits. Comparing these as
    strings silently compares different clocks, so normalize before any ordering.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    fractional = re.search(r"\.(\d+)", text)
    if fractional and len(fractional.group(1)) > 6:
        text = text.replace("." + fractional.group(1), "." + fractional.group(1)[:6], 1)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"cannot parse timestamp {value!r} to an absolute instant") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _canonical_block_sha256(config: dict[str, Any], keys: Iterable[str]) -> str:
    """Hash selected config blocks semantically, so CRLF/LF never masks a real change."""
    selected = {key: config.get(key) for key in keys}
    return sha256_value(selected)


def _config_at_revision(revision: str | None) -> dict[str, Any] | None:
    """Recover campaign.json as of a git revision, for archived-vs-current gate comparison."""
    if not revision:
        return None
    repo = Path(campaign_config()["commandcenter_root"])
    relative = CONFIG_PATH.resolve().relative_to(repo.resolve()).as_posix()
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:{relative}"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def build_resume_amendment(
    archived_manifest: dict[str, Any],
    current_manifest: dict[str, Any],
    archived_source_receipt: dict[str, Any],
    abort: dict[str, Any],
    *,
    archived_manifest_path: Path,
    archived_source_receipt_path: Path,
    abort_path: Path,
    current_manifest_path: Path,
    expected_stages: Iterable[str] = THERMAL_ABORT_STAGES,
) -> dict[str, Any]:
    expected = tuple(expected_stages)
    if archived_manifest.get("contract_version") != "qwen38-run-manifest.v1":
        raise ValueError("archived run manifest has the wrong contract")
    if current_manifest.get("contract_version") != "qwen38-run-manifest.v1":
        raise ValueError("current run manifest has the wrong contract")
    if abort.get("contract_version") != "qwen38-watchdog-abort.v1":
        raise ValueError("thermal abort receipt has the wrong contract")
    if abort.get("stage") not in expected:
        raise ValueError(f"thermal abort stage is {abort.get('stage')!r}; expected one of {expected!r}")
    max_temperature = float((abort.get("sample") or {}).get("max_temperature_c", -math.inf))
    abort_line = float(campaign_config()["safety"]["vram_temperature_abort_c"])
    if max_temperature < abort_line or "temperature" not in str(abort.get("reason", "")).casefold():
        raise ValueError("abort receipt does not prove a threshold-crossing temperature event")
    # Bind the evidence to THIS run: a stale abort receipt from an earlier campaign
    # must not be able to re-authorize a quarantine forever.
    aborted_at = str(abort.get("aborted_at") or "")
    locked_at = str(archived_manifest.get("locked_at") or "")
    if not aborted_at:
        raise ValueError("abort receipt carries no aborted_at timestamp to bind it to this run")
    aborted_moment = _parse_iso_instant(aborted_at)
    locked_moment = _parse_iso_instant(locked_at) if locked_at else None
    if locked_moment is not None and aborted_moment < locked_moment:
        raise ValueError(
            f"abort receipt predates the archived lock ({aborted_at} < {locked_at}); evidence is stale"
        )
    old_inputs = _locked_input_identity(archived_manifest)
    new_inputs = _locked_input_identity(current_manifest)
    if old_inputs != new_inputs:
        raise ValueError("model or engine identity changed across the resume patch")

    # The model/engine check above says nothing about the campaign config or the
    # task set - the files that carry the promotion gate constants, the safety
    # abort lines and the deterministic pass-rate corpus. Prove those separately
    # and report every hash that moved, rather than emitting one broad boolean.
    current_config = campaign_config()
    archived_config = _config_at_revision(archived_source_receipt.get("commandcenter_revision"))
    current_gate_hash = _canonical_block_sha256(current_config, GATE_BLOCK_KEYS)
    archived_gate_hash = (
        _canonical_block_sha256(archived_config, GATE_BLOCK_KEYS) if archived_config is not None else None
    )
    config_unchanged = archived_manifest.get("campaign_config_sha256") == current_manifest.get(
        "campaign_config_sha256"
    )
    if archived_gate_hash is not None:
        gate_constants_unchanged = archived_gate_hash == current_gate_hash
        gate_evidence = "reconstructed archived config from git and compared promotion/quality/generation blocks"
    elif config_unchanged:
        gate_constants_unchanged = True
        gate_evidence = "campaign_config_sha256 identical across the resume"
    else:
        gate_constants_unchanged = False
        gate_evidence = "campaign config changed and the archived revision could not be recovered from git"
    task_set_unchanged = archived_manifest.get("task_set_sha256") == current_manifest.get("task_set_sha256")
    if not task_set_unchanged:
        raise ValueError("assay task set changed across the resume patch; deterministic pass rates are not comparable")
    if not gate_constants_unchanged:
        raise ValueError(f"promotion/quality/generation constants changed across the resume patch ({gate_evidence})")
    return {
        "contract_version": "qwen38-resume-amendment.v1",
        "created_at": utc_now(),
        "decision": "operator_acknowledged_replica_thermal_quarantine",
        "quarantined_topologies": [
            "qwen27-replica-production",
            "qwen27-replica-throughput",
        ],
        "archived_manifest": {
            "path": str(archived_manifest_path),
            "sha256": sha256_file(archived_manifest_path),
            "source_tree_sha256": archived_manifest.get("source_tree_sha256"),
        },
        "archived_source_receipt": {
            "path": str(archived_source_receipt_path),
            "sha256": sha256_file(archived_source_receipt_path),
            "commandcenter_revision": archived_source_receipt.get("commandcenter_revision"),
            "source_tree_sha256": archived_source_receipt.get("source_tree_sha256"),
        },
        "current_manifest": {
            "path": str(current_manifest_path),
            "sha256": sha256_file(current_manifest_path),
            "source_tree_sha256": current_manifest.get("source_tree_sha256"),
        },
        "abort_evidence": {
            "path": str(abort_path),
            "sha256": sha256_file(abort_path),
            "stage": abort.get("stage"),
            "reason": abort.get("reason"),
            "max_temperature_c": max_temperature,
        },
        # Named for exactly what each one proves. The predecessor field was a
        # single "unchanged_inputs_proved" that compared only engine and model
        # bytes, while campaign.json - which carries the safety abort lines and
        # every promotion gate constant - could drift unnoticed underneath it.
        "model_and_engine_identity_unchanged": True,
        "input_identity_sha256": sha256_value(new_inputs),
        "gate_constants_unchanged": True,
        "gate_constants_evidence": gate_evidence,
        "gate_constants_sha256": current_gate_hash,
        "task_set_unchanged": True,
        "config_drift": {
            "campaign_config_sha256": {
                "archived": archived_manifest.get("campaign_config_sha256"),
                "current": current_manifest.get("campaign_config_sha256"),
                "changed": not config_unchanged,
            },
            "artifacts_config_sha256": {
                "archived": archived_manifest.get("artifacts_config_sha256"),
                "current": current_manifest.get("artifacts_config_sha256"),
                "changed": archived_manifest.get("artifacts_config_sha256")
                != current_manifest.get("artifacts_config_sha256"),
            },
            "task_set_sha256": {
                "archived": archived_manifest.get("task_set_sha256"),
                "current": current_manifest.get("task_set_sha256"),
                "changed": False,
            },
            "source_tree_sha256": {
                "archived": archived_manifest.get("source_tree_sha256"),
                "current": current_manifest.get("source_tree_sha256"),
                "changed": archived_manifest.get("source_tree_sha256")
                != current_manifest.get("source_tree_sha256"),
            },
            "note": (
                "A changed campaign_config_sha256 or source_tree_sha256 is expected when the harness "
                "is patched between legs; it is reported, not suppressed. What must NOT change is "
                "proved above: model bytes, engine binary, the assay task set, and the "
                "promotion/quality/generation constants."
            ),
        },
    }


def _clean_exact(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return value.strip("`\"' .\n\r\t").casefold()


def _json_from_text(text: str) -> Any:
    stripped = text.strip()
    match = FENCE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    return json.loads(stripped)


def _message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        return {}
    return choices[0].get("message") or {}


def response_text(response: dict[str, Any]) -> str:
    content = _message(response).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content or "")


def validate_task_response(task: dict[str, Any], response: dict[str, Any]) -> tuple[bool, str | None]:
    validator = task["validator"]
    kind = validator["type"]
    text = response_text(response)
    try:
        if kind == "exact_text":
            ok = _clean_exact(text) == _clean_exact(str(validator["expected"]))
            return ok, None if ok else "exact_text_mismatch"
        if kind == "json_equal":
            ok = _json_from_text(text) == validator["expected"]
            return ok, None if ok else "json_mismatch"
        if kind == "max_words_contains":
            words = re.findall(r"\b[\w%.-]+\b", text, re.UNICODE)
            missing = [term for term in validator["terms"] if term.casefold() not in text.casefold()]
            if len(words) > int(validator["max_words"]):
                return False, "word_limit_exceeded"
            if missing:
                return False, "missing_required_terms:" + ",".join(missing)
            return True, None
        if kind == "tool_call":
            calls = _message(response).get("tool_calls") or []
            if len(calls) != 1:
                return False, "tool_call_count"
            function = calls[0].get("function") or {}
            if function.get("name") != validator["name"]:
                return False, "tool_name_mismatch"
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            ok = arguments == validator["arguments"]
            return ok, None if ok else "tool_arguments_mismatch"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, f"validator_error:{type(exc).__name__}"
    return False, "unsupported_validator"


def completion_integrity(response: dict[str, Any]) -> tuple[bool, str | None]:
    message = _message(response)
    text = response_text(response)
    calls = message.get("tool_calls") or []
    if not text.strip() and not calls:
        return False, "empty_output"
    if "\ufffd" in text:
        return False, "replacement_character"
    if INVALID_TOKEN_RE.search(text):
        return False, "invalid_special_token"
    tokens = re.findall(r"\S+", text)
    if len(tokens) >= 24:
        for width in range(1, 7):
            tail = tokens[-width:]
            repeats = 0
            cursor = len(tokens) - width
            while cursor >= width and tokens[cursor - width:cursor] == tail:
                repeats += 1
                cursor -= width
            if repeats >= 5:
                return False, "repetition_loop"
    return True, None


def _mime_for(path: Path) -> str:
    if path.suffix.lower() == ".svg":
        return "image/svg+xml"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _data_url(path: Path) -> str:
    return f"data:{_mime_for(path)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_task_payload(
    task: dict[str, Any],
    tasks_doc: dict[str, Any],
    model: str,
    max_tokens: int,
    seed: int,
    disable_thinking: bool = False,
) -> dict[str, Any]:
    messages = [dict(message) for message in task.get("messages", [])]
    if not messages:
        messages = [{"role": "user", "content": task["prompt"]}]
    if task.get("image"):
        image_path = TASKS_PATH.parent / task["image"]
        png_sibling = image_path.with_suffix(".png")
        if image_path.suffix.lower() == ".svg" and png_sibling.exists():
            image_path = png_sibling
        content: Any = [
            {"type": "text", "text": task["prompt"]},
            {"type": "image_url", "image_url": {"url": _data_url(image_path)}},
        ]
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                messages[index]["content"] = content
                break
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if task.get("tools"):
        payload["tools"] = [tasks_doc["tools"][name] for name in task["tools"]]
        payload["tool_choice"] = "auto"
    if disable_thinking:
        # A thinking-enabled template default starves visible output on tight
        # budgets (a 2048-token cap produced empty content on a one-word task).
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


@dataclasses.dataclass
class HttpResult:
    ok: bool
    status: int | None
    response: dict[str, Any]
    error: str | None
    latency_s: float
    ttft_s: float | None = None
    ttft_source: str | None = None


def post_chat(
    endpoint: str,
    payload: dict[str, Any],
    api_key: str | None,
    timeout_s: int,
    *,
    stream: bool = False,
) -> HttpResult:
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    effective_payload = dict(payload)
    if stream:
        effective_payload["stream"] = True
        effective_payload["stream_options"] = {"include_usage": True}
    request = urllib.request.Request(url, data=canonical_bytes(effective_payload), headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as handle:
            status = getattr(handle, "status", 200)
            if stream:
                content: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                finish_reason: str | None = None
                usage: dict[str, Any] = {}
                timings: dict[str, Any] = {}
                ttft: float | None = None
                for raw_line in handle:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or usage
                    timings = chunk.get("timings") or timings
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        content.append(piece)
                    for call_part in delta.get("tool_calls") or []:
                        index = int(call_part.get("index", 0))
                        while len(tool_calls) <= index:
                            tool_calls.append(
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            )
                        target = tool_calls[index]
                        if call_part.get("id"):
                            target["id"] += str(call_part["id"])
                        function = call_part.get("function") or {}
                        target["function"]["name"] += str(function.get("name") or "")
                        target["function"]["arguments"] += str(function.get("arguments") or "")
                        if ttft is None and (function.get("name") or function.get("arguments")):
                            ttft = time.perf_counter() - started
                    finish_reason = choice.get("finish_reason") or delta.get("finish_reason") or finish_reason
                response = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "".join(content),
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": finish_reason,
                        }
                    ],
                    "usage": usage,
                    "timings": timings,
                }
                elapsed = time.perf_counter() - started
                return HttpResult(True, status, response, None, elapsed, ttft, "client_stream_first_token")
            raw = handle.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - started
        response = json.loads(raw)
        timings = response.get("timings") or {}
        prompt_ms = timings.get("prompt_ms")
        ttft = float(prompt_ms) / 1000.0 if prompt_ms is not None else None
        return HttpResult(True, status, response, None, elapsed, ttft, "llama_server_prompt_ms")
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        body = exc.read().decode("utf-8", errors="replace")
        return HttpResult(False, exc.code, {}, f"HTTP {exc.code}: {body[:1000]}", elapsed)
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return HttpResult(False, None, {}, f"{type(exc).__name__}: {exc}", time.perf_counter() - started)


def _request_id(*parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]
    return f"q38-{digest}"


def _usage(response: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = response.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    timings = response.get("timings") or {}
    if completion is None:
        completion = timings.get("predicted_n")
    if prompt is None:
        prompt = timings.get("prompt_n")
    return prompt, completion


def make_row(
    *,
    run_id: str,
    request_key: str,
    candidate: str,
    topology: str,
    endpoint: str,
    model: str,
    test_kind: str,
    result: HttpResult,
    started_at: str,
    concurrency: int,
    mtp: bool,
    task: dict[str, Any] | None = None,
    seed: int | None = None,
    slot_depth: int = 0,
    parallel_slots: int = 0,
    candidate_revision: str | None = None,
    artifact_revision: str | None = None,
    model_quant: str | None = None,
    placement: str | None = None,
    shared_postload_gb: float | None = None,
    commit_preload_gb: float | None = None,
    commit_postload_gb: float | None = None,
    client_id: int | None = None,
    disable_thinking: bool = False,
) -> dict[str, Any]:
    prompt_tokens, completion_tokens = _usage(result.response)
    integrity_ok, integrity_failure = completion_integrity(result.response) if result.ok else (False, "request_failed")
    task_valid, task_failure = (True, None)
    if task is not None and result.ok and integrity_ok:
        task_valid, task_failure = validate_task_response(task, result.response)
    valid = bool(result.ok and integrity_ok and task_valid)
    failure = result.error or integrity_failure or task_failure
    timings = result.response.get("timings") or {}
    image_asset = None
    image_bytes = None
    image_width = None
    image_height = None
    if task is not None and task.get("image"):
        image_path = TASKS_PATH.parent / task["image"]
        png_sibling = image_path.with_suffix(".png")
        if image_path.suffix.lower() == ".svg" and png_sibling.exists():
            image_path = png_sibling
        image_asset = str(task["image"])
        image_bytes = image_path.stat().st_size
        header = image_path.read_bytes()[:24]
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            image_width = int.from_bytes(header[16:20], "big")
            image_height = int.from_bytes(header[20:24], "big")
    return {
        "contract_version": "qwen38-request.v1",
        "request_id": _request_id(run_id, request_key),
        "run_id": run_id,
        "candidate": candidate,
        "topology": topology,
        "endpoint": endpoint,
        "model": model,
        "candidate_revision": candidate_revision,
        "artifact_revision": artifact_revision,
        "model_quant": model_quant,
        "placement": placement,
        "test_kind": test_kind,
        "task_id": task.get("id") if task else None,
        "task_family": task.get("family") if task else None,
        "mtp_enabled": mtp,
        "thinking_disabled": bool(disable_thinking),
        "seed": seed,
        "concurrency": concurrency,
        "slot_depth": slot_depth,
        "parallel_slots": parallel_slots,
        "client_id": client_id,
        "image_asset": image_asset,
        "image_bytes": image_bytes,
        "image_width": image_width,
        "image_height": image_height,
        "shared_postload_gb": shared_postload_gb,
        "commit_preload_gb": commit_preload_gb,
        "commit_postload_gb": commit_postload_gb,
        "started_at": started_at,
        "completed_at": utc_now(),
        "http_status": result.status,
        "success": result.ok,
        "valid": valid,
        "failure_class": failure,
        "latency_s": round(result.latency_s, 6),
        "ttft_s": round(result.ttft_s, 6) if result.ttft_s is not None else None,
        "ttft_source": result.ttft_source,
        "prompt_ms": timings.get("prompt_ms"),
        "predicted_ms": timings.get("predicted_ms"),
        "prompt_tokens_per_s": timings.get("prompt_per_second"),
        "decode_tokens_per_s": timings.get("predicted_per_second"),
        "prompt_tokens": prompt_tokens,
        "generated_tokens": completion_tokens,
        "predicted_tokens": timings.get("predicted_n"),
        "drafted_tokens": timings.get("draft_n"),
        "accepted_tokens": timings.get("draft_n_accepted"),
        "response_text": response_text(result.response),
        "tool_calls": _message(result.response).get("tool_calls") or [],
        "finish_reason": ((result.response.get("choices") or [{}])[0]).get("finish_reason"),
    }


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()


def _existing_ids(path: Path, completed_field: str | None = None) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if completed_field is None or row.get(completed_field):
                    ids.add(row["request_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def run_assay(args: argparse.Namespace) -> Path:
    config = campaign_config()
    tasks_doc = load_json(TASKS_PATH)
    root = runtime_root(config)
    output = Path(args.output) if args.output else root / "results/requests" / f"{args.run_id}.jsonl"
    writer = JsonlWriter(output)
    existing = _existing_ids(output, "success")
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    endpoints = args.endpoint
    families = set(args.family or config["quality"]["families"])
    requested_ids = set(args.task_id or [])
    base_tasks = [
        task for task in tasks_doc["tasks"]
        if task["family"] in families and (not requested_ids or task["id"] in requested_ids)
    ]
    if requested_ids - {task["id"] for task in base_tasks}:
        raise ValueError(f"unknown or family-filtered task ids: {sorted(requested_ids - {task['id'] for task in base_tasks})}")
    if args.repeat_only:
        repeated = set(config["quality"]["repeat_task_ids"])
        base_tasks = [task for task in base_tasks if task["id"] in repeated]
    jobs: list[tuple[dict[str, Any], int, int]] = []
    for ordinal, task in enumerate(base_tasks):
        jobs.append((task, int(config["generation"]["seed"]), ordinal))
    if args.include_repeats:
        repeated = set(config["quality"]["repeat_task_ids"])
        base_seed = int(config["generation"]["seed"])
        for task in base_tasks:
            if task["id"] in repeated:
                for seed in config["quality"]["repeat_seeds"]:
                    if int(seed) != base_seed:
                        jobs.append((task, int(seed), len(jobs)))

    def invoke(job: tuple[dict[str, Any], int, int]) -> dict[str, Any] | None:
        task, seed, ordinal = job
        key = (
            f"assay:{args.candidate}:{args.topology}:mtp={bool(args.mtp)}:"
            f"artifact={args.artifact_revision}:d={args.slot_depth}:slots={args.parallel_slots}:"
            f"{task['id']}:{seed}"
        )
        request_id = _request_id(args.run_id, key)
        if request_id in existing:
            return None
        endpoint = endpoints[ordinal % len(endpoints)]
        payload = build_task_payload(
            task, tasks_doc, args.model, args.max_tokens, seed, disable_thinking=args.disable_thinking
        )
        started_at = utc_now()
        result = post_chat(endpoint, payload, api_key, args.timeout_s)
        row = make_row(
            run_id=args.run_id,
            request_key=key,
            candidate=args.candidate,
            topology=args.topology,
            endpoint=endpoint,
            model=args.model,
            test_kind="assay",
            result=result,
            started_at=started_at,
            concurrency=args.concurrency,
            mtp=args.mtp,
            task=task,
            seed=seed,
            slot_depth=args.slot_depth,
            parallel_slots=args.parallel_slots,
            candidate_revision=args.candidate_revision,
            artifact_revision=args.artifact_revision,
            model_quant=args.model_quant,
            placement=args.placement,
            shared_postload_gb=args.shared_postload_gb,
            commit_preload_gb=args.commit_preload_gb,
            commit_postload_gb=args.commit_postload_gb,
            disable_thinking=args.disable_thinking,
        )
        row["request_payload_bytes"] = len(canonical_bytes(payload))
        writer.append(row)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(invoke, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            if row:
                status = "PASS" if row["valid"] else f"FAIL:{row['failure_class']}"
                print(f"{row['task_id']} seed={row['seed']} {status}")
    return output


def _gemini_parts(task: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": task["prompt"]}]
    if task.get("image"):
        image_path = TASKS_PATH.parent / task["image"]
        png_sibling = image_path.with_suffix(".png")
        if image_path.suffix.lower() == ".svg" and png_sibling.exists():
            image_path = png_sibling
        parts.insert(
            0,
            {
                "inlineData": {
                    "mimeType": _mime_for(image_path),
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            },
        )
    return parts


def _gemini_contents(task: dict[str, Any]) -> list[dict[str, Any]]:
    messages = task.get("messages") or [{"role": "user", "content": task["prompt"]}]
    contents = [
        {
            "role": "model" if message.get("role") == "assistant" else "user",
            "parts": [{"text": str(message.get("content") or "")}],
        }
        for message in messages
        if message.get("role") in {"user", "assistant"}
    ]
    if task.get("image"):
        image_part = _gemini_parts(task)[0]
        if "inlineData" in image_part:
            for content in reversed(contents):
                if content["role"] == "user":
                    content["parts"].append(image_part)
                    break
    return contents


def _openai_tools_to_gemini(task: dict[str, Any], tasks_doc: dict[str, Any]) -> list[dict[str, Any]]:
    declarations = []
    for name in task.get("tools", []):
        function = tasks_doc["tools"][name]["function"]
        declarations.append(
            {"name": function["name"], "description": function.get("description", ""), "parameters": function["parameters"]}
        )
    return [{"functionDeclarations": declarations}] if declarations else []


def _gemini_target() -> tuple[Any, str, str, str, str]:
    """Resolve the pinned HEARTH Vertex rung without exposing its access token."""
    _ensure_hearth_imports()
    from hearth.toolsurface.inference import DEFAULT_ENDPOINT, _resolve_target
    from hearth.toolsurface.backends import load_pool

    reference = campaign_config()["frontier_reference"]
    backend_name = str(reference["backend"])
    model = str(reference["model"])
    pool = load_pool()
    backend = pool.by_name(backend_name)
    if backend is None:
        raise ValueError(f"HEARTH backend {backend_name} is not configured")
    if model not in backend.models:
        raise ValueError(f"HEARTH backend {backend_name} does not expose pinned model {model}")
    target = _resolve_target(DEFAULT_ENDPOINT, None, backend_name)
    if target.auth_error:
        raise RuntimeError(target.auth_error)
    if not target.auth_token:
        raise RuntimeError(f"no Google access token available for {backend_name}")
    project = (
        target.settings.get("project")
        or os.environ.get(target.settings.get("project_env", "GOOGLE_CLOUD_PROJECT"))
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    )
    location = target.settings.get("location") or os.environ.get(
        target.settings.get("location_env", "GOOGLE_CLOUD_LOCATION"), "global"
    )
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not configured")
    return target, model, str(project), str(location), backend_name


def post_gemini_task(task: dict[str, Any], tasks_doc: dict[str, Any], max_tokens: int, timeout_s: int) -> HttpResult:
    # Reuse HEARTH's configured Vertex target and gcloud token acquisition, but
    # construct the richer wire payload here so images and function declarations
    # are present in the frontier reference assay.
    target, model, project, location, _ = _gemini_target()
    url = (
        f"{target.endpoint}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    payload: dict[str, Any] = {
        "contents": _gemini_contents(task),
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens},
    }
    tools = _openai_tools_to_gemini(task, tasks_doc)
    if tools:
        payload["tools"] = tools
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
    request = urllib.request.Request(
        url,
        data=canonical_bytes(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {target.auth_token}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as handle:
            status = getattr(handle, "status", 200)
            body = json.loads(handle.read().decode("utf-8", errors="replace"))
        elapsed = time.perf_counter() - started
        candidates = body.get("candidates") or []
        parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        tool_calls = []
        for index, part in enumerate(parts):
            call = part.get("functionCall") if isinstance(part, dict) else None
            if call:
                tool_calls.append(
                    {
                        "id": f"gemini-call-{index}",
                        "type": "function",
                        "function": {"name": call.get("name"), "arguments": call.get("args", {})},
                    }
                )
        usage = body.get("usageMetadata") or {}
        finish_reason = (candidates[0] if candidates else {}).get("finishReason")
        normalized = {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": text, "tool_calls": tool_calls}, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount"),
                "completion_tokens": usage.get("candidatesTokenCount"),
            },
            "_campaign_request_payload_bytes": len(canonical_bytes(payload)),
        }
        return HttpResult(True, status, normalized, None, elapsed)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return HttpResult(False, exc.code, {}, f"HTTP {exc.code}: {body[:1000]}", time.perf_counter() - started)
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return HttpResult(False, None, {}, f"{type(exc).__name__}: {exc}", time.perf_counter() - started)


def run_gemini_assay(args: argparse.Namespace) -> Path:
    config = campaign_config()
    frontier_model = str(config["frontier_reference"]["model"])
    frontier_backend = str(config["frontier_reference"]["backend"])
    task_set_revision = sha256_file(TASKS_PATH)
    tasks_doc = load_json(TASKS_PATH)
    root = runtime_root(config)
    output = Path(args.output) if args.output else root / "results/requests" / f"{args.run_id}.jsonl"
    writer = JsonlWriter(output)
    existing = _existing_ids(output, "success")
    families = set(args.family or config["quality"]["families"])
    requested_ids = set(args.task_id or [])
    tasks = [
        task for task in tasks_doc["tasks"]
        if task["family"] in families and (not requested_ids or task["id"] in requested_ids)
    ]
    if args.repeat_only:
        repeated = set(config["quality"]["repeat_task_ids"])
        tasks = [task for task in tasks if task["id"] in repeated]
    jobs: list[tuple[dict[str, Any], int]] = [(task, int(config["generation"]["seed"])) for task in tasks]
    if args.include_repeats:
        repeated = set(config["quality"]["repeat_task_ids"])
        jobs = []
        for task in tasks:
            seeds = config["quality"]["repeat_seeds"] if task["id"] in repeated else [config["generation"]["seed"]]
            jobs.extend((task, int(seed)) for seed in seeds)

    def invoke(job: tuple[dict[str, Any], int]) -> dict[str, Any] | None:
        task, seed = job
        key = (
            f"gemini-assay:{frontier_model}:tasks={task_set_revision}:"
            f"task={sha256_value(task)}:max={args.max_tokens}:{task['id']}:{seed}"
        )
        if _request_id(args.run_id, key) in existing:
            return None
        started_at = utc_now()
        result = post_gemini_task(task, tasks_doc, args.max_tokens, args.timeout_s)
        row = make_row(
            run_id=args.run_id,
            request_key=key,
            candidate="gemini-3.1-pro-reference",
            topology="gcp-vertex-reference",
            endpoint=frontier_backend,
            model=frontier_model,
            test_kind="assay",
            result=result,
            started_at=started_at,
            concurrency=args.concurrency,
            mtp=False,
            task=task,
            seed=seed,
            candidate_revision=frontier_model,
            artifact_revision=task_set_revision,
            model_quant="api",
            placement="gcp-vertex",
        )
        row["requested_max_tokens"] = args.max_tokens
        row["request_payload_bytes"] = result.response.get("_campaign_request_payload_bytes")
        writer.append(row)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for future in concurrent.futures.as_completed([pool.submit(invoke, job) for job in jobs]):
            row = future.result()
            if row:
                print(f"{row['task_id']} seed={row['seed']} {'PASS' if row['valid'] else 'FAIL:' + str(row['failure_class'])}")
    return output


def _performance_payload(
    model: str,
    prompt_tokens: int,
    max_tokens: int,
    seed: int,
    retrieval_key: str | None = None,
    disable_thinking: bool = False,
) -> tuple[dict[str, Any], str | None, float | None]:
    # Four ASCII words are approximately five Qwen tokens. Usage receipts record
    # the real token count, so the approximation is never presented as measured.
    words = max(8, int(prompt_tokens * 0.8))
    phrase = "ember circuit durable context "
    prompt = (phrase * math.ceil(words / 4)).split()
    if retrieval_key:
        key_digest = hashlib.sha256(retrieval_key.encode("utf-8")).hexdigest()
        expected = "Q38-" + key_digest[:12].upper()
        retrieval_position = (0.10, 0.50, 0.90)[int(key_digest[-2:], 16) % 3]
        insertion = math.floor(len(prompt) * retrieval_position)
        prompt[insertion:insertion] = ["retrieval", "code", expected]
        instruction = (
            "Find the retrieval code embedded in the filler. Return only that code, with no explanation.\n"
        )
    else:
        expected = None
        retrieval_position = None
        instruction = (
            "Write a detailed operational analysis of at least 350 words; continue until the server output budget stops you.\n"
        )
    content = " ".join(prompt[:words])
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": instruction + content}],
        "temperature": 0,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload, expected, retrieval_position


def run_load(args: argparse.Namespace) -> Path:
    config = campaign_config()
    root = runtime_root(config)
    output = Path(args.output) if args.output else root / "results/requests" / f"{args.run_id}.jsonl"
    writer = JsonlWriter(output)
    existing = _existing_ids(output, "success")
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    deadline = time.monotonic() + args.duration_s if args.duration_s else None

    def client(client_id: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        request_number = 0
        while True:
            if deadline is not None:
                if time.monotonic() >= deadline and request_number > 0:
                    break
            elif request_number >= args.requests_per_client:
                break
            key = (
                f"load:{args.candidate}:{args.topology}:mtp={bool(args.mtp)}:"
                f"artifact={args.artifact_revision}:d={args.slot_depth}:slots={args.parallel_slots}:"
                f"c{client_id}:r{request_number}:p{args.prompt_tokens}:g{args.max_tokens}"
                f":retrieval={bool(args.retrieval)}"
            )
            request_id = _request_id(args.run_id, key)
            request_number += 1
            if request_id in existing:
                continue
            endpoint = args.endpoint[client_id % len(args.endpoint)]
            payload, expected, retrieval_position = _performance_payload(
                args.model,
                args.prompt_tokens,
                args.max_tokens,
                args.seed + client_id,
                key if args.retrieval else None,
                disable_thinking=args.disable_thinking,
            )
            started_at = utc_now()
            result = post_chat(endpoint, payload, api_key, args.timeout_s, stream=True)
            row = make_row(
                run_id=args.run_id,
                request_key=key,
                candidate=args.candidate,
                topology=args.topology,
                endpoint=endpoint,
                model=args.model,
                test_kind="soak" if args.duration_s else "performance",
                result=result,
                started_at=started_at,
                concurrency=args.concurrency,
                mtp=args.mtp,
                seed=args.seed + client_id,
                slot_depth=args.slot_depth,
                parallel_slots=args.parallel_slots,
                candidate_revision=args.candidate_revision,
                artifact_revision=args.artifact_revision,
                model_quant=args.model_quant,
                placement=args.placement,
                shared_postload_gb=args.shared_postload_gb,
                commit_preload_gb=args.commit_preload_gb,
                commit_postload_gb=args.commit_postload_gb,
                client_id=client_id,
                disable_thinking=args.disable_thinking,
            )
            row["requested_prompt_tokens"] = args.prompt_tokens
            row["requested_max_tokens"] = args.max_tokens
            row["request_payload_bytes"] = len(canonical_bytes(payload))
            row["retrieval_expected"] = expected
            row["retrieval_position_fraction"] = retrieval_position
            if expected is not None and row["valid"] and _clean_exact(row["response_text"]) != _clean_exact(expected):
                row["valid"] = False
                row["failure_class"] = "long_context_retrieval_mismatch"
            if (
                expected is None
                and args.max_tokens >= 100
                and row["valid"]
                and int(row.get("generated_tokens") or 0) < math.floor(args.max_tokens * 0.90)
            ):
                row["valid"] = False
                row["failure_class"] = "short_generation"
            writer.append(row)
            rows.append(row)
        return rows

    # Discarded warmup is sent before measured workers and intentionally has no row.
    if not output.exists() or output.stat().st_size == 0:
        for index, endpoint in enumerate(args.endpoint):
            warmup, _, _ = _performance_payload(
                args.model,
                min(args.prompt_tokens, 512),
                min(args.max_tokens, 32),
                args.seed + index,
                disable_thinking=args.disable_thinking,
            )
            post_chat(
                endpoint,
                warmup,
                api_key,
                args.timeout_s,
                stream=True,
            )
    total = 0
    valid = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(client, client_id) for client_id in range(args.concurrency)]
        for future in concurrent.futures.as_completed(futures):
            rows = future.result()
            total += len(rows)
            valid += sum(1 for row in rows if row["valid"])
    print(f"wrote {total} rows ({valid} valid) to {output}")
    return output


def read_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8-sig") as handle:
            for ordinal, line in enumerate(handle, 1):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL {path}:{ordinal}: {exc}") from exc
    return rows


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 6)


def _active_wall_seconds(group: list[dict[str, Any]]) -> float:
    total = 0.0
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in group:
        by_run.setdefault(str(row.get("run_id") or "unknown"), []).append(row)
    for run_rows in by_run.values():
        client_rows = [row for row in run_rows if row.get("client_id") is not None]
        if client_rows:
            by_client: dict[int, float] = {}
            for row in client_rows:
                client = int(row["client_id"])
                by_client[client] = by_client.get(client, 0.0) + float(row.get("latency_s") or 0.0)
            total += max(by_client.values(), default=0.0)
            continue
        starts = [row.get("started_at") for row in run_rows if row.get("started_at")]
        ends = [row.get("completed_at") for row in run_rows if row.get("completed_at")]
        try:
            first = min(dt.datetime.fromisoformat(value.replace("Z", "+00:00")) for value in starts)
            last = max(dt.datetime.fromisoformat(value.replace("Z", "+00:00")) for value in ends)
            total += max((last - first).total_seconds(), 1e-6)
        except (ValueError, TypeError):
            total += sum(float(row.get("latency_s") or 0.0) for row in run_rows)
    return total


def _energy_totals(samples: list[dict[str, Any]]) -> tuple[float, float]:
    energy_j = 0.0
    energy_duration_s = 0.0
    sessions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in samples:
        if row.get("energy_j_counter") is None:
            continue
        stage = str(row.get("stage") or "unknown")
        session = str(row.get("telemetry_session") or f"legacy:{stage}")
        sessions.setdefault((stage, session), []).append(row)
    for energy_samples in sessions.values():
        counters = [float(row["energy_j_counter"]) for row in energy_samples]
        if len(counters) >= 2:
            energy_j += max(0.0, max(counters) - min(counters))
            try:
                times = [dt.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in energy_samples]
                energy_duration_s += max(0.0, (max(times) - min(times)).total_seconds())
            except (KeyError, TypeError, ValueError):
                pass
    return energy_j, energy_duration_s


def _telemetry_summary(run_ids: set[str], telemetry_rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in telemetry_rows if str(row.get("stage")) in run_ids]
    energy_j, energy_duration_s = _energy_totals(selected)
    return {
        "energy_j": round(energy_j, 6) if energy_j else None,
        "average_power_w": round(energy_j / energy_duration_s, 6) if energy_j and energy_duration_s else None,
        "peak_local_vram_used_gb": max(
            (float(row["local_vram_used_gb"]) for row in selected if row.get("local_vram_used_gb") is not None),
            default=None,
        ),
        "local_vram_observability": (
            "unavailable-cross-process-windows-vulkan" if selected else None
        ),
        "peak_host_ram_used_gb": max(
            (float(row["host_ram_used_gb"]) for row in selected if row.get("host_ram_used_gb") is not None),
            default=None,
        ),
        "peak_server_working_set_gb": max(
            (float(row["server_working_set_gb"]) for row in selected if row.get("server_working_set_gb") is not None),
            default=None,
        ),
        "peak_server_private_memory_gb": max(
            (float(row["server_private_memory_gb"]) for row in selected if row.get("server_private_memory_gb") is not None),
            default=None,
        ),
        "peak_shared_growth_gb": max(
            (float(row["shared_growth_gb"]) for row in selected if row.get("shared_growth_gb") is not None),
            default=None,
        ),
        "minimum_commit_headroom_gb": min(
            (float(row["commit_free_gb"]) for row in selected if row.get("commit_free_gb") is not None),
            default=None,
        ),
        "maximum_temperature_c": max(
            (float(row["max_temperature_c"]) for row in selected if row.get("max_temperature_c") is not None),
            default=None,
        ),
        "system_event_count": max(
            (int(row.get("bad_event_count") or 0) for row in selected),
            default=0,
        ),
    }


def summarize_rows(
    rows: list[dict[str, Any]], telemetry_rows: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    rows = _dedupe_rows(rows)
    telemetry_rows = telemetry_rows or []
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row.get("candidate"),
            row.get("topology"),
            bool(row.get("mtp_enabled")),
            row.get("test_kind"),
            row.get("concurrency"),
            row.get("requested_prompt_tokens"),
            row.get("slot_depth"),
            row.get("parallel_slots"),
            row.get("model_quant"),
            row.get("artifact_revision"),
            row.get("placement"),
        )
        groups.setdefault(key, []).append(row)
    summaries: list[dict[str, Any]] = []
    for key, group in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        valid = [row for row in group if row.get("valid")]
        latencies = [float(row["latency_s"]) for row in valid if row.get("latency_s") is not None]
        ttfts = [float(row["ttft_s"]) for row in valid if row.get("ttft_s") is not None]
        generated = sum(int(row.get("generated_tokens") or 0) for row in valid)
        wall_s = _active_wall_seconds(group)
        family_rates: dict[str, float] = {}
        families = sorted({row.get("task_family") for row in group if row.get("task_family")})
        for family in families:
            family_rows = [row for row in group if row.get("task_family") == family]
            family_rates[family] = round(sum(bool(row.get("valid")) for row in family_rows) / len(family_rows), 6)
        drafted = sum(int(row.get("drafted_tokens") or 0) for row in group)
        accepted = sum(int(row.get("accepted_tokens") or 0) for row in group)
        decode_rates = [
            float(row["decode_tokens_per_s"])
            for row in valid
            if row.get("decode_tokens_per_s") is not None
        ]
        # Prefill was measured from the first request onward but never aggregated,
        # so prompt-processing rate - the number the published corpus compares
        # models on - was invisible in every summary this campaign produced.
        #
        # Only rows that actually PREFILLED count. A request whose prefix is
        # already cached reports a rate over the one or two tokens it really
        # processed, which is not a prefill measurement and merely looks like a
        # plausible one: every request in baseline-p512-c1 reported 80-94 tok/s
        # while processing exactly 1 token of a 440-token prompt. Averaging those
        # in silently halves the reported prefill rate at shallow depth.
        prefill_rates = [
            float(row["prompt_tokens_per_s"])
            for row in valid
            if _performed_full_prefill(row)
        ]
        by_client: dict[int, list[dict[str, Any]]] = {}
        for row in group:
            if row.get("client_id") is not None:
                by_client.setdefault(int(row["client_id"]), []).append(row)
        client_goodputs = []
        for client_rows in by_client.values():
            active_s = sum(float(row.get("latency_s") or 0.0) for row in client_rows)
            tokens = sum(int(row.get("generated_tokens") or 0) for row in client_rows if row.get("valid"))
            if active_s:
                client_goodputs.append(tokens / active_s)
        fairness_cv = None
        if client_goodputs and statistics.mean(client_goodputs):
            fairness_cv = round(statistics.pstdev(client_goodputs) / statistics.mean(client_goodputs), 6)
        run_ids = {str(row.get("run_id")) for row in group if row.get("run_id")}
        telemetry = _telemetry_summary(run_ids, telemetry_rows)
        valid_count = len(valid)
        summaries.append(
            {
                "contract_version": "qwen38-summary.v1",
                "candidate": key[0],
                "topology": key[1],
                "mtp_enabled": key[2],
                "test_kind": key[3],
                "concurrency": key[4],
                "requested_prompt_tokens": key[5],
                "slot_depth": key[6],
                "parallel_slots": key[7],
                "model_quant": key[8],
                "artifact_revision": key[9],
                "placement": key[10],
                "run_ids": sorted(run_ids),
                "requests": len(group),
                "successful_requests": sum(bool(row.get("success")) for row in group),
                "valid_requests": len(valid),
                "valid_rate": round(len(valid) / len(group), 6),
                "family_pass_rates": family_rates,
                "macro_family_pass_rate": round(statistics.mean(family_rates.values()), 6) if family_rates else None,
                "wall_s": round(wall_s, 6),
                "jobs_per_hour": round(len(valid) * 3600 / wall_s, 6) if wall_s else 0.0,
                "successful_output_tokens_per_s": round(generated / wall_s, 6) if wall_s else 0.0,
                "latency_p50_s": percentile(latencies, 0.50),
                "latency_p95_s": percentile(latencies, 0.95),
                "latency_p99_s": percentile(latencies, 0.99),
                "ttft_p50_s": percentile(ttfts, 0.50),
                "ttft_p95_s": percentile(ttfts, 0.95),
                "ttft_p99_s": percentile(ttfts, 0.99),
                "decode_rate_p50_tokens_per_s": percentile(decode_rates, 0.50),
                "decode_rate_p95_tokens_per_s": percentile(decode_rates, 0.95),
                "prefill_rate_p50_tokens_per_s": percentile(prefill_rates, 0.50),
                "prefill_rate_p95_tokens_per_s": percentile(prefill_rates, 0.95),
                # Sample size is part of the measurement: a shared-prefix cell
                # contributes one cold request and then only cache hits.
                "prefill_measured_requests": len(prefill_rates),
                "client_goodput_fairness_cv": fairness_cv,
                "structured_output_valid_rate": _family_valid_rate(
                    group, {"extraction", "classification"}
                ),
                "tool_call_valid_rate": _family_valid_rate(group, {"tool_execution"}),
                "vision_valid_rate": _family_valid_rate(
                    group, {"document_ocr", "chart_diagram", "screenshot_grounded"}
                ),
                "mtp_acceptance_rate": round(accepted / drafted, 6) if drafted else None,
                "failure_classes": _counts(row.get("failure_class") for row in group if row.get("failure_class")),
                "joules_per_successful_job": (
                    round(float(telemetry["energy_j"]) / valid_count, 6)
                    if telemetry["energy_j"] is not None and valid_count
                    else None
                ),
                **telemetry,
            }
        )
    return summaries


def _counts(values: Iterable[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return result


def _family_valid_rate(rows: list[dict[str, Any]], families: set[str]) -> float | None:
    selected = [row for row in rows if row.get("task_family") in families]
    if not selected:
        return None
    return round(sum(bool(row.get("valid")) for row in selected) / len(selected), 6)


def build_judge_packet(
    rows: list[dict[str, Any]],
    baseline: str,
    candidate: str,
    families: set[str] | None = None,
) -> list[dict[str, Any]]:
    task_lookup = {task["id"]: task for task in load_json(TASKS_PATH)["tasks"]}

    def judged_output(row: dict[str, Any]) -> str:
        calls = row.get("tool_calls") or []
        if calls:
            return json.dumps({"tool_calls": calls}, ensure_ascii=False, sort_keys=True)
        return str(row.get("response_text") or "")

    by_key: dict[tuple[str, int | None], dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("test_kind") != "assay" or row.get("candidate") not in {baseline, candidate}:
            continue
        key = (row.get("task_id"), row.get("seed"))
        by_key.setdefault(key, {})[row["candidate"]] = row
    packet: list[dict[str, Any]] = []
    for (task_id, seed), pair in sorted(by_key.items()):
        if baseline not in pair or candidate not in pair:
            continue
        task = task_lookup.get(task_id, {})
        if families and task.get("family") not in families:
            continue
        outputs = {baseline: judged_output(pair[baseline]), candidate: judged_output(pair[candidate])}
        for order in (0, 1):
            first, second = ((baseline, candidate) if order == 0 else (candidate, baseline))
            packet.append(
                {
                    "pair_id": _request_id("judge", baseline, candidate, task_id, seed, order),
                    "task_id": task_id,
                    "seed": seed,
                    "order": order,
                    "prompt": task.get("prompt", ""),
                    "messages": task.get("messages"),
                    "validator": task.get("validator"),
                    "image": task.get("image"),
                    "A": outputs[first],
                    "B": outputs[second],
                    "blind_map": {"A": first, "B": second},
                    "rubric": "Judge correctness, completeness, feasibility, and usefulness. Ignore length and identity. Return A, B, or TIE with one short reason.",
                }
            )
    return packet


def _gemini_text(prompt: str, max_tokens: int, timeout_s: int, image_path: Path | None = None) -> str:
    target, model, project, location, _ = _gemini_target()
    url = (
        f"{target.endpoint}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if image_path is not None:
        png_sibling = image_path.with_suffix(".png")
        if image_path.suffix.lower() == ".svg" and png_sibling.exists():
            image_path = png_sibling
        parts.append(
            {
                "inlineData": {
                    "mimeType": _mime_for(image_path),
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            }
        )
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    request = urllib.request.Request(
        url,
        data=canonical_bytes(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {target.auth_token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as handle:
        body = json.loads(handle.read().decode("utf-8", errors="replace"))
    candidates = body.get("candidates") or []
    parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def run_gemini_judge(packet_path: Path, output: Path, concurrency: int, timeout_s: int) -> list[dict[str, Any]]:
    packet = read_rows([packet_path])
    writer = JsonlWriter(output)
    existing = _existing_ids(output, "valid")
    frontier = campaign_config()["frontier_reference"]
    judge_model = str(frontier["model"])
    judge_max_tokens = int(frontier["max_output_tokens"])

    def judge(row: dict[str, Any]) -> dict[str, Any] | None:
        pair_id = row["pair_id"]
        # JsonlWriter resume logic is request_id-based; judgment rows use the
        # pair id in both fields to keep the same invariant.
        if pair_id in existing:
            return None
        prompt = (
            "You are an impartial pairwise evaluator. Do not infer model identity. "
            "Judge correctness, instruction following, completeness, feasibility, and usefulness; "
            "do not reward length. Return JSON only as "
            '{"winner":"A"|"B"|"TIE","reason":"brief reason"}.\n\n'
            f"TASK ID: {row['task_id']}\nTASK:\n{row.get('prompt', '')}\n"
            f"MULTI-TURN TRANSCRIPT (if any):\n{json.dumps(row.get('messages'), ensure_ascii=False)}\n"
            f"MECHANICAL ACCEPTANCE CRITERIA:\n{json.dumps(row.get('validator'), ensure_ascii=False)}\n\n"
            f"OUTPUT A:\n{row['A']}\n\nOUTPUT B:\n{row['B']}"
        )
        judged_at = utc_now()
        try:
            image_path = TASKS_PATH.parent / row["image"] if row.get("image") else None
            decision = _json_from_text(_gemini_text(prompt, judge_max_tokens, timeout_s, image_path))
            winner = str(decision.get("winner", "")).upper()
            if winner not in {"A", "B", "TIE"}:
                raise ValueError(f"invalid winner {winner!r}")
            mapped = "tie" if winner == "TIE" else row["blind_map"][winner]
            result = {
                "contract_version": "qwen38-judgment.v1",
                "request_id": pair_id,
                "pair_id": pair_id,
                "task_id": row["task_id"],
                "seed": row.get("seed"),
                "order": row["order"],
                "winner": winner,
                "mapped_winner": mapped,
                "reason": decision.get("reason", ""),
                "blind_map": row["blind_map"],
                "judge": judge_model,
                "judged_at": judged_at,
                "valid": True,
            }
        except Exception as exc:
            result = {
                "contract_version": "qwen38-judgment.v1",
                "request_id": pair_id,
                "pair_id": pair_id,
                "task_id": row["task_id"],
                "seed": row.get("seed"),
                "order": row["order"],
                "winner": None,
                "mapped_winner": None,
                "reason": f"judge_error:{type(exc).__name__}:{exc}",
                "blind_map": row["blind_map"],
                "judge": judge_model,
                "judged_at": judged_at,
                "valid": False,
            }
        writer.append(result)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for future in concurrent.futures.as_completed([pool.submit(judge, row) for row in packet]):
            row = future.result()
            if row:
                print(f"{row['task_id']} order={row['order']} -> {row['mapped_winner'] or row['reason']}")
    return read_rows([output])


def choose_topology(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        row for row in summaries
        if row.get("candidate") == "qwen38-27b"
        and row.get("topology") in {"qwen27-dual-production", "qwen27-replica-production"}
        and row.get("test_kind") == "performance"
        and row.get("concurrency") == 16
        and row.get("requested_prompt_tokens") == 512
        and float(row.get("valid_rate", 0)) == 1.0
    ]
    if not eligible:
        raise ValueError("no fully valid 16-client, 512-token production-shaped Qwen3.8 summary")
    eligible.sort(
        key=lambda row: (
            float(row.get("jobs_per_hour", 0)),
            float(row.get("successful_output_tokens_per_s", 0)),
            -float(row.get("latency_p95_s") or math.inf),
        ),
        reverse=True,
    )
    winner = eligible[0]
    return {
        "contract_version": "qwen38-winning-topology.v1",
        "selected_at": utc_now(),
        "topology": winner["topology"],
        "mtp_enabled": bool(winner["mtp_enabled"]),
        "selection_metric": "valid completed jobs/hour at 16 saturated clients and 512-token prompt",
        "jobs_per_hour": winner["jobs_per_hour"],
        "successful_output_tokens_per_s": winner["successful_output_tokens_per_s"],
        "latency_p95_s": winner["latency_p95_s"],
        "eligible_cells": len(eligible),
    }


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id[row.get("request_id") or str(uuid.uuid4())] = row
    return list(by_id.values())


def _quality_evidence_regimes(rows: list[dict[str, Any]], candidate: str, seed: int) -> set[bool]:
    """MTP regimes the candidate's deterministic quality evidence actually came from."""
    return {
        bool(row.get("mtp_enabled"))
        for row in rows
        if row.get("candidate") == candidate and row.get("test_kind") == "assay" and row.get("seed") == seed
    }


def _performed_full_prefill(row: dict[str, Any], minimum_fraction: float = 0.5) -> bool:
    """True when the row's prompt-rate reflects real prefill work, not a cache hit.

    llama.cpp reports ``prompt_per_second`` over the tokens it actually processed.
    With a warm prefix that is one or two tokens, so the rate describes nothing
    about prefill throughput. Recover the processed count from rate x duration and
    require it to be a real fraction of the prompt.
    """
    rate = row.get("prompt_tokens_per_s")
    duration_ms = row.get("prompt_ms")
    prompt_tokens = row.get("prompt_tokens")
    if not rate or not duration_ms or not prompt_tokens:
        return False
    processed = float(rate) * float(duration_ms) / 1000.0
    return processed >= float(prompt_tokens) * minimum_fraction


def _deterministic_quality(rows: list[dict[str, Any]], candidate: str, seed: int) -> tuple[float, dict[str, float]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("candidate") == candidate and row.get("test_kind") == "assay" and row.get("seed") == seed:
            selected.setdefault(row.get("task_id"), row)
    if not selected:
        raise ValueError(f"no deterministic assay rows for {candidate}")
    family_rows: dict[str, list[dict[str, Any]]] = {}
    for row in selected.values():
        family_rows.setdefault(row["task_family"], []).append(row)
    rates = {
        family: round(sum(bool(row.get("valid")) for row in group) / len(group), 6)
        for family, group in family_rows.items()
    }
    overall = round(sum(bool(row.get("valid")) for row in selected.values()) / len(selected), 6)
    return overall, rates


def expected_judgment_keys(families: set[str] | None = None) -> set[tuple[str, int]]:
    config = campaign_config()
    tasks = load_json(TASKS_PATH)["tasks"]
    repeated = set(config["quality"]["repeat_task_ids"])
    base_seed = int(config["generation"]["seed"])
    repeat_seeds = [int(seed) for seed in config["quality"]["repeat_seeds"]]
    keys: set[tuple[str, int]] = set()
    for task in tasks:
        if families and task["family"] not in families:
            continue
        seeds = repeat_seeds if task["id"] in repeated else [base_seed]
        keys.update((task["id"], seed) for seed in seeds)
    return keys


def _judgment_counts(
    rows: list[dict[str, Any]],
    candidate: str,
    baseline: str,
    expected_keys: set[tuple[str, int]] | None = None,
) -> tuple[dict[str, int], int]:
    rows = _dedupe_rows(rows)
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("contract_version") == "qwen38-judgment.v1":
            grouped.setdefault((row.get("task_id"), row.get("seed")), []).append(row)
    counts = {"wins": 0, "ties": 0, "losses": 0}
    disagreements = 0
    keys = expected_keys if expected_keys is not None else set(grouped)
    for key in keys:
        group = grouped.get(key, [])
        valid_group = [row for row in group if row.get("valid")]
        mapped = {row.get("mapped_winner") for row in valid_group}
        orders = {row.get("order") for row in valid_group}
        if len(valid_group) != 2 or orders != {0, 1} or len(mapped) != 1:
            disagreements += 1
            continue
        winner = mapped.pop()
        if winner == candidate:
            counts["wins"] += 1
        elif winner == baseline:
            counts["losses"] += 1
        elif winner == "tie":
            counts["ties"] += 1
        else:
            disagreements += 1
    return counts, disagreements


def proven_spill_free_slot_tokens(
    rows: list[dict[str, Any]], topology: str | None = None, mtp_enabled: bool | None = None
) -> int:
    """Return configured slot depth only when the final context soak proved it."""
    context_rows = [
        row for row in rows
        if row.get("candidate") == "qwen38-27b"
        and row.get("run_id") == "final-deep-context"
        and row.get("test_kind") == "soak"
        and (topology is None or row.get("topology") == topology)
        and (mtp_enabled is None or bool(row.get("mtp_enabled")) == mtp_enabled)
        and row.get("valid")
        and int(row.get("slot_depth") or 0) >= int(row.get("requested_prompt_tokens") or 0)
        and 0 < int(row.get("prompt_tokens") or 0) <= int(row.get("slot_depth") or 0)
    ]
    return max((int(row.get("slot_depth") or 0) for row in context_rows), default=0)


def proven_routing_capacity(
    rows: list[dict[str, Any]], topology: str | None = None, mtp_enabled: bool | None = None
) -> dict[str, int]:
    context_rows = [
        row for row in rows
        if row.get("candidate") == "qwen38-27b"
        and row.get("run_id") == "final-deep-context"
        and row.get("test_kind") == "soak"
        and (topology is None or row.get("topology") == topology)
        and (mtp_enabled is None or bool(row.get("mtp_enabled")) == mtp_enabled)
        and row.get("valid")
        and int(row.get("slot_depth") or 0) >= int(row.get("requested_prompt_tokens") or 0)
        and 0 < int(row.get("prompt_tokens") or 0) <= int(row.get("slot_depth") or 0)
    ]
    if not context_rows:
        return {"slot_tokens": 0, "parallel_slots": 0, "context_bytes": 0}
    return {
        "slot_tokens": min(int(row.get("slot_depth") or 0) for row in context_rows),
        "parallel_slots": min(int(row.get("parallel_slots") or 0) for row in context_rows),
        "context_bytes": min(int(row.get("request_payload_bytes") or 0) for row in context_rows),
    }


def compile_scorecard(
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    winner: dict[str, Any],
    judgments: list[dict[str, Any]],
    telemetry_paths: list[Path],
    frontier_judgments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = campaign_config()
    rows = _dedupe_rows(rows)
    seed = int(config["generation"]["seed"])
    baseline_rate, baseline_families = _deterministic_quality(rows, "qwen3-30b-baseline", seed)
    candidate_rate, candidate_families = _deterministic_quality(rows, "qwen38-27b", seed)

    def summary_for(candidate: str, topology: str, mtp: bool) -> dict[str, Any]:
        matches = [
            row for row in summaries
            if row.get("candidate") == candidate
            and row.get("topology") == topology
            and bool(row.get("mtp_enabled")) == mtp
            and row.get("test_kind") == "performance"
            and row.get("concurrency") == 16
            and row.get("requested_prompt_tokens") == 512
        ]
        if not matches:
            raise ValueError(f"missing 16-client summary for {candidate}/{topology}/mtp={mtp}")
        return matches[0]

    baseline_perf = summary_for("qwen3-30b-baseline", "baseline-production", False)
    candidate_perf = summary_for("qwen38-27b", winner["topology"], bool(winner["mtp_enabled"]))
    expected_keys = expected_judgment_keys()
    counts, disagreements = _judgment_counts(
        judgments, "qwen38-27b", "qwen3-30b-baseline", expected_keys
    )
    if sum(counts.values()) == 0:
        raise ValueError("no order-consistent blind judgments are available")
    frontier_counts, frontier_disagreements = _judgment_counts(
        frontier_judgments or [], "qwen38-27b", "gemini-3.1-pro-reference", expected_keys
    )

    routing_capacity = proven_routing_capacity(
        rows, str(winner["topology"]), bool(winner["mtp_enabled"])
    )
    spill_free_slot_tokens = routing_capacity["slot_tokens"]

    samples: list[dict[str, Any]] = []
    for path in telemetry_paths:
        if path.is_file() and path.suffix == ".jsonl":
            samples.extend(read_rows([path]))
    soak_rows = [
        row
        for row in rows
        if row.get("run_id") in {"final-soak", "final-deep-context"}
        and row.get("topology") == winner["topology"]
        and bool(row.get("mtp_enabled")) == bool(winner["mtp_enabled"])
    ]
    soak_receipt = runtime_root(config) / "results/receipts/06-final-soak.json"
    soak_completed = soak_receipt.is_file() and load_json(soak_receipt).get("status") == "passed"
    min_commit = min((float(row["commit_free_gb"]) for row in samples if row.get("commit_free_gb") is not None), default=0.0)
    max_temp = max((float(row["max_temperature_c"]) for row in samples if row.get("max_temperature_c") is not None), default=math.inf)
    max_shared = max((float(row["shared_growth_gb"]) for row in samples if row.get("shared_growth_gb") is not None), default=math.inf)
    soak_energy_j, soak_energy_duration_s = _energy_totals(samples)

    mtp_enabled = bool(winner["mtp_enabled"])
    mtp_block: dict[str, Any] = {"enabled": mtp_enabled, "successful_output_goodput_delta": 0.0, "validity_regression": 0.0}
    if mtp_enabled:
        non_mtp = summary_for("qwen38-27b", winner["topology"], False)
        base_goodput = float(non_mtp["successful_output_tokens_per_s"])
        mtp_block["successful_output_goodput_delta"] = (
            float(candidate_perf["successful_output_tokens_per_s"]) / base_goodput - 1 if base_goodput else -1.0
        )
        mtp_off_rows = [row for row in rows if row.get("run_id") == "qwen27-compat-mtp-off" and row.get("task_family") in {"extraction", "classification", "tool_execution", "document_ocr", "chart_diagram", "screenshot_grounded"}]
        mtp_on_rows = [row for row in rows if row.get("run_id") == "qwen27-compat-mtp-on"]
        off_rate = sum(bool(row.get("valid")) for row in mtp_off_rows) / max(1, len(mtp_off_rows))
        on_rate = sum(bool(row.get("valid")) for row in mtp_on_rows) / max(1, len(mtp_on_rows))
        mtp_block["validity_regression"] = off_rate - on_rate

    return {
        "contract_version": "qwen38-scorecard.v1",
        "compiled_at": utc_now(),
        "baseline": {
            "deterministic_pass_rate": baseline_rate,
            "family_pass_rates": baseline_families,
            "jobs_per_hour": baseline_perf["jobs_per_hour"],
            "p95_latency_s": baseline_perf["latency_p95_s"],
        },
        "candidate": {
            "id": f"qwen38-27b-q4-{winner['topology']}-mtp-{'on' if mtp_enabled else 'off'}",
            "deterministic_pass_rate": candidate_rate,
            "family_pass_rates": candidate_families,
            # Quality is attributed to the WINNING configuration, so it has to
            # have been measured on it. Speculative decoding is only output-safe
            # when verification is exact; on 2026-08-27 the MTP-on and MTP-off
            # paths produced different text at temperature 0, which makes an
            # MTP-off pass rate no evidence at all about an MTP-on winner.
            "quality_evidence": {
                "mtp_regimes_measured": sorted(_quality_evidence_regimes(rows, "qwen38-27b", seed)),
                "winner_mtp_enabled": mtp_enabled,
                "matches_winning_configuration": _quality_evidence_regimes(rows, "qwen38-27b", seed) == {mtp_enabled},
            },
            "jobs_per_hour": candidate_perf["jobs_per_hour"],
            "p95_latency_s": candidate_perf["latency_p95_s"],
            "blind_wins": counts["wins"],
            "blind_ties": counts["ties"],
            "blind_losses": counts["losses"],
            "blind_disagreements_requiring_adjudication": disagreements,
            "frontier_blind_wins": frontier_counts["wins"],
            "frontier_blind_ties": frontier_counts["ties"],
            "frontier_blind_losses": frontier_counts["losses"],
            "frontier_blind_disagreements_requiring_adjudication": frontier_disagreements,
            "spill_free_slot_tokens": spill_free_slot_tokens,
            "advertised_parallel_slots": routing_capacity["parallel_slots"],
            "advertised_context_bytes": routing_capacity["context_bytes"],
            "soak": {
                "completed": soak_completed,
                "correctness_failures": sum(not bool(row.get("valid")) for row in soak_rows),
                "tdr_events": 0 if soak_completed else 1,
                "whea_events": 0 if soak_completed else 1,
                "kernel_power_events": 0 if soak_completed else 1,
                "process_corruptions": sum(row.get("failure_class") in {"replacement_character", "invalid_special_token", "repetition_loop"} for row in soak_rows),
                "min_commit_headroom_gb": min_commit,
                "max_vram_temperature_c": max_temp,
                "max_unplanned_shared_growth_gb": max_shared,
                "peak_local_vram_used_gb": max(
                    (float(row["local_vram_used_gb"]) for row in samples if row.get("local_vram_used_gb") is not None),
                    default=None,
                ),
                "local_vram_observability": "unavailable-cross-process-windows-vulkan",
                "peak_host_ram_used_gb": max(
                    (float(row["host_ram_used_gb"]) for row in samples if row.get("host_ram_used_gb") is not None),
                    default=None,
                ),
                "peak_server_working_set_gb": max(
                    (float(row["server_working_set_gb"]) for row in samples if row.get("server_working_set_gb") is not None),
                    default=None,
                ),
                "peak_server_private_memory_gb": max(
                    (float(row["server_private_memory_gb"]) for row in samples if row.get("server_private_memory_gb") is not None),
                    default=None,
                ),
                "energy_j": round(soak_energy_j, 6) if soak_energy_j else None,
                "average_power_w": (
                    round(soak_energy_j / soak_energy_duration_s, 6)
                    if soak_energy_j and soak_energy_duration_s
                    else None
                ),
                "joules_per_successful_job": (
                    round(soak_energy_j / len(soak_rows), 6) if soak_energy_j and soak_rows else None
                ),
            },
            "mtp": mtp_block,
        },
    }


def evaluate_promotion(scorecard: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or campaign_config()
    rules = config["promotion"]
    safety = config["safety"]
    baseline = scorecard["baseline"]
    candidate = scorecard["candidate"]
    soak = candidate["soak"]
    comparisons = candidate.get("blind_wins", 0) + candidate.get("blind_ties", 0) + candidate.get("blind_losses", 0)
    blind_rate = (
        (candidate.get("blind_wins", 0) + candidate.get("blind_ties", 0)) / comparisons if comparisons else 0.0
    )
    disagreements = int(candidate.get("blind_disagreements_requiring_adjudication", 0))
    judgment_coverage = comparisons / (comparisons + disagreements) if comparisons + disagreements else 0.0
    baseline_jph = float(baseline["jobs_per_hour"])
    throughput_improvement = float(candidate["jobs_per_hour"]) / baseline_jph - 1 if baseline_jph else -1.0
    baseline_p95 = float(baseline["p95_latency_s"])
    p95_ratio = float(candidate["p95_latency_s"]) / baseline_p95 if baseline_p95 else math.inf
    family_regressions = {
        family: round(float(candidate["family_pass_rates"].get(family, 0)) - float(rate), 6)
        for family, rate in baseline["family_pass_rates"].items()
        if float(candidate["family_pass_rates"].get(family, 0)) < float(rate) - float(rules["family_regression_tolerance"])
    }
    mtp = candidate.get("mtp", {"enabled": False})
    gates = {
        "deterministic_pass_rate": float(candidate["deterministic_pass_rate"]) >= float(baseline["deterministic_pass_rate"]) * float(rules["deterministic_pass_rate_ratio_min"]),
        "no_family_regression": not family_regressions,
        "blind_win_or_tie_rate": comparisons > 0 and blind_rate >= float(rules["blind_win_or_tie_rate_min"]),
        "blind_judgment_coverage": judgment_coverage >= float(rules["blind_judgment_coverage_min"]),
        "jobs_per_hour_improvement": throughput_improvement >= float(rules["jobs_per_hour_improvement_min"]),
        "p95_latency": p95_ratio <= float(rules["p95_latency_ratio_max"]),
        "spill_free_context": int(candidate["spill_free_slot_tokens"]) >= int(rules["minimum_spill_free_slot_tokens"]),
        "soak_completed": bool(soak.get("completed")),
        "soak_correctness": int(soak.get("correctness_failures", 1)) == 0 and int(soak.get("process_corruptions", 1)) == 0,
        "soak_system_events": sum(int(soak.get(name, 1)) for name in ("tdr_events", "whea_events", "kernel_power_events")) == 0,
        "commit_headroom": float(soak.get("min_commit_headroom_gb", 0)) >= float(safety["commit_min_free_gb"]),
        "vram_temperature": float(soak.get("max_vram_temperature_c", math.inf)) < float(safety["vram_temperature_abort_c"]),
        "shared_memory_growth": float(soak.get("max_unplanned_shared_growth_gb", math.inf)) <= float(safety["shared_growth_abort_gb"]),
        "mtp_net_goodput": (not mtp.get("enabled")) or (
            float(mtp.get("successful_output_goodput_delta", -1)) > 0 and float(mtp.get("validity_regression", 1)) <= 0
        ),
        # A verdict must not attribute quality measured on one configuration to a
        # different one. Without this the scorecard can certify an MTP-on winner
        # using MTP-off text, which is exactly what this campaign produced.
        "quality_measured_on_winning_config": bool(
            candidate.get("quality_evidence", {}).get("matches_winning_configuration", False)
        ),
    }
    return {
        "contract_version": "qwen38-promotion-verdict.v1",
        "evaluated_at": utc_now(),
        "candidate": candidate.get("id"),
        "decision": "eligible_for_pin_only_canary" if all(gates.values()) else "do_not_promote",
        "all_gates_pass": all(gates.values()),
        "gates": gates,
        "derived": {
            "blind_win_or_tie_rate": round(blind_rate, 6),
            "blind_judgment_coverage": round(judgment_coverage, 6),
            "jobs_per_hour_improvement": round(throughput_improvement, 6),
            "p95_latency_ratio": round(p95_ratio, 6),
            "family_regressions": family_regressions,
        },
    }


def command_validate(_: argparse.Namespace) -> int:
    errors = validate_sources()
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    tasks = load_json(TASKS_PATH)["tasks"]
    print(f"VALID: {len(tasks)} tasks, {len(set(task['family'] for task in tasks))} balanced families")
    return 0


def command_frontier_preflight(_: argparse.Namespace) -> int:
    target, model, _project, location, backend = _gemini_target()
    print(
        json.dumps(
            {
                "backend": backend,
                "model": model,
                "endpoint": target.endpoint,
                "location": location,
                "auth": "available",
            },
            indent=2,
        )
    )
    return 0


def command_init(args: argparse.Namespace) -> int:
    print(init_runtime(force=args.force))
    return 0


def command_lock(_: argparse.Namespace) -> int:
    manifest = lock_artifacts()
    print(json.dumps({"locked": sum(row["state"] == "locked" for row in manifest["artifacts"]), "manifest": str(runtime_root() / "state/run-manifest.json")}, indent=2))
    return 0


def command_verify_lock(args: argparse.Namespace) -> int:
    errors = verify_manifest(rehash_artifacts=args.rehash_artifacts)
    if errors:
        print("LOCK INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("LOCK VALID")
    return 0


def command_resume_amendment(args: argparse.Namespace) -> int:
    root = runtime_root()
    archived_manifest_path = Path(args.archived_manifest)
    archived_source_receipt_path = Path(args.archived_source_receipt)
    abort_path = Path(args.abort) if args.abort else (
        root
        / "results/telemetry"
        / "watchdog-qwen27-replica-production-mtp-off-p512-c4-abort.json"
    )
    current_manifest_path = root / "state/run-manifest.json"
    for path in (
        archived_manifest_path,
        archived_source_receipt_path,
        abort_path,
        current_manifest_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(f"resume amendment input is missing: {path}")
    amendment = build_resume_amendment(
        load_json(archived_manifest_path),
        load_json(current_manifest_path),
        load_json(archived_source_receipt_path),
        load_json(abort_path),
        archived_manifest_path=archived_manifest_path,
        archived_source_receipt_path=archived_source_receipt_path,
        abort_path=abort_path,
        current_manifest_path=current_manifest_path,
        expected_stages=tuple(args.expected_stage) if args.expected_stage else THERMAL_ABORT_STAGES,
    )
    output = Path(args.output) if args.output else root / "state/resume-amendment.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(amendment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


def command_assay(args: argparse.Namespace) -> int:
    print(run_assay(args))
    return 0


def command_gemini_assay(args: argparse.Namespace) -> int:
    print(run_gemini_assay(args))
    return 0


def command_load(args: argparse.Namespace) -> int:
    print(run_load(args))
    return 0


def command_summarize(args: argparse.Namespace) -> int:
    paths = [Path(path) for path in args.input]
    if not paths:
        paths = sorted((runtime_root() / "results/requests").glob("*.jsonl"))
    telemetry_paths = sorted((runtime_root() / "results/telemetry").glob("watchdog-*.jsonl"))
    summaries = summarize_rows(read_rows(paths), read_rows(telemetry_paths) if telemetry_paths else [])
    output = Path(args.output) if args.output else runtime_root() / "results/summaries/all-configurations.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


def command_judge_packet(args: argparse.Namespace) -> int:
    paths = [Path(path) for path in args.input]
    packet = build_judge_packet(read_rows(paths), args.baseline, args.candidate, set(args.family or []))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in packet:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{output}: {len(packet)} order-balanced comparisons")
    return 0


def command_gemini_judge(args: argparse.Namespace) -> int:
    rows = run_gemini_judge(Path(args.packet), Path(args.output), args.concurrency, args.timeout_s)
    print(f"{args.output}: {sum(bool(row.get('valid')) for row in rows)} valid judgments")
    return 0


def command_choose(args: argparse.Namespace) -> int:
    winner = choose_topology(load_json(Path(args.summaries)))
    output = Path(args.output) if args.output else runtime_root() / "results/summaries/winning-topology.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(winner, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(winner, indent=2))
    return 0


def command_scorecard(args: argparse.Namespace) -> int:
    request_paths = [Path(path) for path in args.requests]
    if not request_paths:
        request_paths = sorted((runtime_root() / "results/requests").glob("*.jsonl"))
    telemetry = [Path(path) for path in args.telemetry]
    if not telemetry:
        telemetry = sorted((runtime_root() / "results/telemetry").glob("watchdog-final-*.jsonl"))
    scorecard = compile_scorecard(
        read_rows(request_paths),
        load_json(Path(args.summaries)),
        load_json(Path(args.winner)),
        read_rows([Path(args.judgments)]),
        telemetry,
        read_rows([Path(args.frontier_judgments)]) if args.frontier_judgments else [],
    )
    output = Path(args.output) if args.output else runtime_root() / "results/summaries/promotion-scorecard.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


def command_verdict(args: argparse.Namespace) -> int:
    verdict = evaluate_promotion(load_json(Path(args.scorecard)))
    output = Path(args.output) if args.output else runtime_root() / "results/promotion-verdict.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["all_gates_pass"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate canonical config and the 72-task assay")
    validate.set_defaults(func=command_validate)
    frontier = sub.add_parser("frontier-preflight", help="verify the pinned Gemini reference and local credentials")
    frontier.set_defaults(func=command_frontier_preflight)
    init = sub.add_parser("init", help="materialize the isolated runtime directory")
    init.add_argument("--force", action="store_true", help="refresh runtime control copies")
    init.set_defaults(func=command_init)
    lock = sub.add_parser("lock", help="hash all required model and engine inputs")
    lock.set_defaults(func=command_lock)
    verify_lock = sub.add_parser("verify-lock", help="fail if locked campaign inputs have drifted")
    verify_lock.add_argument("--rehash-artifacts", action="store_true", help="rehash model parts instead of size-checking")
    verify_lock.set_defaults(func=command_verify_lock)
    amendment = sub.add_parser(
        "resume-amendment",
        help="prove unchanged locked inputs across the reviewed thermal-quarantine resume patch",
    )
    amendment.add_argument("--archived-manifest", required=True)
    amendment.add_argument("--archived-source-receipt", required=True)
    amendment.add_argument("--abort")
    amendment.add_argument(
        "--expected-stage",
        action="append",
        help="leg id the abort receipt must name; repeatable (default: the known thermal-abort legs)",
    )
    amendment.add_argument("--output")
    amendment.set_defaults(func=command_resume_amendment)

    assay = sub.add_parser("assay", help="run the fixed assay through OpenAI-compatible chat")
    _add_request_args(assay)
    assay.add_argument("--family", action="append", help="restrict to a family; repeatable")
    assay.add_argument("--task-id", action="append", help="restrict to a task id; repeatable")
    assay.add_argument("--repeat-only", action="store_true")
    assay.add_argument("--include-repeats", action="store_true")
    assay.add_argument("--max-tokens", type=int, default=2048)
    assay.set_defaults(func=command_assay)

    gemini = sub.add_parser("gemini-assay", help="run the same assay as a Gemini 3.1 Pro frontier reference")
    gemini.add_argument("--run-id", required=True)
    gemini.add_argument("--concurrency", type=int, default=2)
    gemini.add_argument("--family", action="append")
    gemini.add_argument("--task-id", action="append")
    gemini.add_argument("--repeat-only", action="store_true")
    gemini.add_argument("--include-repeats", action="store_true")
    gemini.add_argument(
        "--max-tokens",
        type=int,
        default=int(campaign_config()["frontier_reference"]["max_output_tokens"]),
    )
    gemini.add_argument("--timeout-s", type=int, default=1000)
    gemini.add_argument("--output")
    gemini.set_defaults(func=command_gemini_assay)

    load = sub.add_parser("load", help="run a closed-loop performance or soak leg")
    _add_request_args(load)
    load.add_argument("--prompt-tokens", type=int, required=True)
    load.add_argument("--max-tokens", type=int, default=200)
    load.add_argument("--requests-per-client", type=int, default=3)
    load.add_argument("--duration-s", type=int, default=0)
    load.add_argument("--seed", type=int, default=38027)
    load.add_argument("--retrieval", action="store_true", help="mechanically verify a buried context code")
    load.set_defaults(func=command_load)

    summarize = sub.add_parser("summarize", help="derive configuration summaries from raw request JSONL")
    summarize.add_argument("--input", action="append", default=[])
    summarize.add_argument("--output")
    summarize.set_defaults(func=command_summarize)

    judge = sub.add_parser("judge-packet", help="create anonymized, order-reversed pairwise comparisons")
    judge.add_argument("--input", action="append", required=True)
    judge.add_argument("--baseline", required=True)
    judge.add_argument("--candidate", required=True)
    judge.add_argument("--family", action="append")
    judge.add_argument("--output", required=True)
    judge.set_defaults(func=command_judge_packet)

    gemini_judge = sub.add_parser("gemini-judge", help="judge an order-balanced packet with Gemini Pro")
    gemini_judge.add_argument("--packet", required=True)
    gemini_judge.add_argument("--output", required=True)
    gemini_judge.add_argument("--concurrency", type=int, default=2)
    gemini_judge.add_argument("--timeout-s", type=int, default=1000)
    gemini_judge.set_defaults(func=command_gemini_judge)

    choose = sub.add_parser("choose-topology", help="select the fastest fully valid production-shaped topology")
    choose.add_argument("--summaries", required=True)
    choose.add_argument("--output")
    choose.set_defaults(func=command_choose)

    scorecard = sub.add_parser("scorecard", help="compile raw results, blind judgments, soak, and context proof")
    scorecard.add_argument("--requests", action="append", default=[])
    scorecard.add_argument("--summaries", required=True)
    scorecard.add_argument("--winner", required=True)
    scorecard.add_argument("--judgments", required=True)
    scorecard.add_argument("--frontier-judgments")
    scorecard.add_argument("--telemetry", action="append", default=[])
    scorecard.add_argument("--output")
    scorecard.set_defaults(func=command_scorecard)

    verdict = sub.add_parser("verdict", help="apply every production promotion gate")
    verdict.add_argument("--scorecard", required=True)
    verdict.add_argument("--output")
    verdict.set_defaults(func=command_verdict)
    return parser


def _add_request_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--endpoint", action="append", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--slot-depth", type=int, default=0, help="configured tokens per server slot")
    parser.add_argument("--parallel-slots", type=int, default=0, help="aggregate slots across recorded endpoints")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--artifact-revision")
    parser.add_argument("--model-quant")
    parser.add_argument("--placement")
    parser.add_argument("--shared-postload-gb", type=float)
    parser.add_argument("--commit-preload-gb", type=float)
    parser.add_argument("--commit-postload-gb", type=float)
    parser.add_argument("--mtp", action="store_true")
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="send chat_template_kwargs enable_thinking=false (the gated no-think regime)",
    )
    parser.add_argument("--timeout-s", type=int, default=1000)
    parser.add_argument("--api-key-env", default="QWEN38_API_KEY")
    parser.add_argument("--output")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # concise operator-facing boundary; tests call functions directly
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
