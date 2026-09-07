"""Dispatch-time corpus records for the idle drain — the write-back half of a tick.

B-03 made the drain *choose* a brief. This module is what makes the choice
leave a record the belief layer can read, so a dispatched candidate stops being
eligible instead of being re-dispatched forever.

Three artifacts/records, in the order a tick writes them:

  1. **the experiment plan** (``experiment-plan.v1``) — the declared intent,
     written BEFORE the dispatch. It is the thing ``project_experiments``
     joins to build an ``experiment-result.v1`` row, and the row's
     ``experiment_id`` is what ``sources.already_run_ids`` skips on. Its shape
     mirrors ``tools.workflow.reference_runner.build_experiment_plan`` (same
     contract) but none of its gate-specific logic is reused: that function
     answers "which policy gate did this dispatch open", and an idle-drain
     dispatch opens none.
  2. **the dispatch event** (``work.accepted``) — carries the plan as an
     ``artifact_refs`` entry, plus the decision/run ids that tie the eventual
     observation back to the plan.
  3. **the observation** (``retrospective.created``, plus a
     ``capacity-observation.v1`` artifact when a builder is actually named) —
     the terminal record of what the run did.

**Why those two event types.** ``contracts/workflow-event.schema.json`` closes
``event_type`` to a 21-value enum with no dispatch/observation family for an
unattended lane, and ``contracts/`` is not ours to change. ``work.accepted`` is
the intake of a brief as fleet work; ``retrospective.created`` is the corpus's
own terminal marker (``ontology.TERMINAL_EVENTS``) and the observation IS this
run's terminal record. Neither pollutes a projection: ``project_learning``
opens a dispatch window on ``builder.assigned``, which this lane deliberately
never emits (ADR-0008 — the conductor is the sole scheduler; the drain hands
over a brief and never assigns a builder). What carries the meaning here is the
artifact refs, not the verb.

**The ``.pending`` rule (why a plan is written twice).** The plan artifact is
written before the dispatch — intent on the record before the outcome exists —
but under ``<stem>.json.pending``, the path the dispatch event does NOT
reference. It is *published* (one ``os.replace`` onto ``<stem>.json``) only
after ``submit_task`` reports the brief actually reached the conductor's inbox.
That is what keeps a crashed, never-submitted attempt from suppressing its own
candidate: an unpublished plan leaves the event's artifact ref unresolved, the
projection counts it in ``unresolved_refs`` and synthesizes NO result row, so
the candidate stays eligible. A published plan always resolves, so a real
dispatch always produces a row — even before any observation lands (the row
then reads ``outcome: no_observation``).

Deterministic by construction: every id here is derived from the brief and the
dispatch id, so a crashed tick's successor recomputes the same paths and the
same ``event_id``s, which is what makes the replay idempotent.

Stdlib + ``hearth.backlog.briefs`` + two imports this module deliberately makes
rather than re-implements: ``task_lane._new_plan_id`` (one definition of what a
dispatch id looks like) and ``tools.workflow.append_event`` (which validates
every event against the corpus schema before it can land).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hearth.backlog.briefs import Brief, safe_slug  # noqa: E402
from hearth.toolsurface.task_lane import _new_plan_id  # noqa: E402
from tools.workflow.append_event import append_event  # noqa: E402

# --- corpus identity ---------------------------------------------------------

# One workflow_id for the whole lane: every drain dispatch belongs to the same
# workflow, and each dispatch gets its own run_id underneath it.
WORKFLOW_ID = "bankedfire-drain"
# runs/<RUN_NAMESPACE>/<dispatch_id>/ — the drain owns this subtree of the
# corpus and writes nowhere else in it.
RUN_NAMESPACE = "hearth-drain"
# actor.type is a closed enum in the event schema; the drain is a system actor
# (not a planner, and emphatically not a builder).
DRAIN_ACTOR = {"type": "system", "id": "bankedfire-drain"}

DISPATCH_EVENT_TYPE = "work.accepted"
OBSERVATION_EVENT_TYPE = "retrospective.created"

PLAN_CONTRACT = "experiment-plan.v1"
OBSERVATION_CONTRACT = "capacity-observation.v1"
PLAN_ARTIFACT_TYPE = "experiment_plan"
OBSERVATION_ARTIFACT_TYPE = "capacity_observation"

# The experiment_type enum from contracts/experiment-plan.v1.schema.json. Kept
# as a constant (no schema read at import time) and pinned to the contract by
# hearth/tests/backlog/test_dispatch.py, which reads the schema and compares.
# NOTE tools.workflow.project_experiments.EXPERIMENT_TYPES additionally lists
# "confidence_calibration", which the plan/result CONTRACTS do not — a candidate
# named with that prefix therefore falls back to DEFAULT_EXPERIMENT_TYPE rather
# than producing a plan its own schema rejects.
PLAN_EXPERIMENT_TYPES = frozenset({
    "known_bad_retest",
    "prediction_bias_calibration",
    "uncertain_resolution",
    "regression_probe",
    "prefer_validation",
    "backend_comparison",
    "qualification_run",
    "coverage_probe",
})
# What an authored/refined brief — and any candidate whose id does not name a
# contract experiment type — is recorded as. The enum has no "operator brief"
# member; coverage_probe is the honest nearest neighbour: the corpus has no
# observation of this work, and the dispatch exists to get one.
DEFAULT_EXPERIMENT_TYPE = "coverage_probe"

# Terminal outcomes an observation event can carry (the event schema's `outcome`
# is a free string; these are this lane's closed vocabulary).
OUTCOME_DISPATCHED = "dispatched"
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_NO_WINNER = "no_winner"
OUTCOME_DISPATCH_FAILED = "dispatch_failed"
OUTCOME_NEVER_SUBMITTED = "reconciled_never_submitted"
TERMINAL_OUTCOMES = frozenset({
    OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_NO_WINNER,
    OUTCOME_DISPATCH_FAILED, OUTCOME_NEVER_SUBMITTED,
})
# capacity-observation.v1's own closed outcome enum. The drain can only tell
# success from not-success, so a failure is recorded as the generic "error"
# rather than guessing at oom_crash/timeout/assay_failed.
OBSERVATION_OUTCOME_SUCCESS = "success"
OBSERVATION_OUTCOME_ERROR = "error"

PENDING_SUFFIX = ".pending"
OBSERVED_SUFFIX = ".observed"

IN_FLIGHT_CONTRACT = "bankedfire-drain-inflight.v1"


# --- small helpers -----------------------------------------------------------

def utc_now_iso(now: Optional[datetime] = None) -> str:
    """ISO-8601 UTC with a Z suffix. ``now`` is injectable so a test can assert
    exact bytes without a clock."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def write_text_atomic(path: Path, text: str,
                      replace_fn: Optional[Callable[[str, str], None]] = None) -> Path:
    """Temp file beside the target, then ``os.replace``.

    ``replace_fn`` is injectable (resolved at call time) so a fault-injection
    test can crash between the write and the swap and prove the previous file is
    byte-intact — the same idiom ``sources._atomic_write_text`` uses.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    fn = replace_fn or os.replace
    try:
        fn(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def write_json_atomic(path: Path, document: dict,
                      replace_fn: Optional[Callable[[str, str], None]] = None) -> Path:
    return write_text_atomic(path, json.dumps(document, indent=2) + "\n", replace_fn)


# --- identity derived from a brief -------------------------------------------

def new_dispatch_id(brief: Brief) -> str:
    """A fresh id for one dispatch attempt, from the SAME generator
    ``submit_task`` uses (``hearth-<slug>-<8 hex>``).

    This is NOT the conductor's plan_id and never can be: ``submit_task`` mints
    its own id inside the call, so nothing generated here can be handed to it.
    The drain's corpus run dir is keyed on this id (stable, known before the
    dispatch, so the slot can be persisted first) and the conductor's plan_id
    rides alongside in the in-flight record once it exists.
    """
    return _new_plan_id(brief.plan_id_hint())


def run_id_for(dispatch_id: str) -> str:
    return f"{RUN_NAMESPACE}/{dispatch_id}"


def decision_id_for(dispatch_id: str) -> str:
    return f"dec_{dispatch_id}"


def experiment_id_for(brief: Brief) -> str:
    """The id the belief layer will know this dispatch by.

    * ``candidate`` -> the ``candidate_id`` VERBATIM. This is the whole point:
      ``sources.already_run_ids`` skips a candidate whose id shows up as an
      ``experiment_id`` on an ``experiment-result.v1`` row, so the id must match
      byte for byte or the loop does not close.
    * ``authored``  -> ``authored:<slug>``
    * ``refined``   -> ``refined:<intent_id>``

    The two namespaced forms can never collide with a candidate_id: candidate
    ids are ``<experiment_type>:<combo>`` and neither "authored" nor "refined"
    is an experiment type.
    """
    if brief.source == "candidate":
        return brief.source_ref
    if brief.source == "refined":
        return f"refined:{brief.source_ref}"
    return f"authored:{brief.slug}"


def experiment_type_for(brief: Brief) -> str:
    """The contract experiment_type for this brief.

    A candidate id names its own type as the segment before the first ``:``
    (``project_experiments._candidate`` builds them that way). Anything the
    plan contract does not know — including an unprefixed hand-written
    candidate_id and every authored/refined brief — becomes
    ``DEFAULT_EXPERIMENT_TYPE``, never an invented enum member.
    """
    if brief.source == "candidate":
        head = brief.source_ref.split(":", 1)[0]
        if head in PLAN_EXPERIMENT_TYPES:
            return head
    return DEFAULT_EXPERIMENT_TYPE


def artifact_stem(experiment_id: str) -> str:
    """The filename an experiment_id is stored under.

    Never the raw id: candidate ids carry ``:`` and ``+`` (e.g.
    ``backend_comparison:qwen2.5:14b:ollama-cuda+...``) and ``:`` is not a legal
    NTFS filename character — it opens an alternate data stream. ``safe_slug``
    is the same validated charset ``Brief.slug`` uses. Uniqueness is not at risk
    from the truncation: exactly one experiment lives in one run dir.
    """
    return safe_slug(experiment_id)


# --- corpus paths ------------------------------------------------------------

def run_dir(corpus_root, dispatch_id: str) -> Path:
    return Path(corpus_root) / "runs" / RUN_NAMESPACE / dispatch_id


def events_path(corpus_root, dispatch_id: str) -> Path:
    return run_dir(corpus_root, dispatch_id) / "events.jsonl"


def plan_artifact_path(corpus_root, dispatch_id: str, experiment_id: str) -> Path:
    return (run_dir(corpus_root, dispatch_id) / "artifacts" / "experiments"
            / f"{artifact_stem(experiment_id)}.json")


def pending_plan_artifact_path(corpus_root, dispatch_id: str, experiment_id: str) -> Path:
    path = plan_artifact_path(corpus_root, dispatch_id, experiment_id)
    return path.with_name(path.name + PENDING_SUFFIX)


def observation_artifact_path(corpus_root, dispatch_id: str, experiment_id: str) -> Path:
    return (run_dir(corpus_root, dispatch_id) / "artifacts" / "observations"
            / f"{artifact_stem(experiment_id)}.json")


def observed_marker_path(corpus_root, dispatch_id: str, experiment_id: str) -> Path:
    """The guard that makes the completion write-back exactly-once.

    It is the fast path only — ``event_already_recorded`` is the truth, because
    a crash between the event append and this marker must not append twice.
    """
    return (run_dir(corpus_root, dispatch_id) / "artifacts" / "experiments"
            / f"{artifact_stem(experiment_id)}{OBSERVED_SUFFIX}")


def corpus_ref(corpus_root, path: Path) -> str:
    """The repo-relative, forward-slashed path an ``artifact_refs`` entry uses.

    ``project_capacity._resolve_artifact_path`` re-resolves everything after the
    first ``artifacts/`` segment against the run dir, so the prefix is
    provenance for humans; the tail is what has to be right.
    """
    return Path(path).relative_to(Path(corpus_root)).as_posix()


# --- the plan ----------------------------------------------------------------

def build_drain_experiment_plan(brief: Brief, dispatch_id: str, *,
                                timestamp: str,
                                scope: Optional[str] = None,
                                backend: Optional[str] = None) -> dict:
    """An ``experiment-plan.v1`` for one idle-drain dispatch.

    ``subject`` deliberately leaves builder_id/model_id/backend NULL. The drain
    gates on ``omen-arc`` occupancy, but the brief runs on a conductor builder
    the drain does not choose and cannot name at dispatch time — stamping the
    gating rung here would file evidence against a backend that never served the
    work. ``task_kind`` is the brief's own task_class, which IS known.
    ``gate_opened`` is null: an idle-drain dispatch opens no policy gate, it
    spends an idle slot.
    """
    experiment_id = experiment_id_for(brief)
    if brief.source == "candidate":
        reason = (f"idle-drain dispatch of priced experiment candidate "
                  f"{brief.source_ref!r}: the corpus proposed it and "
                  f"knowledge/candidate_worth.json priced it, so sunk idle compute "
                  f"buys the evidence instead of the candidate waiting for a human")
    elif brief.source == "refined":
        reason = (f"idle-drain dispatch of promoted refine intent "
                  f"{brief.source_ref!r}: a human promoted the refined result, "
                  f"which is the decision to spend the fleet on it")
    else:
        reason = (f"idle-drain dispatch of authored brief {brief.source_ref!r}: "
                  f"a human queued it for unattended fleet work")
    return {
        "contract_version": PLAN_CONTRACT,
        "experiment_id": experiment_id,
        "experiment_type": experiment_type_for(brief),
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id_for(dispatch_id),
        "decision_id": decision_id_for(dispatch_id),
        "timestamp": timestamp,
        "subject": {
            "builder_id": None,
            "model_id": None,
            "backend": None,
            "task_kind": brief.task_class,
            "metric": None,
        },
        "target_finding_id": None,
        "derived_from_candidate": (brief.source_ref if brief.source == "candidate"
                                   else None),
        "gate_opened": None,
        "reason": reason,
        "evidence_sought": (
            "one completed fleet run with a recorded outcome; the projection "
            f"joins this plan into an experiment-result.v1 row keyed "
            f"{experiment_id!r}, which is what makes the item ineligible for "
            "re-dispatch instead of it queue-serving forever"),
        "risk_accepted": (
            f"one unattended dispatch slot on the idle fleet, gated on "
            f"{backend or 'the drain backend'} occupancy"
            + (f" under arm scope {scope!r}" if scope else "")
            + "; the drain holds exactly one in-flight run and the arm file stops it"),
    }


# --- events ------------------------------------------------------------------

def dispatch_event_id(dispatch_id: str) -> str:
    return f"{dispatch_id}.dispatch"


def observation_event_id(dispatch_id: str) -> str:
    return f"{dispatch_id}.observation"


def build_dispatch_event(brief: Brief, dispatch_id: str, *, timestamp: str,
                         plan_ref: str, est_tokens: Optional[int] = None,
                         scope: Optional[str] = None,
                         backend: Optional[str] = None) -> dict:
    """The ``work.accepted`` event that puts the dispatch on the corpus.

    It carries the plan as an artifact ref (that ref is the ONLY way
    ``project_experiments`` finds the plan) plus the decision/run ids the later
    observation reuses, so plan -> decision -> observation joins without a
    lookup table.
    """
    experiment_id = experiment_id_for(brief)
    return {
        "event_id": dispatch_event_id(dispatch_id),
        "event_type": DISPATCH_EVENT_TYPE,
        "timestamp": timestamp,
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id_for(dispatch_id),
        "actor": dict(DRAIN_ACTOR),
        "status": "dispatched",
        "outcome": OUTCOME_DISPATCHED,
        "decision_id": decision_id_for(dispatch_id),
        "decision_reason": (
            f"idle-drain accepted the {brief.source} brief {brief.source_ref!r} "
            f"as unattended fleet work"),
        "artifact_refs": [{
            "artifact_id": experiment_id,
            "artifact_type": PLAN_ARTIFACT_TYPE,
            "path": plan_ref,
        }],
        "payload": {
            "dispatch_id": dispatch_id,
            "experiment_id": experiment_id,
            "source": brief.source,
            "source_ref": brief.source_ref,
            "slug": brief.slug,
            "task_class": brief.task_class,
            "est_tokens": est_tokens,
            "plan_id_hint": brief.plan_id_hint(),
            "arm_scope": scope,
            "gating_backend": backend,
        },
    }


def build_capacity_observation(dispatch_id: str, experiment_id: str, *,
                               timestamp: str, builder_id: str,
                               succeeded: bool,
                               task_kind: Optional[str] = None,
                               est_tokens: Optional[int] = None) -> dict:
    """A ``capacity-observation.v1`` for a finished drain run.

    Only ever built when a builder is actually named (``winner``): the contract
    requires a non-empty ``builder_id``, and filling it with "unknown" would
    file real evidence against a combo nobody ran. A run with no winner gets the
    observation EVENT (the outcome is on the record) and no capacity artifact —
    the experiment result then reads ``no_observation``, which is true.
    """
    if not isinstance(builder_id, str) or not builder_id.strip():
        raise ValueError("capacity observation needs a non-empty builder_id")
    return {
        "contract_version": OBSERVATION_CONTRACT,
        "observation_id": observation_event_id(dispatch_id),
        "decision_id": decision_id_for(dispatch_id),
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id_for(dispatch_id),
        "timestamp": timestamp,
        "builder_id": builder_id,
        "model_id": None,
        "backend": None,
        "hardware_profile_id": None,
        "workload_shape": {
            "task_kind": task_kind or "unknown",
            "estimated_context_tokens": est_tokens,
            "requires_gpu": None,
            "notes": f"idle-drain dispatch, experiment {experiment_id}",
        },
        "observed": None,
        "outcome": (OBSERVATION_OUTCOME_SUCCESS if succeeded
                    else OBSERVATION_OUTCOME_ERROR),
        "failure_class": None,
        "promotion_status": None,
    }


def build_observation_event(dispatch_id: str, experiment_id: str, *,
                            timestamp: str, outcome: str,
                            payload: Optional[dict] = None,
                            observation_ref: Optional[str] = None) -> dict:
    """The terminal ``retrospective.created`` event for one dispatch.

    One per run dir, guarded by ``observed_marker_path`` and — the real
    guarantee — by ``event_already_recorded`` on this deterministic event_id.
    """
    if outcome not in TERMINAL_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(TERMINAL_OUTCOMES)}; "
                         f"got {outcome!r}")
    event = {
        "event_id": observation_event_id(dispatch_id),
        "event_type": OBSERVATION_EVENT_TYPE,
        "retrospective_id": observation_event_id(dispatch_id),
        "timestamp": timestamp,
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id_for(dispatch_id),
        "actor": dict(DRAIN_ACTOR),
        "status": "observed",
        "outcome": outcome,
        "decision_id": decision_id_for(dispatch_id),
        "payload": {"experiment_id": experiment_id, **(payload or {})},
    }
    if observation_ref:
        event["artifact_refs"] = [{
            "artifact_id": observation_event_id(dispatch_id),
            "artifact_type": OBSERVATION_ARTIFACT_TYPE,
            "path": observation_ref,
        }]
    return event


def append_corpus_event(path: Path, event: dict) -> None:
    """Append one event, validated first. ``append_event`` raises before writing
    if the event does not satisfy the corpus schema, so a bad event never
    lands."""
    append_event(Path(path), event)


def event_already_recorded(path: Path, event_id: str) -> bool:
    """Is ``event_id`` already in this run's events.jsonl?

    The exactly-once truth. A missing/unreadable file answers False (there is
    nothing recorded); a malformed line is skipped rather than raising, because
    a torn line must not stop the drain from finishing its write-back.
    """
    path = Path(path)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_id") == event_id:
            return True
    return False


# --- the plan's two-phase write ----------------------------------------------

def write_pending_plan(corpus_root, dispatch_id: str, plan: dict,
                       replace_fn: Optional[Callable[[str, str], None]] = None) -> Path:
    """Write the plan to ``<stem>.json.pending`` — on disk before the dispatch,
    but not yet at the path the dispatch event references."""
    return write_json_atomic(
        pending_plan_artifact_path(corpus_root, dispatch_id, plan["experiment_id"]),
        plan, replace_fn)


def publish_plan(corpus_root, dispatch_id: str, experiment_id: str,
                 replace_fn: Optional[Callable[[str, str], None]] = None) -> bool:
    """Move ``<stem>.json.pending`` onto ``<stem>.json``. Idempotent.

    Returns True when a pending plan was published by this call, False when
    there was nothing pending (already published, or never written). Publishing
    is what makes the dispatch event's artifact ref resolve — and therefore what
    makes the projection emit a result row for this experiment_id.
    """
    pending = pending_plan_artifact_path(corpus_root, dispatch_id, experiment_id)
    final = plan_artifact_path(corpus_root, dispatch_id, experiment_id)
    if not pending.is_file():
        return False
    final.parent.mkdir(parents=True, exist_ok=True)
    (replace_fn or os.replace)(str(pending), str(final))
    return True


# --- the in-flight record ----------------------------------------------------

def new_in_flight(brief: Brief, dispatch_id: str, *, dispatched_at: str,
                  plan_artifact: str, dispatch_event_id: str,
                  corpus_root, task_class: Optional[str] = None,
                  est_tokens: Optional[int] = None) -> dict:
    """The slot record as written BEFORE the submit (phase "prepared").

    ``submit_attempted`` / ``plan_id`` are the two bits the next tick reconciles
    on; see ``in_flight_phase``.
    """
    return {
        "contract_version": IN_FLIGHT_CONTRACT,
        "dispatch_id": dispatch_id,
        "run_id": run_id_for(dispatch_id),
        "experiment_id": experiment_id_for(brief),
        "decision_id": decision_id_for(dispatch_id),
        "source": brief.source,
        "source_ref": brief.source_ref,
        "slug": brief.slug,
        "task_class": task_class if task_class is not None else brief.task_class,
        "est_tokens": est_tokens,
        "dispatched_at": dispatched_at,
        "plan_artifact": plan_artifact,
        "dispatch_event_id": dispatch_event_id,
        "corpus_root": str(corpus_root),
        "submit_attempted": False,
        "submitted": False,
        "plan_id": None,
        "inbox_path": None,
        "result_path": None,
        "submitted_at": None,
    }


PHASE_PREPARED = "prepared"
PHASE_ATTEMPTED = "attempted"
PHASE_SUBMITTED = "submitted"


def in_flight_phase(record: Optional[dict]) -> Optional[str]:
    """Which of the three persisted phases a slot is in — the reconcile rule.

    * ``prepared``  — the slot exists, ``submit_attempted`` is False. The
      submit call had NOT been entered when the process died, so nothing can be
      in the conductor's inbox: safe to record a failure and free the slot.
    * ``attempted`` — the submit call was entered but never returned a plan_id
      into the slot. Whether the inbox write landed is genuinely UNKNOWN, so
      this fails closed: the slot stays held and no second dispatch happens.
    * ``submitted`` — ``plan_id`` (and normally ``inbox_path``) are recorded;
      the conductor owns a run and ``task_status`` can be asked about it.

    A record with no ``submit_attempted`` key (a slot written by an older
    build, or a hand-edited file) is read as ``submitted`` when it names a
    plan_id and ``prepared`` otherwise — never as ``attempted``, because
    inventing an unknown would wedge a lane that is not actually ambiguous.
    """
    if not isinstance(record, dict):
        return None
    if record.get("plan_id"):
        return PHASE_SUBMITTED
    if record.get("submit_attempted"):
        return PHASE_ATTEMPTED
    return PHASE_PREPARED


def read_result(status: Optional[dict]) -> tuple[Optional[bool], Optional[str], Optional[str]]:
    """(result_ok, winner, result_path) from a ``task_status`` reply.

    Handles both shapes the tool returns: the full ``{"result": {...}}`` reply
    and the ``out_file`` ACK, which lifts ``result_ok``/``winner`` to the top
    level. A winner that is not a non-empty string is reported as absent rather
    than coerced.
    """
    if not isinstance(status, dict):
        return None, None, None
    result = status.get("result")
    if isinstance(result, dict):
        result_ok = result.get("ok")
        winner = result.get("winner")
    else:
        result_ok = status.get("result_ok")
        winner = status.get("winner")
    if not isinstance(winner, str) or not winner.strip():
        winner = None
    if not isinstance(result_ok, bool):
        result_ok = None
    result_path = status.get("result_path")
    if not isinstance(result_path, str) or not result_path.strip():
        result_path = None
    return result_ok, winner, result_path


def completion_outcome(result_ok: Optional[bool], winner: Optional[str]) -> str:
    """winner present and ok -> succeeded; winner present and not ok -> failed;
    no winner -> no_winner. An absent ``ok`` with a winner is NOT read as
    success — the run named a builder but never said it worked."""
    if winner is None:
        return OUTCOME_NO_WINNER
    return OUTCOME_SUCCEEDED if result_ok is True else OUTCOME_FAILED
