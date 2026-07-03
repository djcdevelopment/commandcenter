from __future__ import annotations

from tools.workflow.ontology import EVENT_TO_PHASE


def to_otel_span_event(event: dict) -> dict:
    attributes = {
        "semantic.event": event["event_type"],
        "semantic.phase": EVENT_TO_PHASE.get(event["event_type"]),
        "semantic.layer": "business",
        "workflow_id": event["workflow_id"],
        "run_id": event["run_id"],
        "status": event["status"],
        "operator.action_required": bool(event.get("operator_action_required", False)),
    }

    optional_fields = (
        "parent_run_id",
        "lap_id",
        "segment_id",
        "question_id",
        "candidate_id",
        "assay_id",
        "risk_report_id",
        "promotion_id",
        "retrospective_id",
        "decision_id",
        "decision_type",
        "decision_class",
        "decision_reason",
        "outcome",
    )
    for field_name in optional_fields:
        if event.get(field_name) is not None:
            attributes[field_name] = event[field_name]

    actor = event.get("actor") or {}
    if actor.get("type"):
        attributes["actor.type"] = actor["type"]
    if actor.get("id"):
        attributes["actor.id"] = actor["id"]
    if actor.get("model_id"):
        attributes["model_id"] = actor["model_id"]

    if event.get("artifact_refs"):
        attributes["artifact.refs"] = [ref["path"] for ref in event["artifact_refs"]]

    decision_maker = event.get("decision_maker") or {}
    if decision_maker.get("type"):
        attributes["decision.maker.type"] = decision_maker["type"]
    if decision_maker.get("id"):
        attributes["decision.maker.id"] = decision_maker["id"]

    return {
        "name": event["event_type"],
        "timestamp": event["timestamp"],
        "attributes": attributes,
    }
