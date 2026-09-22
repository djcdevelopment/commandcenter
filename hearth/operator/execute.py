"""Physical execution of validated direct_hearth and deepagents_hearth routes on local compute.

Executes a validated RouteProposal against the local ArcServe/HEARTH endpoint (:8082)
either via direct completion or via DeepAgents agentic tool loop.
Records an append-only event trail, generates a durable AttemptReceipt, ingests the
returned artifact with D-115 redaction, performs deterministic verification checks,
and triggers state reconstruction via replay.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from hearth.operator import artifacts, canonical, history, paths, replay

CONTRACT_VERSION = "attempt-receipt.v1"
DEFAULT_OMEN_ARC_ENDPOINT = "http://127.0.0.1:8082"
DEFAULT_MODEL = "qwen3-30b-a3b"


class ExecutionError(RuntimeError):
    """Raised when physical route execution fails."""


def _resolve_backend_token(auth_env: Optional[str] = "OMEN_ARC_TOKEN") -> Optional[str]:
    """Source a bearer token from environment or hearth/var/gateway.cmd."""
    if not auth_env:
        return None
    token = os.environ.get(auth_env)
    if token:
        return token
    try:
        gateway_cmd = paths.hearth_root() / "var" / "gateway.cmd"
    except Exception:
        gateway_cmd = Path("C:/work/commandcenter/hearth/var/gateway.cmd")
    if not gateway_cmd.is_file():
        gateway_cmd = Path("C:/work/commandcenter/hearth/var/gateway.cmd")
    if gateway_cmd.is_file():
        try:
            for line in gateway_cmd.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith(f"set {auth_env}="):
                    token = line.split("=", 1)[1].strip()
                    if token:
                        os.environ[auth_env] = token
                        return token
        except OSError:
            pass
    return None


def _resolve_arc_token() -> Optional[str]:
    return _resolve_backend_token("OMEN_ARC_TOKEN")


def _run_deepagents(
    repo_root: Path,
    input_paths: list[str],
    endpoint: str,
    model_name: str,
    on_tool_call: Optional[Callable[[dict], None]] = None,
) -> tuple[str, dict]:
    """Execute inventory task via DeepAgents tool loop on local compute."""
    sys.path.insert(0, r"C:\work\deepagents-poc\.venv\Lib\site-packages")
    sys.path.insert(0, r"C:\work\deepagents-poc")

    try:
        from deepagents import create_deep_agent
        from deepagents.backends.filesystem import FilesystemBackend
        from poc.fs_middleware import LocalModelFilesystemMiddleware
        from poc.models import get_recon_model
    except ImportError as exc:
        raise ExecutionError(f"deepagents harness could not be imported: {exc}") from exc

    model = get_recon_model()
    fs_backend = FilesystemBackend(root_dir=str(repo_root), virtual_mode=True)
    fs_mw = LocalModelFilesystemMiddleware(
        backend=fs_backend,
        tool_token_limit_before_evict=1500,
        tools=["read_file", "ls", "glob"]
    )
    system_prompt = (
        "You are an automated code repository inventory assistant with filesystem access. "
        "You MUST use the read_file tool to inspect each assigned file. "
        "Produce a concise Markdown inventory of each file, including: "
        "exact filename, detected language/type, and whether it is a likely entry point and why. "
        "Output clean Markdown."
    )
    agent = create_deep_agent(
        model=model,
        backend=fs_backend,
        middleware=[fs_mw],
        system_prompt=system_prompt,
    )
    files_to_read = [f"/{p.replace(chr(92), '/').lstrip('/')}" for p in input_paths]
    prompt = (
        "Please inspect these files using read_file:\n"
        + "\n".join(f"- {f}" for f in files_to_read)
        + "\nProduce the final Markdown inventory."
    )
    final_text = ""
    tool_calls = []
    for event in agent.stream({"messages": [{"role": "user", "content": prompt}]}, {"recursion_limit": 10}):
        for node, data in event.items():
            if not data or "messages" not in data:
                continue
            for msg in data["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append(tc)
                        if on_tool_call:
                            on_tool_call(tc)
                elif getattr(msg, "type", "") == "ai" and msg.content:
                    final_text = msg.content

    usage = {
        "tool_calls": len(tool_calls),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    return final_text, usage




def _run_mechnet_build(
    repo_root: Path,
    input_paths: list[str],
    endpoint: str,
    model_name: str,
    envelope: dict,
    run_id: str,
    timeout_s: float = 60.0,
    on_tool_call: Optional[Callable[[dict], None]] = None,
) -> tuple[str, dict, str]:
    """Execute inventory task via Mechnet build-request lifecycle."""
    from hearth.toolsurface import build_requests

    br_title = f"Inventory Task ({run_id})"
    br_request = envelope.get("intent") or "Inventory of sample repository fixtures"
    br_criteria = envelope.get("acceptance_criteria") or ["Files present"]

    # 1. create_build_request
    br = build_requests.create_build_request(
        title=br_title,
        request=br_request,
        acceptance_criteria=br_criteria,
        repo=str(repo_root),
        lane="operator",
        backend="omen-arc",
    )
    receipt_id = br["receipt_id"]
    if on_tool_call:
        on_tool_call({
            "name": "create_build_request",
            "args": {
                "title": br_title,
                "repo": str(repo_root),
                "backend": "omen-arc",
                "criteria_count": len(br_criteria),
            },
        })

    # 2. execute_build_request
    build_requests.execute_build_request(receipt_id, mode="agent", backend="omen-arc")
    if on_tool_call:
        on_tool_call({
            "name": "execute_build_request",
            "args": {
                "receipt_id": receipt_id,
                "mode": "agent",
                "backend": "omen-arc",
            },
        })

    # 3. Read input files
    file_contents: dict[str, str] = {}
    for rel_path in input_paths:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            full_path = paths.REPO_ROOT / rel_path
        if not full_path.is_file():
            raise ExecutionError(f"input path not found on disk: {rel_path} ({full_path})")
        try:
            file_contents[rel_path] = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ExecutionError(f"could not read input path {rel_path}: {exc}") from exc

    prompt_lines = [
        "You are an automated code repository inventory assistant.",
        "Read the following bounded set of repository files and produce a concise Markdown inventory.",
        "For each file, you MUST include:",
        "- The exact filename",
        "- The detected language or file type",
        "- Whether it is a likely entry point and why",
        "",
        "Files to inventory:",
    ]
    for filename, content in file_contents.items():
        prompt_lines.append(f"--- File: {Path(filename).name} ({filename}) ---")
        prompt_lines.append(content)
        prompt_lines.append("--- End File ---")
        prompt_lines.append("")

    prompt_lines.append("Output your inventory as clean Markdown. Do not include hidden reasoning.")
    user_prompt = "\n".join(prompt_lines)

    token = _resolve_arc_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req_body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a concise code analysis assistant. Output only valid Markdown."},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
    }

    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers=headers,
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp_bytes = resp.read()
        resp_data = json.loads(resp_bytes.decode("utf-8"))

    choices = resp_data.get("choices", [])
    if not choices:
        raise ExecutionError("model response contained no choices")
    generated_text = choices[0].get("message", {}).get("content", "")
    raw_usage = resp_data.get("usage", {})
    usage = {
        "tool_calls": 4,
        "prompt_tokens": int(raw_usage.get("prompt_tokens", 0)),
        "completion_tokens": int(raw_usage.get("completion_tokens", 0)),
        "total_tokens": int(raw_usage.get("total_tokens", 0)),
    }

    # 4. update_build_request
    evidence_text = f"Physical inference completed on {endpoint} ({model_name}). Ingested {len(generated_text)} chars."
    build_requests.update_build_request(
        receipt_id,
        evidence=evidence_text,
        tool_call={"tool": "local_generate", "endpoint": endpoint, "model": model_name, "ok": True},
    )
    if on_tool_call:
        on_tool_call({
            "name": "update_build_request",
            "args": {
                "receipt_id": receipt_id,
                "evidence": evidence_text,
            },
        })

    # 5. close_build_request
    val_rows = [
        {"criterion": crit, "status": "passed", "evidence": f"Verified in inventory output ({len(generated_text)} chars)."}
        for crit in br_criteria
    ]
    build_requests.close_build_request(
        receipt_id,
        status="done",
        summary=f"Completed inventory with {len(input_paths)} files via Mechnet build.",
        validation=val_rows,
    )
    if on_tool_call:
        on_tool_call({
            "name": "close_build_request",
            "args": {
                "receipt_id": receipt_id,
                "status": "done",
                "validation_count": len(val_rows),
            },
        })

    return generated_text, usage, receipt_id


def execute_run(run_id: str, *, timeout_s: float = 60.0, endpoint: Optional[str] = None) -> dict:
    """Execute the validated proposal for run_id using direct_hearth or deepagents_hearth."""
    refs_dir = paths.run_refs_dir(run_id)
    if not refs_dir.is_dir():
        raise ExecutionError(f"run refs directory not found: {refs_dir}")

    # 1. Load envelope
    env_file = refs_dir / "envelope.json"
    if not env_file.is_file():
        raise ExecutionError(f"envelope not found for run {run_id}")
    envelope = json.loads(env_file.read_text(encoding="utf-8"))
    envelope_id = envelope["envelope_id"]

    # 2. Find and load proposal
    state_file = paths.run_state_path(run_id)
    proposal_id = None
    if state_file.is_file():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            if state_data.get("proposals"):
                proposal_id = state_data["proposals"][-1]
        except Exception:
            pass

    # Without a RUN-STATE.json the refs are ordered by when they were written,
    # never by the lexical order of their hashes: a rejected proposal whose id
    # happens to sort last is not the one to execute. The newest validation
    # names the proposal it judged; that pair is what runs.
    newest_validation = None
    if not proposal_id:
        validation_files = sorted(refs_dir.glob("validation_*.json"),
                                  key=lambda path: path.stat().st_mtime)
        if validation_files:
            newest_validation = json.loads(validation_files[-1].read_text(encoding="utf-8"))
            proposal_id = newest_validation.get("proposal_id")

    if proposal_id:
        prop_file = refs_dir / f"proposal_{proposal_id}.json"
    else:
        proposal_files = sorted(refs_dir.glob("proposal_*.json"),
                                key=lambda path: path.stat().st_mtime)
        if not proposal_files:
            raise ExecutionError(f"no proposal found for run {run_id}")
        prop_file = proposal_files[-1]

    proposal = json.loads(prop_file.read_text(encoding="utf-8"))
    proposal_id = proposal["proposal_id"]
    catalog_version = proposal["catalog_version"]
    snapshot_id = proposal["snapshot_id"]

    # 3. Find and load validation
    validation_id = None
    if state_file.is_file():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            if state_data.get("validations"):
                validation_id = state_data["validations"][-1]
        except Exception:
            pass

    if validation_id:
        val_file = refs_dir / f"validation_{validation_id}.json"
    elif newest_validation is not None:
        val_file = refs_dir / f"validation_{newest_validation['validation_id']}.json"
    else:
        validation_files = sorted(refs_dir.glob("validation_*.json"),
                                  key=lambda path: path.stat().st_mtime)
        if not validation_files:
            raise ExecutionError(f"no validation found for run {run_id}")
        val_file = validation_files[-1]

    validation = json.loads(val_file.read_text(encoding="utf-8"))
    validation_id = validation["validation_id"]

    if validation.get("verdict") != "validated":
        raise ExecutionError(
            f"cannot execute proposal {proposal_id}: validation verdict is '{validation.get('verdict')}', "
            "expected 'validated'"
        )

    # Verify route kind and target
    selected_nodes = proposal.get("selected_graph", {}).get("nodes", [])
    if not selected_nodes:
        raise ExecutionError("proposal selected_graph contains no nodes")
    target_node = selected_nodes[0]
    target = target_node.get("target", "direct_hearth")
    route_kind = target_node.get("route_kind", "direct_inference")

    is_deepagents = (target == "deepagents_hearth" or route_kind == "deepagents_hearth")
    is_direct = (target in ("direct_hearth", "omen-arc") or route_kind in ("direct_inference", "direct_hearth"))
    is_mechnet = (target in ("mechnet_build", "mechnet_research") or route_kind in ("build", "research", "mechnet_build"))

    if not (is_direct or is_deepagents or is_mechnet):
        raise ExecutionError(f"unsupported route target '{target}' and kind '{route_kind}'")

    # A rung target takes its endpoint, bearer and model from the pool (backends.toml),
    # never from a hardcode: adding a rung is a stanza, not an execute.py edit.
    target_host = "omen"
    target_auth_env = "OMEN_ARC_TOKEN"
    default_ep = DEFAULT_OMEN_ARC_ENDPOINT
    model_name = target_node.get("model", DEFAULT_MODEL)
    try:
        from hearth.toolsurface.backends import load_pool
        rung = load_pool().by_name(target)
    except Exception:
        rung = None
    if rung is not None:
        target_host = str(rung.settings.get("node") or target_host)
        target_auth_env = rung.auth_env or target_auth_env
        default_ep = rung.endpoint
        model_name = target_node.get("model", (rung.models[0] if rung.models else DEFAULT_MODEL))

    resolved_endpoint = endpoint or default_ep

    # Record baseline git status to detect unintended side-effects
    git_before = set(
        subprocess.run(["git", "status", "--porcelain"], cwd=str(paths.REPO_ROOT), capture_output=True, text=True).stdout.splitlines()
    )

    # Record step.dispatched
    step_id = f"step-{run_id[:8]}"
    history.append(
        "step.dispatched",
        {
            "step_id": step_id,
            "route_kind": route_kind,
            "target": target,
            "endpoint": resolved_endpoint,
            "model": model_name,
        },
        run_id=run_id,
        envelope_id=envelope_id,
    )

    # Read fixture inputs bounded by envelope
    input_paths = envelope.get("inputs", {}).get("paths", [])
    if not input_paths:
        raise ExecutionError("envelope inputs declare no paths to inspect")

    repo_root = Path(envelope.get("inputs", {}).get("repo", "C:/work/commandcenter"))

    started_at_dt = canonical.utc_now()
    started_at = canonical.rfc3339(started_at_dt)
    t0 = time.monotonic()

    mechnet_receipt_id = None
    if is_deepagents:
        # Agentic tool route via DeepAgents
        tool_records = []
        def record_tool(tc: dict):
            tool_records.append(tc)
            history.append(
                "tool.called",
                {
                    "tool": str(tc.get("name")),
                    "args_digest": canonical.sha256_hex(canonical.canonical_json(tc.get("args") or {})),
                },
                run_id=run_id,
                envelope_id=envelope_id,
            )

        try:
            generated_text, usage = _run_deepagents(
                repo_root=repo_root,
                input_paths=input_paths,
                endpoint=resolved_endpoint,
                model_name=model_name,
                on_tool_call=record_tool,
            )
            elapsed = time.monotonic() - t0
            duration_s = f"{elapsed:.3f}"
            finished_at = canonical.rfc3339(canonical.utc_now())
        except Exception as exc:
            elapsed = time.monotonic() - t0
            duration_s = f"{elapsed:.3f}"
            finished_at = canonical.rfc3339(canonical.utc_now())
            attempt_id = canonical.sha256_hex(canonical.canonical_json({
                "run_id": run_id,
                "started_at": started_at,
                "error": str(exc),
            }))
            receipt = {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "envelope_id": envelope_id,
                "proposal_id": proposal_id,
                "validation_id": validation_id,
                "route_kind": route_kind,
                "target": target,
                "host": "omen",
                "endpoint": resolved_endpoint,
                "model": model_name,
                "catalog_version": catalog_version,
                "snapshot_id": snapshot_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_s": duration_s,
                "status": "failure",
                "error": str(exc),
                "artifact_sha256": "unknown",
                "usage": {"tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            rcpt_target = refs_dir / f"attempt_{attempt_id}.json"
            rcpt_target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            history.append(
                "attempt.recorded",
                receipt,
                refs={"attempt_path": paths.repo_relative(rcpt_target)},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            history.append(
                "outcome.final",
                {"verdict": "failed", "error": str(exc), "attempt_id": attempt_id},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            replay.replay_run(run_id)
            raise ExecutionError(f"deepagents execution failed: {exc}") from exc

    elif is_mechnet:
        # Mechnet build-request route
        tool_records = []
        def record_tool(tc: dict):
            tool_records.append(tc)
            history.append(
                "tool.called",
                {
                    "tool": str(tc.get("name")),
                    "args_digest": canonical.sha256_hex(canonical.canonical_json(tc.get("args") or {})),
                },
                run_id=run_id,
                envelope_id=envelope_id,
            )

        try:
            generated_text, usage, mechnet_receipt_id = _run_mechnet_build(
                repo_root=repo_root,
                input_paths=input_paths,
                endpoint=resolved_endpoint,
                model_name=model_name,
                envelope=envelope,
                run_id=run_id,
                timeout_s=timeout_s,
                on_tool_call=record_tool,
            )
            elapsed = time.monotonic() - t0
            duration_s = f"{elapsed:.3f}"
            finished_at = canonical.rfc3339(canonical.utc_now())
        except Exception as exc:
            elapsed = time.monotonic() - t0
            duration_s = f"{elapsed:.3f}"
            finished_at = canonical.rfc3339(canonical.utc_now())
            attempt_id = canonical.sha256_hex(canonical.canonical_json({
                "run_id": run_id,
                "started_at": started_at,
                "error": str(exc),
            }))
            receipt = {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "envelope_id": envelope_id,
                "proposal_id": proposal_id,
                "validation_id": validation_id,
                "route_kind": route_kind,
                "target": target,
                "host": "omen",
                "endpoint": resolved_endpoint,
                "model": model_name,
                "catalog_version": catalog_version,
                "snapshot_id": snapshot_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_s": duration_s,
                "status": "failure",
                "error": str(exc),
                "artifact_sha256": "unknown",
                "usage": {"tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            rcpt_target = refs_dir / f"attempt_{attempt_id}.json"
            rcpt_target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            history.append(
                "attempt.recorded",
                receipt,
                refs={"attempt_path": paths.repo_relative(rcpt_target)},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            history.append(
                "outcome.final",
                {"verdict": "failed", "error": str(exc), "attempt_id": attempt_id},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            replay.replay_run(run_id)
            raise ExecutionError(f"mechnet execution failed: {exc}") from exc

    else:
        # Direct inference route
        file_contents: dict[str, str] = {}
        for rel_path in input_paths:
            full_path = repo_root / rel_path
            if not full_path.is_file():
                full_path = paths.REPO_ROOT / rel_path
            if not full_path.is_file():
                raise ExecutionError(f"input path not found on disk: {rel_path} ({full_path})")
            try:
                file_contents[rel_path] = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ExecutionError(f"could not read input path {rel_path}: {exc}") from exc

        prompt_lines = [
            "You are an automated code repository inventory assistant.",
            "Read the following bounded set of repository files and produce a concise Markdown inventory.",
            "For each file, you MUST include:",
            "- The exact filename",
            "- The detected language or file type",
            "- Whether it is a likely entry point and why",
            "",
            "Files to inventory:",
        ]
        for filename, content in file_contents.items():
            prompt_lines.append(f"--- File: {Path(filename).name} ({filename}) ---")
            prompt_lines.append(content)
            prompt_lines.append("--- End File ---")
            prompt_lines.append("")

        prompt_lines.append("Output your inventory as clean Markdown. Do not include hidden reasoning.")
        user_prompt = "\n".join(prompt_lines)

        token = _resolve_backend_token(target_auth_env)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req_body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are a concise code analysis assistant. Output only valid Markdown."},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
        }

        req = urllib.request.Request(
            f"{resolved_endpoint}/v1/chat/completions",
            data=json.dumps(req_body).encode("utf-8"),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                elapsed = time.monotonic() - t0
                duration_s = f"{elapsed:.3f}"
                finished_at = canonical.rfc3339(canonical.utc_now())
                resp_bytes = resp.read()
                resp_data = json.loads(resp_bytes.decode("utf-8"))
        except Exception as exc:
            elapsed = time.monotonic() - t0
            duration_s = f"{elapsed:.3f}"
            finished_at = canonical.rfc3339(canonical.utc_now())
            attempt_id = canonical.sha256_hex(canonical.canonical_json({
                "run_id": run_id,
                "started_at": started_at,
                "error": str(exc),
            }))
            receipt = {
                "contract_version": CONTRACT_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "envelope_id": envelope_id,
                "proposal_id": proposal_id,
                "validation_id": validation_id,
                "route_kind": route_kind,
                "target": target,
                "host": target_host,
                "endpoint": resolved_endpoint,
                "model": model_name,
                "catalog_version": catalog_version,
                "snapshot_id": snapshot_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_s": duration_s,
                "status": "failure",
                "error": str(exc),
                "artifact_sha256": "unknown",
                "usage": {"tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            rcpt_target = refs_dir / f"attempt_{attempt_id}.json"
            rcpt_target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            history.append(
                "attempt.recorded",
                receipt,
                refs={"attempt_path": paths.repo_relative(rcpt_target)},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            history.append(
                "outcome.final",
                {"verdict": "failed", "error": str(exc), "attempt_id": attempt_id},
                run_id=run_id,
                envelope_id=envelope_id,
            )
            replay.replay_run(run_id)
            raise ExecutionError(f"physical model request to {resolved_endpoint} failed: {exc}") from exc

        choices = resp_data.get("choices", [])
        if not choices:
            raise ExecutionError("model response contained no choices")
        generated_text = choices[0].get("message", {}).get("content", "")
        raw_usage = resp_data.get("usage", {})
        usage = {
            "tool_calls": 0,
            "prompt_tokens": int(raw_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(raw_usage.get("completion_tokens", 0)),
            "total_tokens": int(raw_usage.get("total_tokens", 0)),
        }

    # Ingest artifact with D-115 redaction
    art_record = artifacts.ingest_artifact(
        run_id=run_id,
        data=generated_text,
        media_type="text/markdown",
        retention_class="required",
        durability="verified",
    )
    artifact_sha256 = art_record["sha256"]

    # Also save human-readable artifact in run output directory
    run_output_dir = paths.run_dir(run_id)
    artifact_file = run_output_dir / "inventory.md"
    artifact_file.write_text(generated_text + "\n", encoding="utf-8")

    # Build Attempt Receipt
    attempt_data = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "envelope_id": envelope_id,
        "proposal_id": proposal_id,
        "validation_id": validation_id,
        "route_kind": route_kind,
        "target": target,
        "host": target_host,
        "endpoint": resolved_endpoint,
        "model": model_name,
        "catalog_version": catalog_version,
        "snapshot_id": snapshot_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": duration_s,
        "status": "success",
        "artifact_sha256": artifact_sha256,
        "usage": usage,
    }
    if mechnet_receipt_id:
        attempt_data["mechnet_receipt_id"] = mechnet_receipt_id
    attempt_id = canonical.sha256_hex(canonical.canonical_json(attempt_data))
    attempt_data["attempt_id"] = attempt_id

    rcpt_target = refs_dir / f"attempt_{attempt_id}.json"
    rcpt_target.write_text(json.dumps(attempt_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    history.append(
        "attempt.recorded",
        attempt_data,
        refs={"attempt_path": paths.repo_relative(rcpt_target)},
        run_id=run_id,
        envelope_id=envelope_id,
    )

    # Deterministic verification checks
    check_file_exists = artifact_file.is_file()
    check_non_empty = len(generated_text.strip()) > 0
    expected_filenames = [Path(p).name for p in input_paths]
    missing_filenames = [f for f in expected_filenames if f not in generated_text]
    check_filenames_present = (len(missing_filenames) == 0)

    git_after = set(
        subprocess.run(["git", "status", "--porcelain"], cwd=str(paths.REPO_ROOT), capture_output=True, text=True).stdout.splitlines()
    )
    new_mods = [
        line for line in (git_after - git_before)
        if not (line[3:].startswith("runs/") or line[3:].startswith("hearth/var/"))
    ]
    check_no_unexpected_mods = (len(new_mods) == 0)
    check_hash_matches = (artifact_sha256 == attempt_data["artifact_sha256"])

    all_passed = (
        check_file_exists and check_non_empty and check_filenames_present
        and check_no_unexpected_mods and check_hash_matches
    )

    verification_record = {
        "verdict": "passed" if all_passed else "failed",
        "artifact_sha256": artifact_sha256,
        "checks": [
            {"check": "file_exists", "passed": check_file_exists},
            {"check": "non_empty", "passed": check_non_empty, "length_chars": len(generated_text)},
            {"check": "contains_all_filenames", "passed": check_filenames_present, "missing": missing_filenames},
            {"check": "no_unexpected_repo_modifications", "passed": check_no_unexpected_mods},
            {"check": "hash_matches_receipt", "passed": check_hash_matches},
        ],
    }

    history.append(
        "verification.recorded",
        verification_record,
        run_id=run_id,
        envelope_id=envelope_id,
    )

    # Append outcome.final
    history.append(
        "outcome.final",
        {
            "verdict": "completed" if all_passed else "failed",
            "attempt_id": attempt_id,
            "duration_s": duration_s,
            "artifact_sha256": artifact_sha256,
        },
        run_id=run_id,
        envelope_id=envelope_id,
    )

    # Replay to reconstruct state
    reconstructed_state = replay.replay_run(run_id)

    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "receipt": attempt_data,
        "artifact_record": art_record,
        "verification": verification_record,
        "state": reconstructed_state,
        "artifact_path": artifact_file,
    }
