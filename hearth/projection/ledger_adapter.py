"""Project HEARTH ledger events into the workflow event store.

Reads hearth/var/ledger/events.ndjson (hearth-event.v1) and appends
workflow events (contracts/workflow-event.schema.json) into a dedicated
run stream via the existing machinery (tools.workflow.append_event, which
validates every event). mcp-free: runs on system python.

Field mapping (hearth-event.v1 -> workflow event):
    event_id            -> event_id ("evt_hearth_" + event_id)
    ts                  -> timestamp
    (constant)          -> event_type "work.accepted" — the only ontology
                           type that is semantically neutral to tool-call
                           granularity and carries no extra required fields;
                           each gateway call is a unit of work the lab
                           accepted and executed. Downstream evidence
                           readers consume payload, not event_type.
    caller.id           -> actor.id
    caller.runner_class -> actor.type ("builder" for frontier|local,
                           "operator" for human) + payload.runner_class
    task_id             -> segment_id (nullable free-form) + payload.task_id
    ok                  -> status "completed"|"failed", outcome
                           "success"|"failure"
    tool, node, digests, args_preview, error
                        -> payload.*
    duration_ms, cost.* -> payload.duration_ms, payload.cost (economics)

Idempotent: hearth/var/projection_cursor.json records the last processed
event_id + line; re-runs process only new ledger lines (the ledger is
append-only, so line positions are stable).

SELF-MONITORING IS FILTERED OUT BY DEFAULT. 96% of the live ledger (19,295 of
20,110 rows on 2026-07-30) is the lab watching itself: the watchdog's
patrol_snapshot/watchfire/patrol_trend/hindsight/dream loop, bankedfire_drain
ticks, and kernel_status liveness probes. Bridging all of it produced an 18 MB
git-tracked file of heartbeat and left `corpus_event_count` dominated by rows no
projector can learn anything from.

The filter keys on `caller.id` and `profile`, NOT on tool names: caller identity
is a durable declared fact about who produced the row, while tool names
proliferate (`mechnet_watchdog.*` is already five distinct tools and growing).
Deciding what counts as evidence is the authored residue the constitution
reserves for the operator -- the instrumentation decision -- so it lives here as
named, overridable constants rather than as a silent heuristic.

Filtered rows still ADVANCE THE CURSOR: they are deliberately excluded, not
failed, so they are never reconsidered. Consequence to know about: widening the
filter later does not retroactively bridge rows already skipped past. Re-bridging
them needs a cursor reset (delete the cursor file) -- safe in itself, but
append_event does not deduplicate, so reset the TARGET stream too rather than
double-pouring into it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from tools.workflow.append_event import append_event
from tools.workflow.validate_events import ValidationError, validate_event
from tools.workflow.fsio import atomic_write_json

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")
DEFAULT_CURSOR = Path("hearth/var/projection_cursor.json")
DEFAULT_TARGET = Path("runs/hearth-gateway/events.jsonl")

WORKFLOW_ID = "wf-hearth-gateway"
RUN_ID = "hearth-gateway"

# Autonomous self-monitoring loops. These callers exist to watch the lab, so their
# rows describe the observer, not the work observed; 14,804 of 20,110 rows on
# 2026-07-30. Keyed on caller identity because that is what the loops declare about
# themselves and it does not churn as their tool surfaces grow.
HEARTBEAT_CALLER_IDS = frozenset({"mechnet-watchdog", "bankedfire-drain"})

# Liveness probes (4,491 kernel_status calls on 2026-07-30). `profile` is the
# gateway's own label for the call's authorization shape; "probe" means exactly
# "this call was a reachability check", which is the definition of not-evidence.
HEARTBEAT_PROFILES = frozenset({"probe"})

_ACTOR_TYPE_BY_RUNNER_CLASS = {
    "frontier": "builder",
    "local": "builder",
    "human": "operator",
}


def map_event(hearth_event: dict) -> dict:
    """Map one hearth-event.v1 dict to a workflow event dict."""
    if hearth_event.get("schema") != "hearth-event.v1":
        raise ValidationError(f"unknown hearth event schema: {hearth_event.get('schema')!r}")
    for field_name in ("event_id", "ts", "caller", "tool"):
        if not hearth_event.get(field_name):
            raise ValidationError(f"hearth event missing {field_name}")

    caller = hearth_event["caller"]
    runner_class = caller.get("runner_class")
    actor_type = _ACTOR_TYPE_BY_RUNNER_CLASS.get(runner_class)
    if actor_type is None:
        raise ValidationError(f"unknown runner_class: {runner_class!r}")
    if not caller.get("id"):
        raise ValidationError("hearth event caller missing id")

    ok = bool(hearth_event.get("ok"))
    cost = hearth_event.get("cost") or {}

    mapped_refs = []
    observation = build_capacity_observation(hearth_event)
    if observation is not None:
        mapped_refs = [{
            "artifact_id": observation["observation_id"],
            "artifact_type": "capacity_observation",
            "path": observation_relative_path(observation),
        }]

    return {
        "event_id": f"evt_hearth_{hearth_event['event_id']}",
        "event_type": "work.accepted",
        "timestamp": hearth_event["ts"],
        "workflow_id": WORKFLOW_ID,
        "run_id": RUN_ID,
        "segment_id": hearth_event.get("task_id"),
        "actor": {"type": actor_type, "id": caller["id"]},
        "status": "completed" if ok else "failed",
        "outcome": "success" if ok else "failure",
        "payload": {
            "source": "hearth-ledger",
            "hearth_schema": "hearth-event.v1",
            "hearth_event_id": hearth_event["event_id"],
            "tool": hearth_event["tool"],
            "runner_class": runner_class,
            "node": caller.get("node"),
            "args_digest": hearth_event.get("args_digest"),
            "args_preview": hearth_event.get("args_preview"),
            "result_digest": hearth_event.get("result_digest"),
            "error": hearth_event.get("error"),
            "task_id": hearth_event.get("task_id"),
            "duration_ms": hearth_event.get("duration_ms"),
            "cost": {
                "tokens_in": cost.get("tokens_in"),
                "tokens_out": cost.get("tokens_out"),
                "watt_s": cost.get("watt_s"),
            },
        },
        # Without this the bridge was a write-only mirror: every learning projector
        # extracts evidence ONLY through artifact_refs[].artifact_type
        # (project_capacity.extract_observations / extract_scheduler_decisions), so
        # 1,310 bridged rows produced observation_count 27 / decision_count 0 while the
        # corpus grew 1339 -> 1644. Empty list for non-inference rows keeps the shape
        # uniform for consumers.
        "artifact_refs": mapped_refs,
    }


def build_capacity_observation(hearth_event: dict) -> dict | None:
    """A capacity-observation.v1 document for an inference-shaped ledger row, or None.

    "Inference-shaped" == the row names a model. Those rows already carry everything an
    observation needs (model, backend, duration, token cost, outcome); the ledger was
    simply never read for it.

    The observation_id is DERIVED from the hearth event_id, never generated, so the
    bridge is idempotent: re-running over the same row rewrites the identical artifact
    instead of minting a second one. No wall clock is read (D18).
    """
    model = hearth_event.get("model")
    if not model:
        return None

    caller = hearth_event.get("caller") or {}
    cost = hearth_event.get("cost") or {}
    duration_ms = hearth_event.get("duration_ms")
    runtime_s = round(duration_ms / 1000.0, 3) if isinstance(duration_ms, (int, float)) else None
    tokens_out = cost.get("tokens_out")
    tokens_per_s = None
    if isinstance(tokens_out, (int, float)) and runtime_s:
        tokens_per_s = round(tokens_out / runtime_s, 2)

    ok = bool(hearth_event.get("ok"))
    notes = (f"routed_by={hearth_event.get('routed_by')}; "
             f"occupancy={hearth_event.get('occupancy')}; "
             f"bridged from hearth-event.v1 {hearth_event['event_id']}")

    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": f"obs-ledger-{hearth_event['event_id']}",
        "decision_id": None,
        "workflow_id": WORKFLOW_ID,
        "run_id": RUN_ID,
        "timestamp": hearth_event["ts"],
        # The caller is the builder here: capacity is a property of
        # (who dispatched, which model, which rung). The dispatch-time producer records
        # a provider name instead, so its combos stay distinct rather than merging.
        "builder_id": caller.get("id") or "unknown",
        "model_id": model,
        "backend": hearth_event.get("backend"),
        "hardware_profile_id": caller.get("node"),
        "workload_shape": {
            "task_kind": hearth_event.get("tool"),
            "estimated_context_tokens": cost.get("tokens_in"),
            "requires_gpu": None,
            "notes": notes,
        },
        "observed": {
            "runtime_s": runtime_s,
            "ttft_s": None,
            "tokens_per_s": tokens_per_s,
            "ram_gb_peak": None,
            "vram_gb_peak": None,
            "context_tokens": cost.get("tokens_in"),
            "physical": None,
        },
        "outcome": "success" if ok else "failure",
        "failure_class": hearth_event.get("error_code") if not ok else None,
        "promotion_status": None,
    }


def observation_relative_path(observation: dict) -> str:
    """Path recorded in the artifact ref: '<run_id>/artifacts/<date>/<id>.json'.

    project_capacity._resolve_artifact_path resolves everything after 'artifacts/'
    against the directory holding events.jsonl, so this stays correct wherever the run
    tree is rooted.
    """
    day = str(observation["timestamp"])[:10]
    return f"{RUN_ID}/artifacts/{day}/{observation['observation_id']}.json"


def _instant_key(raw: object) -> str | None:
    """Normalize a timestamp to a comparable instant string.

    The two producers spell the same moment differently -- the dispatch producer writes
    `...+00:00`, the ledger writes `...Z` -- so a raw string compare silently fails to
    dedupe. Comparing parsed instants is the only spelling-proof join available; there is
    no shared id between the two producers.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def dispatch_observation_keys(runs_root: Path) -> set[tuple]:
    """(timestamp, model_id, backend) for observations the DISPATCH-time producer already
    wrote under runs/hearth-offload-*/.

    Two producers now describe the same call: this bridge (reading the ledger after the
    fact) and hearth-offload-dispatch (writing at call time, with ttft/tokens_per_s the
    ledger never sees). The richer one wins -- the bridge declines any row a dispatch
    observation already covers, so capacity estimates count each call once.
    """
    keys: set[tuple] = set()
    if not runs_root.is_dir():
        return keys
    for run_dir in runs_root.glob("hearth-offload-*"):
        for artifact in run_dir.glob("artifacts/**/*.json"):
            try:
                document = json.loads(artifact.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            if document.get("contract_version") != "capacity-observation.v1":
                continue
            keys.add((_instant_key(document.get("timestamp")), document.get("model_id"),
                      document.get("backend")))
    return keys


def load_cursor(cursor_path: Path) -> dict:
    """Read the bridge cursor. A cursor that cannot be parsed is reported, not raised.

    This used to be a bare ``json.loads``, which made an unreadable cursor a
    PERMANENT, SELF-SUSTAINING outage: the bridge died reading the cursor before
    it could write a new one, so every later run died the same way and no ledger
    event ever reached the corpus again. Found 2026-08-29 with this file holding
    83 NUL bytes -- the classic crash-during-write signature, where the length was
    extended but the data never flushed. It had been failing silently since
    2026-08-20 and had stranded 144k ledger rows, freezing the whole belief layer.

    A corrupt cursor is flagged rather than swallowed: ``cursor_corrupt`` tells
    project_ledger to REBUILD the position from the target stream instead of
    quietly resuming from zero, which would re-append every already-bridged event.
    """
    if cursor_path.exists():
        raw = cursor_path.read_text(encoding="utf-8", errors="replace")
        try:
            cursor = json.loads(raw)
            if not isinstance(cursor, dict):
                raise json.JSONDecodeError("cursor is not an object", raw or "", 0)
            return cursor
        except json.JSONDecodeError as exc:
            return {"last_event_id": None, "line": 0, "cursor_corrupt": str(exc)}
    return {"last_event_id": None, "line": 0}


def _recover_cursor_from_target(target_path: Path) -> dict | None:
    """Reconstruct the bridge position from the last event already in the target.

    The bridge is append-only and every bridged row carries its source id at
    ``payload.hearth_event_id``, so the target stream is itself an authoritative
    record of how far the bridge got. Returning a cursor with that id lets
    _start_line scan the ledger for the true resume point.

    Returning None means "no usable target" -- an absent or empty stream, where
    starting from zero is genuinely correct rather than duplicative.
    """
    if not target_path.exists():
        return None
    last_id = None
    with target_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            candidate = (event.get("payload") or {}).get("hearth_event_id")
            if candidate:
                last_id = candidate
    if last_id is None:
        return None
    # line is a hint only: _start_line verifies it against the ledger and falls
    # back to scanning for the id, which is the path this recovery relies on.
    return {"last_event_id": last_id, "line": 1}


def save_cursor(cursor_path: Path, last_event_id: str, line: int) -> None:
    atomic_write_json(cursor_path, {"last_event_id": last_event_id, "line": line})


def _start_line(lines: list[str], cursor: dict) -> int:
    """Resume point. The ledger is append-only, so the recorded line should
    still hold the recorded event_id; if not (rotation/edit), fall back to
    scanning for the event_id, then to reprocessing from the start."""
    line = int(cursor.get("line") or 0)
    last_event_id = cursor.get("last_event_id")
    if last_event_id is None or line <= 0:
        return 0
    if line <= len(lines):
        try:
            if json.loads(lines[line - 1]).get("event_id") == last_event_id:
                return line
        except json.JSONDecodeError:
            pass
    for index, raw_line in enumerate(lines):
        try:
            if json.loads(raw_line).get("event_id") == last_event_id:
                return index + 1
        except json.JSONDecodeError:
            continue
    return 0


def is_heartbeat(hearth_event: dict,
                 caller_ids: frozenset[str] = HEARTBEAT_CALLER_IDS,
                 profiles: frozenset[str] = HEARTBEAT_PROFILES) -> bool:
    """True when the row is the lab observing itself rather than doing work."""
    caller = hearth_event.get("caller") or {}
    return caller.get("id") in caller_ids or hearth_event.get("profile") in profiles


def project_ledger(
    ledger_path: Path = DEFAULT_LEDGER,
    target_path: Path = DEFAULT_TARGET,
    cursor_path: Path = DEFAULT_CURSOR,
    dry_run: bool = False,
    filter_heartbeat: bool = True,
    caller_ids: frozenset[str] = HEARTBEAT_CALLER_IDS,
    profiles: frozenset[str] = HEARTBEAT_PROFILES,
) -> dict:
    """Project new ledger events into the workflow store.

    Returns {"processed", "skipped", "filtered", "errors"}. `skipped` counts rows
    the cursor was already past; `filtered` counts self-monitoring rows this run
    deliberately declined to bridge (see the module docstring).
    """
    summary = {"processed": 0, "skipped": 0, "filtered": 0, "observations": 0,
               "observations_deduped": 0, "errors": []}
    if not ledger_path.exists():
        summary["errors"].append(f"ledger not found: {ledger_path}")
        return summary

    run_dir = target_path.parent
    already_covered = dispatch_observation_keys(run_dir.parent)

    lines = [line for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cursor = load_cursor(cursor_path)
    if cursor.get("cursor_corrupt"):
        # Never resume a corrupt cursor from zero: append_event is unconditional
        # (only capacity observations dedupe), so that would re-append every row
        # the bridge already delivered. Rebuild the position from the target.
        summary["cursor_corrupt"] = cursor["cursor_corrupt"]
        recovered = _recover_cursor_from_target(target_path)
        if recovered is not None:
            cursor = recovered
            summary["cursor_recovered_from_target"] = recovered["last_event_id"]
        else:
            summary["cursor_recovered_from_target"] = None
    start = _start_line(lines, cursor)
    if cursor.get("last_event_id") is not None and start == 0:
        # The recorded id is not in this ledger at all (rotation, or a target from
        # a different ledger). Re-bridging from zero would duplicate, so refuse and
        # let a human decide -- an append-only stream has no undo.
        summary["errors"].append(
            f"bridge cursor names event {cursor['last_event_id']!r}, which is not in "
            f"{ledger_path}; refusing to re-bridge from zero (would duplicate rows in "
            f"{target_path}). Resolve by hand: confirm the target/ledger pairing.")
        return summary
    summary["skipped"] = start

    last_event_id = cursor.get("last_event_id")
    last_line = start
    for index in range(start, len(lines)):
        line_number = index + 1
        try:
            hearth_event = json.loads(lines[index])
            # Filtered rows advance the cursor below without being appended: an
            # excluded row is a decision, not a failure, so it must not be
            # reconsidered on the next run.
            if filter_heartbeat and is_heartbeat(hearth_event, caller_ids, profiles):
                summary["filtered"] += 1
            else:
                observation = build_capacity_observation(hearth_event)
                if observation is not None and (
                    _instant_key(observation["timestamp"]),
                    observation["model_id"],
                    observation["backend"],
                ) in already_covered:
                    # The dispatch-time producer already recorded this call, with
                    # richer detail. Bridge the event, drop the duplicate evidence.
                    observation = None
                    summary["observations_deduped"] += 1

                workflow_event = map_event(hearth_event)
                if observation is None:
                    workflow_event["artifact_refs"] = []
                if dry_run:
                    validate_event(workflow_event)
                else:
                    if observation is not None:
                        # The ref is only evidence if the file it names exists --
                        # extract_observations counts an unresolvable ref as
                        # `unresolved`, not as an observation. Write it first.
                        artifact_path = run_dir / "artifacts" / Path(
                            observation_relative_path(observation).split("artifacts/", 1)[1])
                        artifact_path.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_json(artifact_path, observation)
                        summary["observations"] += 1
                    append_event(target_path, workflow_event)
                summary["processed"] += 1
        except (json.JSONDecodeError, ValidationError) as exc:
            summary["errors"].append(f"{ledger_path}:{line_number}: {exc}")
            continue
        # A malformed row keeps the cursor where it is (the `continue` above), so a
        # later fix can re-attempt it; a mapped or filtered row moves it forward.
        last_event_id = hearth_event["event_id"]
        last_line = line_number

    if not dry_run and last_event_id is not None and last_line > start:
        save_cursor(cursor_path, last_event_id, last_line)
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.ledger_adapter",
        description="Project HEARTH ledger events into the workflow event store.",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    parser.add_argument("--dry-run", action="store_true", help="validate mapping, write nothing")
    parser.add_argument("--no-filter-heartbeat", action="store_true",
                        help="Bridge self-monitoring rows too (watchdog/drain callers and "
                             "probe-profile calls), which the default excludes")
    args = parser.parse_args(argv[1:])

    summary = project_ledger(args.ledger, args.target, args.cursor, dry_run=args.dry_run,
                             filter_heartbeat=not args.no_filter_heartbeat)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
