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
    }


def load_cursor(cursor_path: Path) -> dict:
    if cursor_path.exists():
        return json.loads(cursor_path.read_text(encoding="utf-8"))
    return {"last_event_id": None, "line": 0}


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
    summary = {"processed": 0, "skipped": 0, "filtered": 0, "errors": []}
    if not ledger_path.exists():
        summary["errors"].append(f"ledger not found: {ledger_path}")
        return summary

    lines = [line for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cursor = load_cursor(cursor_path)
    start = _start_line(lines, cursor)
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
                workflow_event = map_event(hearth_event)
                if dry_run:
                    validate_event(workflow_event)
                else:
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
