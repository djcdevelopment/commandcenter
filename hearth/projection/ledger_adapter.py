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


def project_ledger(
    ledger_path: Path = DEFAULT_LEDGER,
    target_path: Path = DEFAULT_TARGET,
    cursor_path: Path = DEFAULT_CURSOR,
    dry_run: bool = False,
) -> dict:
    """Project new ledger events into the workflow store.

    Returns {"processed": int, "skipped": int, "errors": [str]}.
    """
    summary = {"processed": 0, "skipped": 0, "errors": []}
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
            workflow_event = map_event(hearth_event)
            if dry_run:
                validate_event(workflow_event)
            else:
                append_event(target_path, workflow_event)
        except (json.JSONDecodeError, ValidationError) as exc:
            summary["errors"].append(f"{ledger_path}:{line_number}: {exc}")
            continue
        summary["processed"] += 1
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
    args = parser.parse_args(argv[1:])

    summary = project_ledger(args.ledger, args.target, args.cursor, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
