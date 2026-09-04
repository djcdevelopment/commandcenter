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
    "Fleet / builds": "work_plane",
    "Git / VCS": "work_plane",
    "Filesystem": "work_plane",
    "Test / assay": "work_plane",
    "Catalog / hardware": "other",
    "Scheduler": "other",
    "Other": "other",
}
RUNG_STATES = {"at_rate", "warn", "degraded", "stalled", "stale", "unreachable"}
PUBLIC_KEYS = {
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
}
FORBIDDEN_SOURCE_KEYS = {
    "args_preview", "caller", "task_id", "event_id", "reason", "error", "hostname",
    "port", "path", "prompt", "request_id", "job_id", "invocation_id", "principal",
    "desired", "observed", "source",
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


def _scan_gateway(path: Path) -> dict[str, Any]:
    families: Counter[str] = Counter()
    weekly: dict[str, Counter[str]] = defaultdict(Counter)
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
        for key in ("operations", "learning", "inference", "work_plane", "other"):
            value = weekly[week].get(key, 0)
            if 0 < value < MINIMUM_PUBLIC_CELL:
                row[key] = None
                suppressed += 1
            else:
                row[key] = value
        row["suppressed_cells"] = suppressed
        weekly_rows.append(row)

    family_rows = [
        {"id": FAMILY_IDS[name], "label": name, "count": families.get(name, 0)}
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
        "first_day": min(days),
        "last_day": max(days),
        "prefix_sha256": digest.hexdigest(),
        "rung": latest_rung,
    }


def _scan_execution(path: Path) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    job_attempts: dict[str, Counter[str]] = defaultdict(Counter)
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

    if parse_errors:
        raise PublicProjectionError(f"execution ledger has {parse_errors} malformed rows")
    if not days:
        raise PublicProjectionError("execution ledger has no valid dated events")
    if not replayed_states:
        raise PublicProjectionError("execution ledger replay produced no job projections")

    terminal = sum(event_types[name] for name in ("job.succeeded", "job.failed", "job.cancelled", "job.expired"))
    retried = sum(1 for stats in job_attempts.values() if stats["started"] > 1)
    recovered = sum(1 for stats in job_attempts.values() if stats["failed"] and stats["succeeded"])
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
        },
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
        if set(snapshot) != required:
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
        "gateway": gateway["public"],
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
            ],
        },
        "provenance": {
            "exporter_revision": exporter_revision or _git_revision(),
            "exporter_sha256": _source_digest(),
            "gateway_prefix_sha256": gateway["prefix_sha256"],
            "execution_prefix_sha256": execution["prefix_sha256"],
        },
    }
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
