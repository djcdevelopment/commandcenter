"""HEARTH shadow scheduler (JS3) — advisory, read-only job-shop scheduling.

A pure CP-SAT job-shop scheduler that turns a snapshot of jobs and the fleet's
machine inventory into an ADVISORY schedule proposal. It never dispatches, never
touches SSH, never mutates the fleet — it only proposes. The objective encodes the
two-economies doctrine: local compute is ~free, frontier tokens are expensive, so
metered token spend is minimized first and makespan second.

Three pure modules:
  - ontology  — dataclasses (Job/Machine/ScheduleProposal) + loaders (machines,
                capacity durations) with declared fallbacks.
  - solve     — the CP-SAT model: intervals, no-overlap, precedence, deadlines,
                token-then-span objective.
  - decision  — proposal -> scheduler-decision.v1 record, schema-validated.
"""

from __future__ import annotations

from hearth.scheduler.ontology import (
    DEFAULT_DURATIONS_S,
    Job,
    Machine,
    ScheduleProposal,
    load_capacity,
    load_machines,
    lookup_duration_s,
)
from hearth.scheduler.solve import solve_schedule
from hearth.scheduler.decision import build_scheduler_decision, validate_decision

__all__ = [
    "DEFAULT_DURATIONS_S",
    "Job",
    "Machine",
    "ScheduleProposal",
    "load_capacity",
    "load_machines",
    "lookup_duration_s",
    "solve_schedule",
    "build_scheduler_decision",
    "validate_decision",
]
