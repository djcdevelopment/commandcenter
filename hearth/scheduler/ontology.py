"""Scheduler data model: jobs, machines, model specs, and the proposal result."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelSpec:
    """Specification for a model's resource and performance profile."""
    model_id: str
    placement: str  # "single", "distributed", etc.
    per_card_gb: float  # VRAM per GPU card
    warmup_ms_p50: float  # Load time p50 in milliseconds

    def setup_s(self) -> float:
        """Model load/setup time in seconds."""
        return self.warmup_ms_p50 / 1000.0


@dataclass
class Job:
    """A scheduled job/task."""
    plan_id: str
    task_class: str
    deadline_s: float | None
    required_model: str
    est_tokens: int


@dataclass
class Machine:
    """A compute node available for scheduling."""
    name: str
    kind: str  # "local", "cloud", etc.
    token_cost_weight: float
    tags: list[str]
    available: bool
    stateful: bool
    cards: list[dict[str, Any]]  # GPU cards: [{"index": 0, "vram_gb": 32.0}, ...]
    resident_models: list[str]
    staging_slots: int
    host: str


@dataclass
class ScheduleProposal:
    """Result of a scheduler solve."""
    solver_status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", etc.
    makespan_s: float
    assignments: list[dict[str, Any]] = field(default_factory=list)
    loads: list[dict[str, Any]] = field(default_factory=list)
