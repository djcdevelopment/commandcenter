from __future__ import annotations

import json
from pathlib import Path

from tools.workflow.otel_adapter import to_otel_span_event
from tools.workflow.fsio import atomic_write_json


def project_board(state: dict) -> dict:
    return {
        "workflow_id": state["workflow_id"],
        "run_id": state["run_id"],
        "status": state["status"],
        "current_phase": state["current_phase"],
        "operator_action_required": state["operator_action_required"],
        "last_event_type": state["last_event_type"],
        "question_id": state["question_id"],
        "candidate_id": state["candidate_id"],
        "assay_id": state["assay_id"],
        "promotion_id": state["promotion_id"],
        "decision_count": len(state.get("decisions", [])),
        "active_builder_ids": sorted(state.get("builders", {}).keys()),
    }


def write_board_projection(path: Path, state: dict) -> None:
    atomic_write_json(path, project_board(state))


def write_otel_mirror(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(to_otel_span_event(event), separators=(",", ":")) + "\n")
