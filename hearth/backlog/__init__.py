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
]
