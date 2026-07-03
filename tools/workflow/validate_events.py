from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.workflow.ontology import DECISION_EVENT_TYPES, EVENT_REQUIREMENTS, EVENT_TYPES


class ValidationError(Exception):
    pass


def _builder_id(event: dict) -> str | None:
    actor = event.get("actor") or {}
    if actor.get("type") == "builder":
        return actor.get("id")
    return event.get("builder_id")


def validate_event(event: dict) -> None:
    required = {"event_id", "event_type", "timestamp", "workflow_id", "run_id", "actor", "status", "payload"}
    missing = sorted(name for name in required if name not in event)
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")

    event_type = event["event_type"]
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unknown event_type: {event_type}")

    actor = event["actor"]
    if not isinstance(actor, dict) or not actor.get("type") or not actor.get("id"):
        raise ValidationError("actor must contain type and id")

    for field_name in EVENT_REQUIREMENTS.get(event_type, set()):
        if field_name == "builder_id":
            if not _builder_id(event):
                raise ValidationError(f"{event_type} requires builder actor identity")
            continue
        if not event.get(field_name):
            raise ValidationError(f"{event_type} requires {field_name}")

    expected_decision_class = DECISION_EVENT_TYPES.get(event_type)
    if expected_decision_class:
        if not event.get("decision_id"):
            raise ValidationError(f"{event_type} requires decision_id")
        if event.get("decision_class") != expected_decision_class:
            raise ValidationError(f"{event_type} requires decision_class={expected_decision_class}")
        decision_maker = event.get("decision_maker")
        if not isinstance(decision_maker, dict) or not decision_maker.get("type") or not decision_maker.get("id"):
            raise ValidationError(f"{event_type} requires decision_maker with type and id")


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
            validate_event(event)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(f"{path}:{line_number}: {exc}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m tools.workflow.validate_events <events.jsonl> [more files]")
        return 2

    all_errors: list[str] = []
    for raw_path in argv[1:]:
        all_errors.extend(validate_file(Path(raw_path)))

    if all_errors:
        for error in all_errors:
            print(error)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
