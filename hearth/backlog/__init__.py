"""hearth.backlog — the brief is the unit of unattended work.

One contract (``briefs.Brief``), three pluggable sources (``sources``:
authored files, promoted refined intents, priced experiment candidates), and
one deterministic chooser (``select.select_next``, authored > refined >
candidate). ``fleet.bankedfire_drain`` is the first consumer; the CLI
(``python -m hearth.backlog``) is the read-only window onto the same data —
``list`` shows what a tick WOULD choose and dispatches nothing.

Stdlib plus the two functions this package deliberately imports rather than
re-implements: ``task_lane._ccmeta_header`` (the header format) and
``assay_acceptance._CCMETA_RE`` (the way to read it back). No
``hearth.kernel`` import — nothing here ledgers, leases, or dispatches.

Nothing in this package creates ``hearth/var``. The ``DEFAULT_*`` constants in
``sources`` name those paths; scanning an absent backlog returns an empty scan.
"""
from hearth.backlog.briefs import SOURCES, Brief, parse, safe_slug
from hearth.backlog.dispatch import (
    DEFAULT_EXPERIMENT_TYPE,
    IN_FLIGHT_CONTRACT,
    PHASE_ATTEMPTED,
    PHASE_PREPARED,
    PHASE_SUBMITTED,
    PLAN_EXPERIMENT_TYPES,
    RUN_NAMESPACE,
    WORKFLOW_ID,
    append_corpus_event,
    build_capacity_observation,
    build_dispatch_event,
    build_drain_experiment_plan,
    build_observation_event,
    completion_outcome,
    event_already_recorded,
    events_path,
    experiment_id_for,
    experiment_type_for,
    in_flight_phase,
    new_dispatch_id,
    new_in_flight,
    observed_marker_path,
    plan_artifact_path,
    publish_plan,
    read_result,
    run_id_for,
    write_pending_plan,
)
from hearth.backlog.select import PRIORITY, SCOPES, select_next
from hearth.backlog.sources import (
    CANDIDATE_TASK_CLASS,
    DEFAULT_BACKLOG_ROOT,
    DEFAULT_CANDIDATE_WORTH_PATH,
    DEFAULT_DISPATCHED_DIR,
    DEFAULT_DONE_DIR,
    DEFAULT_EXPERIMENT_RESULTS_PATH,
    DEFAULT_QUEUED_DIR,
    DEFAULT_REFINE_DIR,
    PROMOTE_CONTRACT,
    SourceScan,
    already_run_ids,
    authored_source,
    candidate_brief,
    candidate_prompt,
    candidate_source,
    mark_dispatched,
    mark_done,
    promote_refine,
    rank_candidates,
    refined_source,
    select_candidate,
    validate_promote_sidecar,
)

__all__ = [
    "Brief", "SOURCES", "parse", "safe_slug",
    "select_next", "PRIORITY", "SCOPES",
    "SourceScan", "authored_source", "refined_source", "candidate_source",
    "mark_dispatched", "mark_done", "promote_refine", "validate_promote_sidecar",
    "select_candidate", "rank_candidates", "already_run_ids",
    "candidate_brief", "candidate_prompt", "CANDIDATE_TASK_CLASS",
    "PROMOTE_CONTRACT",
    "DEFAULT_BACKLOG_ROOT", "DEFAULT_QUEUED_DIR", "DEFAULT_DISPATCHED_DIR",
    "DEFAULT_DONE_DIR", "DEFAULT_REFINE_DIR",
    "DEFAULT_CANDIDATE_WORTH_PATH", "DEFAULT_EXPERIMENT_RESULTS_PATH",
    # dispatch (B-04): the corpus records a dispatch leaves behind.
    "WORKFLOW_ID", "RUN_NAMESPACE", "IN_FLIGHT_CONTRACT",
    "PLAN_EXPERIMENT_TYPES", "DEFAULT_EXPERIMENT_TYPE",
    "PHASE_PREPARED", "PHASE_ATTEMPTED", "PHASE_SUBMITTED",
    "new_dispatch_id", "run_id_for", "experiment_id_for", "experiment_type_for",
    "build_drain_experiment_plan", "build_dispatch_event",
    "build_observation_event", "build_capacity_observation",
    "write_pending_plan", "publish_plan", "plan_artifact_path",
    "events_path", "observed_marker_path", "append_corpus_event",
    "event_already_recorded", "new_in_flight", "in_flight_phase",
    "read_result", "completion_outcome",
]
