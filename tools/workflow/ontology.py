from __future__ import annotations

EVENT_TYPES = {
    "work.accepted",
    "planning.started",
    "planning.completed",
    "backlog.entry_created",
    "builder.assigned",
    "builder.grooming_started",
    "builder.grooming_completed",
    "question.raised",
    "question.answered",
    "builder.resumed",
    "candidate.produced",
    "assay.started",
    "assay.passed",
    "assay.failed",
    "risk.scored",
    "promotion.held",
    "promotion.approved",
    "promotion.rejected",
    "retrospective.created",
    "idle.observed",
    "idle.ended",
}

EVENT_REQUIREMENTS = {
    "question.raised": {"question_id"},
    "question.answered": {"question_id"},
    "builder.assigned": {"lap_id"},
    "builder.grooming_started": {"builder_id"},
    "builder.grooming_completed": {"builder_id"},
    "builder.resumed": {"builder_id"},
    "candidate.produced": {"candidate_id", "builder_id"},
    "assay.started": {"assay_id"},
    "assay.passed": {"assay_id", "candidate_id"},
    "assay.failed": {"assay_id"},
    "risk.scored": {"risk_report_id"},
    "promotion.held": {"promotion_id"},
    "promotion.approved": {"promotion_id", "candidate_id"},
    "promotion.rejected": {"promotion_id"},
    "retrospective.created": {"retrospective_id"},
}

EVENT_TO_PHASE = {
    "work.accepted": "intake",
    "planning.started": "planning",
    "planning.completed": "planning",
    "backlog.entry_created": "routing",
    "builder.assigned": "dispatch",
    "builder.grooming_started": "builder",
    "builder.grooming_completed": "builder",
    "question.raised": "builder",
    "question.answered": "builder",
    "builder.resumed": "builder",
    "candidate.produced": "builder",
    "assay.started": "assay",
    "assay.passed": "assay",
    "assay.failed": "assay",
    "risk.scored": "risk",
    "promotion.held": "promotion",
    "promotion.approved": "promotion",
    "promotion.rejected": "promotion",
    "retrospective.created": "retrospective",
    "idle.observed": "idle",
    "idle.ended": "idle",
}

TERMINAL_EVENTS = {"promotion.approved", "promotion.rejected", "retrospective.created"}

DECISION_EVENT_TYPES = {
    "builder.assigned": "builder_assignment",
    "question.answered": "question_answer",
    "promotion.held": "promotion_hold",
    "promotion.approved": "promotion_approval",
    "promotion.rejected": "promotion_rejection",
}
