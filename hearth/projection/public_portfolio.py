"""Export a content-free, privacy-gated proof snapshot for steppeintegrations.com.

The private ledgers contain prompt previews, paths, identities, exact timestamps,
and error details. None of those values are copied or pseudonymized here. This
projection emits only fixed-dimension counts, day-granularity windows, coarse
MechNet state, and hashes of the consumed append-only prefixes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from hearth.execution import (
    ExecutionEventError,
    ExecutionLedger,
    ExecutionLedgerError,
    validate_execution_event,
)
from hearth.projection.call_mix_dashboard import FAMILY_ORDER, classify_event


SCHEMA_ID = "steppe.public-system-proof.v1"
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "hearth" / "contracts" / "public-system-proof.v1.schema.json"
DEFAULT_GATEWAY_LEDGER = ROOT / "hearth" / "var" / "ledger" / "events.ndjson"
DEFAULT_EXECUTION_LEDGER = ROOT / "hearth" / "var" / "execution" / "events.ndjson"
DEFAULT_OUT = ROOT / "hearth" / "var" / "public-portfolio" / "candidate.json"
MINIMUM_PUBLIC_CELL = 10

FAMILY_IDS = {
    "Health / automation": "health_automation",
    "Door status": "door_status",
    "Learning / retro": "learning_retro",
    "Local inference": "local_inference",
    "Cloud / remote inference": "cloud_inference",
    "Image generation": "image_generation",
    "Media / video render": "media_render",
    "Fleet / builds": "fleet_builds",
    "Git / VCS": "git_vcs",
    "Filesystem": "filesystem",
    "Test / assay": "test_assay",
    "Catalog / hardware": "catalog_hardware",
    "Scheduler": "scheduler",
    "Other": "other",
}
MACRO_IDS = {
    "Health / automation": "operations",
    "Door status": "operations",
    "Learning / retro": "learning",
    "Local inference": "inference",
    "Cloud / remote inference": "inference",
    "Image generation": "media",
    "Media / video render": "media",
    "Fleet / builds": "work_plane",
    "Git / VCS": "work_plane",
    "Filesystem": "work_plane",
    "Test / assay": "work_plane",
    "Catalog / hardware": "other",
    "Scheduler": "other",
    "Other": "other",
}
# Presentation-only relabels. The family id is the stable public contract; the
# label is text, so "Door status" can say what it actually measures without
# renaming the id every consumer already keys on.
PUBLIC_LABELS = {"Door status": "Door status / polling"}
WEEKLY_MACRO_KEYS = ("operations", "learning", "inference", "media", "work_plane", "other")
# A "work call" is a measured gateway call outside health polling and door
# status. It is a volume statement, never a statement about who did the work.
NON_WORK_FAMILIES = {"Health / automation", "Door status"}

# The only place raw execution operation names may live. Operations are private
# routing identifiers: they are folded into this closed set of public family ids
# before anything is emitted, and an unmapped or absent operation lands in
# "other" rather than leaking its own name.
EXECUTION_FAMILY = {
    "image.generate": "image_generation",
    "bf6.process_segment": "video_highlight_render",
    "bf6.render_clip_workflow": "video_highlight_render",
    "media.render": "media_render",
    "media.podcast": "media_pipeline",
    "media.animate": "media_pipeline",
    "media.pipeline": "media_pipeline",
    "llm.chat": "inference",
    "inference.generate": "inference",
    "inference.external": "inference",
}
EXECUTION_FAMILY_ORDER = [
    "image_generation",
    "video_highlight_render",
    "media_render",
    "media_pipeline",
    "inference",
    "other",
]
EXECUTION_FAMILY_LABELS = {
    "image_generation": "Image generation jobs",
    "video_highlight_render": "Clippy · BF6 highlight renders",
    "media_render": "Media renders",
    "media_pipeline": "Media pipelines",
    "inference": "Inference jobs",
    "other": "Other jobs",
}
TERMINAL_JOB_EVENTS = {
    "job.succeeded": "jobs_succeeded",
    "job.failed": "jobs_failed",
    "job.cancelled": "jobs_cancelled",
    "job.expired": "jobs_expired",
}

# An "agent lane" is observed activity through a class of caller. It is a fixed
# public label derived from a private identifier; the identifier itself is never
# emitted, and a lane makes no claim of authorship or ownership of the work.
AGENT_LANE_BY_CALLER = {
    "deepagents-direct": "deepagents",
    "claude-frontier": "claude_code",
    "codex-cli": "codex",
    "dmos-poc": "dmos_image_client",
    "bf6-dispatcher": "clippy_dispatcher",
    "botherder-am4": "irc_adapter",
    "omen-worker-1": "fleet_workers",
    "peer-inference": "fleet_workers",
    "mechnet-orchestrator": "fleet_workers",
    "mechnet-watchdog": "automation",
    "bankedfire-drain": "automation",
    "dev-local": "automation",
}
AGENT_LANE_BY_ADAPTER = {**AGENT_LANE_BY_CALLER, "bf6-hatchet": "clippy_dispatcher"}
AGENT_LANE_ORDER = [
    "deepagents",
    "claude_code",
    "codex",
    "dmos_image_client",
    "clippy_dispatcher",
    "irc_adapter",
    "fleet_workers",
    "automation",
    "other",
]
AGENT_LANE_LABELS = {
    "deepagents": "DeepAgents",
    "claude_code": "Claude Code",
    "codex": "Codex",
    "dmos_image_client": "DMos image client",
    "clippy_dispatcher": "Clippy dispatcher",
    "irc_adapter": "IRC adapter",
    "fleet_workers": "Fleet workers",
    "automation": "Automation",
    "other": "Other",
}
UNATTRIBUTED_LANE = "other"
RUNG_STATES = {"at_rate", "warn", "degraded", "stalled", "stale", "unreachable"}
PUBLIC_KEYS = {
    "external_inference", "attempts", "measured_attempts", "unknown_usage_attempts",
    "failed_attempts", "setup", "task", "restoration", "by_phase",
    "schema", "snapshot_id", "source_watermark_day", "observation_window",
    "first_day", "last_day", "gateway", "execution", "weekly", "mechnet",
    "coverage", "provenance", "integrity", "events", "ok_events",
    "ok_rate_basis_points", "operational_observations", "work_and_learning_events",
    "unclassified_events", "families", "inference", "id", "label", "count",
    "calls", "local_calls", "cloud_calls", "token_receipts", "tokens_in",
    "tokens_out", "requests_accepted", "requests_with_idempotency_key",
    "jobs_succeeded", "jobs_failed", "jobs_cancelled", "jobs_expired",
    "success_rate_basis_points", "invocations_started", "retried_jobs",
    "recovered_jobs", "artifacts_recorded", "deliveries_projected",
    "projection_replay_verified", "week_start", "operations", "learning",
    "work_plane", "other", "suppressed_cells", "accelerator", "operating_system",
    "compute_runtime", "topology", "snapshot_state", "observed_day", "boundary",
    "raw_content_withheld", "minimum_public_cell", "limitations",
    "exporter_revision", "exporter_sha256", "gateway_prefix_sha256",
    "execution_prefix_sha256", "content_sha256",
    "by_family", "by_agent", "media", "work_calls", "jobs_accepted",
}
FORBIDDEN_SOURCE_KEYS = {
    "args_preview", "caller", "task_id", "event_id", "reason", "error", "hostname",
    "port", "path", "prompt", "request_id", "job_id", "invocation_id", "principal",
    "desired", "observed", "source", "operation",
}
FORBIDDEN_TEXT = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\\Users\\", re.IGNORECASE),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
)


class PublicProjectionError(RuntimeError):
    pass


def _day(value: Any) -> str | None:
    candidate = str(value or "")[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _week_start(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def _number(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return int(value)
    return 0


def _basis_points(numerator: int, denominator: int) -> int:
    return round(10000 * numerator / denominator) if denominator else 0


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _source_digest() -> str:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(SCHEMA_PATH.read_bytes())
    return digest.hexdigest()


def _gateway_lane(event: dict[str, Any]) -> str:
    """Map one gateway event to a public agent lane. Identifiers stay private."""
    caller = event.get("caller")
    caller_id = str(caller.get("id") or "") if isinstance(caller, dict) else ""
    return AGENT_LANE_BY_CALLER.get(caller_id, UNATTRIBUTED_LANE)


def _execution_lane(event: dict[str, Any]) -> str:
    """Map one execution event to a public agent lane.

    Precedence (D-015): ``principal.id`` first; when it is absent or maps to the
    unattributed lane, fall back to ``source.adapter``; otherwise unattributed.
    """
    principal = event.get("principal")
    principal_id = str(principal.get("id") or "") if isinstance(principal, dict) else ""
    lane = AGENT_LANE_BY_CALLER.get(principal_id, UNATTRIBUTED_LANE)
    if lane != UNATTRIBUTED_LANE:
        return lane
    source = event.get("source")
    adapter = str(source.get("adapter") or "") if isinstance(source, dict) else ""
    return AGENT_LANE_BY_ADAPTER.get(adapter, UNATTRIBUTED_LANE)


def _execution_family(operation: Any) -> str:
    """Fold a private operation name into a closed public family id."""
    if not isinstance(operation, str):
        return "other"
    return EXECUTION_FAMILY.get(operation, "other")


def _agent_lane_rows(
    gateway_lanes: dict[str, Counter[str]], execution_lanes: dict[str, Counter[str]]
) -> list[dict[str, Any]]:
    """Always emit every lane, in fixed order, so absence reads as zero."""
    rows: list[dict[str, Any]] = []
    for lane in AGENT_LANE_ORDER:
        calls = gateway_lanes.get(lane) or {}
        jobs = execution_lanes.get(lane) or {}
        rows.append({
            "id": lane,
            "label": AGENT_LANE_LABELS[lane],
            "calls": calls.get("calls", 0),
            "work_calls": calls.get("work_calls", 0),
            "jobs_accepted": jobs.get("jobs_accepted", 0),
            "jobs_succeeded": jobs.get("jobs_succeeded", 0),
        })
    return rows


def _scan_gateway(path: Path) -> dict[str, Any]:
    families: Counter[str] = Counter()
    weekly: dict[str, Counter[str]] = defaultdict(Counter)
    agent_calls: dict[str, Counter[str]] = defaultdict(Counter)
    days: list[str] = []
    digest = hashlib.sha256()
    total = ok = parse_errors = 0
    local_calls = cloud_calls = token_receipts = tokens_in = tokens_out = 0
    latest_rung: tuple[str, str] | None = None

    with path.open("rb") as stream:
        for encoded in stream:
            digest.update(encoded)
            if not encoded.strip():
                continue
            try:
                event = json.loads(encoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                parse_errors += 1
                continue
            if not isinstance(event, dict):
                parse_errors += 1
                continue
            total += 1
            ok += int(event.get("ok") is True)
            family = classify_event(event)
            families[family] += 1
            lane = _gateway_lane(event)
            agent_calls[lane]["calls"] += 1
            if family not in NON_WORK_FAMILIES:
                agent_calls[lane]["work_calls"] += 1
            event_day = _day(event.get("ts"))
            if event_day:
                days.append(event_day)
                weekly[_week_start(event_day)][MACRO_IDS[family]] += 1

            if family in {"Local inference", "Cloud / remote inference"}:
                if family == "Local inference":
                    local_calls += 1
                else:
                    cloud_calls += 1
                cost = event.get("cost") if isinstance(event.get("cost"), dict) else {}
                if isinstance(cost.get("tokens_in"), (int, float)) or isinstance(cost.get("tokens_out"), (int, float)):
                    token_receipts += 1
                    tokens_in += _number(cost.get("tokens_in"))
                    tokens_out += _number(cost.get("tokens_out"))

            if event.get("tool") == "mechnet_watchdog.rung_state" and event_day:
                outcome = str(event.get("outcome") or "")
                if outcome in RUNG_STATES:
                    latest_rung = (event_day, outcome)

    if parse_errors:
        raise PublicProjectionError(f"gateway ledger has {parse_errors} malformed rows")
    if not days:
        raise PublicProjectionError("gateway ledger has no valid dated events")

    weekly_rows = []
    for week in sorted(weekly):
        row: dict[str, Any] = {"week_start": week}
        suppressed = 0
        for key in WEEKLY_MACRO_KEYS:
            value = weekly[week].get(key, 0)
            if 0 < value < MINIMUM_PUBLIC_CELL:
                row[key] = None
                suppressed += 1
            else:
                row[key] = value
        row["suppressed_cells"] = suppressed
        weekly_rows.append(row)

    family_rows = [
        {
            "id": FAMILY_IDS[name],
            "label": PUBLIC_LABELS.get(name, name),
            "count": families.get(name, 0),
        }
        for name in FAMILY_ORDER
    ]
    operations = families["Health / automation"] + families["Door status"]
    work_and_learning = sum(
        count for name, count in families.items()
        if name not in {"Health / automation", "Door status", "Other"}
    )
    return {
        "public": {
            "events": total,
            "ok_events": ok,
            "ok_rate_basis_points": _basis_points(ok, total),
            "operational_observations": operations,
            "work_and_learning_events": work_and_learning,
            "unclassified_events": families["Other"],
            "families": family_rows,
            "inference": {
                "calls": local_calls + cloud_calls,
                "local_calls": local_calls,
                "cloud_calls": cloud_calls,
                "token_receipts": token_receipts,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        },
        "weekly": weekly_rows,
        "agent_calls": agent_calls,
        "first_day": min(days),
        "last_day": max(days),
        "prefix_sha256": digest.hexdigest(),
        "rung": latest_rung,
    }


def _scan_execution(path: Path) -> dict[str, Any]:
    external = {key: 0 for key in ("attempts", "measured_attempts", "unknown_usage_attempts", "failed_attempts", "tokens_in", "tokens_out")}
    phases = {phase: dict(external) for phase in ("setup", "task", "restoration")}
    event_types: Counter[str] = Counter()
    job_attempts: dict[str, Counter[str]] = defaultdict(Counter)
    job_family: dict[str, str] = {}
    job_lane: dict[str, str] = {}
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    agent_jobs: dict[str, Counter[str]] = defaultdict(Counter)
    replayed_states: dict[str, dict[str, Any]] = {}
    reducer = object.__new__(ExecutionLedger)
    days: list[str] = []
    digest = hashlib.sha256()
    total = parse_errors = artifacts = idempotent_requests = 0
    expected_sequence = 1

    with path.open("rb") as stream:
        for encoded in stream:
            digest.update(encoded)
            if not encoded.strip():
                continue
            try:
                event = json.loads(encoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                parse_errors += 1
                continue
            if not isinstance(event, dict):
                parse_errors += 1
                continue
            total += 1
            if event.get("sequence") != expected_sequence:
                raise PublicProjectionError(
                    f"execution ledger sequence is not contiguous at public position {expected_sequence}"
                )
            try:
                validate_execution_event(event)
                job_id = event["job_id"]
                replayed_states[job_id] = reducer._reduce(replayed_states.get(job_id), event)
            except (ExecutionEventError, ExecutionLedgerError, KeyError, TypeError, ValueError) as exc:
                raise PublicProjectionError(
                    f"execution ledger replay failed at public position {expected_sequence}"
                ) from exc
            expected_sequence += 1
            source = event.get("source") or {}
            if (source.get("execution_mode") == "external" and source.get("accounting_owner") == "direct"
                    and source.get("adapter") == "deepagents-direct"
                    and event["event_type"] in {"invocation.succeeded", "invocation.failed"}):
                from hearth.execution.external_inference import validate_receipt, canonical
                receipt = (event.get("observed") or {}).get("external_receipt")
                try:
                    validate_receipt(receipt)
                    state = replayed_states[event["job_id"]]
                    receipt_hash = hashlib.sha256(canonical(receipt).encode()).hexdigest()
                    if receipt_hash != state["desired"].get("receipt_sha256"):
                        raise ValueError("receipt digest mismatch")
                except (TypeError, AttributeError, ValueError) as exc:
                    raise PublicProjectionError("invalid direct inference receipt") from exc
                usage = receipt.get("usage") or {}
                measured = all(type(usage.get(k)) is int for k in ("tokens_in", "tokens_out"))
                for counter in (external, phases[receipt["phase"]]):
                    counter["attempts"] += 1
                    counter["measured_attempts"] += int(measured)
                    counter["unknown_usage_attempts"] += int(not measured)
                    counter["failed_attempts"] += int(receipt["outcome"] != "succeeded")
                    counter["tokens_in"] += usage.get("tokens_in") or 0
                    counter["tokens_out"] += usage.get("tokens_out") or 0
            event_type = str(event.get("event_type") or "")
            event_types[event_type] += 1
            event_day = _day(event.get("timestamp"))
            if event_day:
                days.append(event_day)
            job = str(event.get("job_id") or "")
            if job and event_type == "invocation.started":
                job_attempts[job]["started"] += 1
            elif job and event_type == "invocation.failed":
                job_attempts[job]["failed"] += 1
            elif job and event_type == "invocation.succeeded":
                job_attempts[job]["succeeded"] += 1
            if event_type == "artifact.recorded" and isinstance(event.get("artifacts"), list):
                artifacts += len(event["artifacts"])
            if event_type == "request.accepted":
                desired = event.get("desired") if isinstance(event.get("desired"), dict) else {}
                idempotent_requests += int(bool(desired.get("idempotency_key")))
                family = _execution_family(event.get("operation"))
                lane = _execution_lane(event)
                job_family[job] = family
                job_lane[job] = lane
                family_counts[family]["requests_accepted"] += 1
                agent_jobs[lane]["jobs_accepted"] += 1
            elif event_type in TERMINAL_JOB_EVENTS:
                # A terminal event is attributed to the family and lane its
                # accepted request recorded. A job that was never accepted
                # cannot reach this line through the replay gate above; the
                # fallbacks keep the projection closed rather than raising a
                # second, later error.
                family_counts[job_family.get(job, "other")][TERMINAL_JOB_EVENTS[event_type]] += 1
                if event_type == "job.succeeded":
                    agent_jobs[job_lane.get(job, UNATTRIBUTED_LANE)]["jobs_succeeded"] += 1

    if parse_errors:
        raise PublicProjectionError(f"execution ledger has {parse_errors} malformed rows")
    if not days:
        raise PublicProjectionError("execution ledger has no valid dated events")
    if not replayed_states:
        raise PublicProjectionError("execution ledger replay produced no job projections")

    terminal = sum(event_types[name] for name in TERMINAL_JOB_EVENTS)
    retried = sum(1 for stats in job_attempts.values() if stats["started"] > 1)
    recovered = sum(1 for stats in job_attempts.values() if stats["failed"] and stats["succeeded"])
    by_family = [
        {
            "id": family,
            "label": EXECUTION_FAMILY_LABELS[family],
            "requests_accepted": family_counts[family]["requests_accepted"],
            "jobs_succeeded": family_counts[family]["jobs_succeeded"],
            "jobs_failed": family_counts[family]["jobs_failed"],
            "jobs_cancelled": family_counts[family]["jobs_cancelled"],
            "jobs_expired": family_counts[family]["jobs_expired"],
        }
        for family in EXECUTION_FAMILY_ORDER
    ]
    return {
        "public": {
            "events": total,
            "requests_accepted": event_types["request.accepted"],
            "requests_with_idempotency_key": idempotent_requests,
            "jobs_succeeded": event_types["job.succeeded"],
            "jobs_failed": event_types["job.failed"],
            "jobs_cancelled": event_types["job.cancelled"],
            "jobs_expired": event_types["job.expired"],
            "success_rate_basis_points": _basis_points(event_types["job.succeeded"], terminal),
            "invocations_started": event_types["invocation.started"],
            "retried_jobs": retried,
            "recovered_jobs": recovered,
            "artifacts_recorded": artifacts,
            "deliveries_projected": event_types["delivery.projected"],
            "projection_replay_verified": True,
            "by_family": by_family,
        },
        "agent_jobs": agent_jobs,
        "external_inference": {**external, "by_phase": phases} if external["attempts"] else None,
        "first_day": min(days),
        "last_day": max(days),
        "prefix_sha256": digest.hexdigest(),
    }


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


def validate_public_snapshot(snapshot: dict[str, Any]) -> None:
    if "external_inference" in snapshot:
        external = snapshot["external_inference"]
        keys = ("attempts", "measured_attempts", "unknown_usage_attempts", "failed_attempts", "tokens_in", "tokens_out")
        try:
            phases = external["by_phase"]
            if set(phases) != {"setup", "task", "restoration"}:
                raise ValueError("invalid phases")
            for row in [external, *phases.values()]:
                if any(type(row[k]) is not int or row[k] < 0 for k in keys):
                    raise ValueError("invalid count")
                if row["measured_attempts"] + row["unknown_usage_attempts"] != row["attempts"] or row["failed_attempts"] > row["attempts"]:
                    raise ValueError("attempt counts do not reconcile")
            if any(sum(row[k] for row in phases.values()) != external[k] for k in keys):
                raise ValueError("phase counts do not reconcile")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PublicProjectionError("invalid external inference aggregate") from exc
    unknown_keys = set(_walk_keys(snapshot)) - PUBLIC_KEYS
    if unknown_keys:
        raise PublicProjectionError(f"public snapshot contains undeclared keys: {sorted(unknown_keys)}")
    leaked_keys = set(_walk_keys(snapshot)) & FORBIDDEN_SOURCE_KEYS
    if leaked_keys:
        raise PublicProjectionError(f"public snapshot contains private source keys: {sorted(leaked_keys)}")
    serialized = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    for pattern in FORBIDDEN_TEXT:
        if pattern.search(serialized):
            raise PublicProjectionError(f"public snapshot matches forbidden content pattern {pattern.pattern!r}")

    if snapshot.get("schema") != SCHEMA_ID:
        raise PublicProjectionError(f"public snapshot schema must be {SCHEMA_ID}")
    candidate = dict(snapshot)
    integrity = candidate.pop("integrity", None)
    snapshot_id = candidate.pop("snapshot_id", None)
    canonical = json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if integrity != {"content_sha256": expected} or snapshot_id != f"sha256:{expected}":
        raise PublicProjectionError("public snapshot content digest does not match its payload")

    try:
        import jsonschema
    except ImportError:
        required = {
            "schema", "snapshot_id", "source_watermark_day", "observation_window",
            "gateway", "execution", "weekly", "mechnet", "coverage", "provenance", "integrity",
        }
        if set(snapshot) - {"external_inference"} != required:
            raise PublicProjectionError("public snapshot top-level shape does not match v1")
    else:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            jsonschema.Draft202012Validator(schema).validate(snapshot)
        except jsonschema.ValidationError as exc:
            raise PublicProjectionError(f"public snapshot schema validation failed: {exc.message}") from exc


def build_snapshot(
    gateway_ledger: Path = DEFAULT_GATEWAY_LEDGER,
    execution_ledger: Path = DEFAULT_EXECUTION_LEDGER,
    *,
    exporter_revision: str | None = None,
) -> dict[str, Any]:
    gateway = _scan_gateway(Path(gateway_ledger))
    execution = _scan_execution(Path(execution_ledger))
    first_day = min(gateway["first_day"], execution["first_day"])
    last_day = max(gateway["last_day"], execution["last_day"])
    rung = gateway["rung"]

    payload: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "source_watermark_day": last_day,
        "observation_window": {"first_day": first_day, "last_day": last_day},
        "gateway": {
            **gateway["public"],
            "by_agent": _agent_lane_rows(gateway["agent_calls"], execution["agent_jobs"]),
        },
        "execution": execution["public"],
        "weekly": gateway["weekly"],
        "mechnet": {
            "accelerator": "2 × Intel Arc Pro B70",
            "operating_system": "Windows",
            "compute_runtime": "Vulkan · llama.cpp",
            "topology": "role-abstracted local fleet",
            "snapshot_state": rung[1] if rung else "unknown",
            "observed_day": rung[0] if rung else None,
        },
        "coverage": {
            "boundary": "calls observed at the HEARTH gateway and execution ledgers",
            "raw_content_withheld": True,
            "minimum_public_cell": MINIMUM_PUBLIC_CELL,
            "limitations": [
                "Direct model, shell, file, and cloud calls made around HEARTH are outside this boundary.",
                "Counts prove observed flow and recovery behavior; they do not measure business value or authorship.",
                "Image and media rows count execution-ledger jobs (one per accepted request), never gateway status polls.",
                "Image jobs run before the execution ledger existed (May 2026) are not claimed.",
                "Agent lanes are fixed labels derived from caller class; caller identities are never published.",
            ],
        },
        "provenance": {
            "exporter_revision": exporter_revision or _git_revision(),
            "exporter_sha256": _source_digest(),
            "gateway_prefix_sha256": gateway["prefix_sha256"],
            "execution_prefix_sha256": execution["prefix_sha256"],
        },
    }
    if execution["external_inference"] is not None:
        payload["external_inference"] = execution["external_inference"]
        payload["coverage"]["boundary"] = "HEARTH gateway and execution ledgers, including instrumented direct local inference"
        payload["coverage"]["limitations"][0] = "Uninstrumented model calls and direct shell, file and cloud calls remain outside this boundary."
        payload["coverage"]["limitations"].append("External inference is a disjoint direct-owned cohort; local tokens include mixed CPU and accelerator placement and do not establish accelerator-hours.")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snapshot = {**payload, "snapshot_id": f"sha256:{digest}", "integrity": {"content_sha256": digest}}
    validate_public_snapshot(snapshot)
    return snapshot


def write_snapshot(output: Path, gateway_ledger: Path, execution_ledger: Path) -> dict[str, Any]:
    snapshot = build_snapshot(gateway_ledger, execution_ledger)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage a privacy-safe Steppe portfolio snapshot")
    parser.add_argument("--gateway-ledger", type=Path, default=DEFAULT_GATEWAY_LEDGER)
    parser.add_argument("--execution-ledger", type=Path, default=DEFAULT_EXECUTION_LEDGER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        snapshot = write_snapshot(args.out, args.gateway_ledger, args.execution_ledger)
    except (OSError, PublicProjectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Staged {snapshot['gateway']['events']:,} aggregate boundary events through {snapshot['source_watermark_day']}")
    print(f"  JSON: {args.out.resolve()}")
    print(f"  Snapshot: {snapshot['snapshot_id']}")
    print("  Publication: candidate only; explicit site promotion is still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
