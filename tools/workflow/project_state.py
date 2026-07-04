from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.workflow.ontology import EVENT_TO_PHASE
from tools.workflow.fsio import atomic_write_json


def _builder_id(event: dict) -> str | None:
    actor = event.get("actor") or {}
    if actor.get("type") == "builder":
        return actor.get("id")
    return None


def initial_state() -> dict:
    return {
        "workflow_id": None,
        "run_id": None,
        "status": "unknown",
        "current_phase": None,
        "operator_action_required": False,
        "last_event_type": None,
        "decisions": [],
        "question_id": None,
        "candidate_id": None,
        "assay_id": None,
        "risk_report_id": None,
        "promotion_id": None,
        "retrospective_id": None,
        "builders": {},
        "artifact_refs": [],
    }


def apply_event(state: dict, event: dict) -> dict:
    state = json.loads(json.dumps(state))
    state["workflow_id"] = event["workflow_id"]
    state["run_id"] = event["run_id"]
    state["last_event_type"] = event["event_type"]
    state["current_phase"] = EVENT_TO_PHASE.get(event["event_type"])
    state["operator_action_required"] = bool(event.get("operator_action_required", False))

    if event.get("artifact_refs"):
        state["artifact_refs"] = event["artifact_refs"]

    if event.get("decision_id"):
        state["decisions"].append(
            {
                "decision_id": event["decision_id"],
                "decision_type": event.get("decision_type"),
                "decision_class": event.get("decision_class"),
                "decision_maker": event.get("decision_maker"),
                "decision_reason": event.get("decision_reason"),
                "event_type": event["event_type"],
            }
        )

    for key in ("question_id", "candidate_id", "assay_id", "risk_report_id", "promotion_id", "retrospective_id"):
        if event.get(key):
            state[key] = event[key]

    builder_id = _builder_id(event)
    if builder_id:
        state["builders"].setdefault(builder_id, {})
        state["builders"][builder_id]["last_event_type"] = event["event_type"]
        if event.get("lap_id"):
            state["builders"][builder_id]["lap_id"] = event["lap_id"]

    event_type = event["event_type"]
    if event_type == "work.accepted":
        state["status"] = "accepted"
    elif event_type == "planning.started":
        state["status"] = "planning"
    elif event_type == "planning.completed":
        state["status"] = "planned"
    elif event_type == "backlog.entry_created":
        state["status"] = "queued"
    elif event_type == "builder.assigned":
        state["status"] = "building"
    elif event_type == "builder.grooming_started":
        state["status"] = "grooming"
    elif event_type == "builder.grooming_completed":
        state["status"] = "building"
    elif event_type == "question.raised":
        state["status"] = "waiting_on_operator"
    elif event_type == "question.answered":
        state["status"] = "answered"
        state["operator_action_required"] = False
    elif event_type == "builder.resumed":
        state["status"] = "building"
        state["operator_action_required"] = False
    elif event_type == "candidate.produced":
        state["status"] = "candidate_ready"
    elif event_type == "assay.started":
        state["status"] = "assaying"
    elif event_type == "assay.passed":
        state["status"] = "assay_passed"
    elif event_type == "assay.failed":
        state["status"] = "assay_failed"
    elif event_type == "risk.scored":
        state["status"] = "risk_scored"
    elif event_type == "promotion.held":
        state["status"] = "held"
    elif event_type == "promotion.approved":
        state["status"] = "approved"
        state["operator_action_required"] = False
    elif event_type == "promotion.rejected":
        state["status"] = "rejected"
        state["operator_action_required"] = False
    elif event_type == "retrospective.created":
        if state["status"] not in {"approved", "rejected"}:
            state["status"] = "retrospective_created"

    return state


def project_events(events: list[dict]) -> dict:
    state = initial_state()
    for event in events:
        state = apply_event(state, event)
    return state


def read_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            events.append(json.loads(raw_line))
    return events


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("usage: python -m tools.workflow.project_state <events.jsonl> [state.json]")
        return 2

    events = read_events(Path(argv[1]))
    state = project_events(events)
    if len(argv) == 3:
        atomic_write_json(Path(argv[2]), state)
    else:
        print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
