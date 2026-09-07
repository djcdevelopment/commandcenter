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
     ``capacity-observation.v1`` artifact when — and only when — the run's
     winner IS the combo the candidate names) — the terminal record of what the
     run did.

**The combo rule (B-04-R1).** A capacity observation is evidence about one
``builder_id|model_id|backend`` combo. The drain does not choose the builder
(ADR-0008) and cannot name the model or the backend a conductor run used, so
the only combo it may honestly write is the one the *candidate itself* names:
a priced experiment candidate exists to test a specific combo, and
``project_experiments._combo`` encodes that combo in the ``candidate_id``.
``combo_from_candidate_id`` decodes it back. Everything else — an authored or
refined brief, a candidate whose id names no combo, a candidate whose combo
carries the literal ``"unknown"`` that ``_combo`` writes for a missing field —
yields NO combo, and therefore no capacity artifact. A winner that is not the
combo's builder yields no artifact either: the run happened on someone else's
machine, so it is not evidence about this combo. In both cases the observation
EVENT still records the outcome and gains
``payload.capacity_artifact_skipped``, and the experiment-result row then reads
``no_observation`` — which is the truth.

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
from typing import Callable, NamedTuple, Optional

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

# --- which candidate ids name a combo, and how ---------------------------------
#
# SOURCE OF TRUTH: tools/workflow/project_experiments.py::_combo --
#     "|".join(subject.get(f) or "unknown" for f in ("builder_id","model_id","backend"))
# and the candidate_id literals that embed it. Four experiment types put the
# combo straight after the type:
#     f"known_bad_retest:{_combo(subject)}"       (L244)
#     f"uncertain_resolution:{_combo(subject)}"   (L267)
#     f"regression_probe:{_combo(subject)}"       (L276)
#     f"prefer_validation:{_combo(subject)}"      (L290)
COMBO_EXPERIMENT_TYPES = frozenset({
    "known_bad_retest",
    "uncertain_resolution",
    "regression_probe",
    "prefer_validation",
})
# A coverage_probe candidate is "coverage_probe:" + the GAP id
# (project_experiments L169), and only two gap ids are combo-shaped:
#     f"unobserved_combo:{'|'.join(combo)}"    (project_coverage L74)
#     f"unmeasured_metrics:{'|'.join(combo)}"  (project_coverage L134)
# where `combo` is _combo_of(record) -> (builder_id, model_id, backend), same
# order, same "unknown" filler. The third shape, `single_workflow_evidence`,
# ALSO contains "|" -- it joins "name=value" invariant pairs -- so gating on the
# gap type is what keeps an invariant list from being misread as a combo.
COMBO_COVERAGE_GAP_TYPES = frozenset({"unobserved_combo", "unmeasured_metrics"})
# The literal `_combo` writes for a field the corpus does not know. It is a
# placeholder, never an identity: a candidate id carrying it names no combo.
# Real examples in knowledge/experiment_candidates.json today:
#     prefer_validation:claude-frontier|gemini-3.5-flash|unknown
UNKNOWN_COMBO_SEGMENT = "unknown"

# Why a capacity artifact was NOT written, recorded on the observation event.
CAPACITY_SKIP_FIELD = "capacity_artifact_skipped"
CAPACITY_SKIP_NO_COMBO = "no-combo"
CAPACITY_SKIP_WINNER_MISMATCH = "winner-mismatch"

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


class Combo(NamedTuple):
    """The ``builder_id|model_id|backend`` triple a candidate is about.

    Every field is a non-empty string that is not the ``"unknown"`` placeholder
    — ``combo_from_candidate_id`` returns None rather than build a Combo with a
    hole in it, which is what makes ``Combo`` safe to write into a
    ``capacity-observation.v1``.
    """
    builder_id: str
    model_id: str
    backend: str


def combo_from_candidate_id(candidate_id) -> Optional[Combo]:
    """Decode the combo a candidate id names, or None if it names none.

    THE DERIVATION RULE. ``Brief`` carries no structural combo field (it is
    ``slug/title/body/builders/task_class/est_tokens/requires/max_age_s/source/
    source_ref`` and nothing else) and ``sources.candidate_brief`` copies only
    ``candidate_id``/``worth_points``/``reason`` off the worth entry, so the id
    IS the carrier. Splitting it:

    * on the FIRST ``:`` only — a model_id may itself contain ``:``
      (``prefer_validation:omen-5070|qwen2.5:14b|ollama-cuda``) or ``/``
      (a .gguf path), so a last-colon or a naive 3-way split is wrong;
    * a second time for ``coverage_probe``, whose id embeds a gap id;
    * then on ``|`` into EXACTLY three parts.

    A part that is empty or the literal ``"unknown"`` voids the whole combo:
    ``"unknown"`` is what ``project_experiments._combo`` writes for a field the
    corpus does not know, and it is precisely the value that must never reach
    ``project_capacity._combo_key``'s bucket of the same name.

    Every other id shape yields None by construction, and deliberately:
    ``prediction_bias_calibration:<model>:<metric>``,
    ``backend_comparison:<model>:<b1>+<b2>``,
    ``qualification_run:<capability_id>``,
    ``coverage_probe:single_workflow_evidence:...`` and
    ``confidence_calibration:corpus`` name no builder+model+backend triple, so
    there is nothing to file evidence against.
    """
    if not isinstance(candidate_id, str) or ":" not in candidate_id:
        return None
    head, rest = candidate_id.split(":", 1)
    if head == "coverage_probe":
        if ":" not in rest:
            return None
        gap_type, rest = rest.split(":", 1)
        if gap_type not in COMBO_COVERAGE_GAP_TYPES:
            return None
    elif head not in COMBO_EXPERIMENT_TYPES:
        return None
    parts = rest.split("|")
    if len(parts) != 3:
        return None
    if any(not part.strip() or part == UNKNOWN_COMBO_SEGMENT for part in parts):
        return None
    return Combo(*parts)


def combo_for_source(source, source_ref) -> Optional[Combo]:
    """The combo a (source, source_ref) pair names. Only candidates have one.

    Authored and refined briefs are human work items, not combo experiments:
    ``authored:<slug>`` and ``refined:<intent_id>`` name a task, never a
    builder+model+backend.
    """
    if source != "candidate":
        return None
    return combo_from_candidate_id(source_ref)


def combo_for(brief: Brief) -> Optional[Combo]:
    return combo_for_source(brief.source, brief.source_ref)


def combo_from_in_flight(record: Optional[dict]) -> Optional[Combo]:
    """The combo behind a persisted slot, recomputed rather than stored.

    The in-flight record already carries ``source`` and ``source_ref``, and
    ``combo_from_candidate_id`` is pure, so the combo is fully recoverable at
    write-back time. That is why B-04-R1 adds NO key to the record and leaves
    the contract at ``bankedfire-drain-inflight.v1``: a stored copy could
    disagree with the id it was derived from, and a slot written by an older
    build would carry no copy at all.
    """
    if not isinstance(record, dict):
        return None
    return combo_for_source(record.get("source"), record.get("source_ref"))


def capacity_write_decision(combo: Optional[Combo],
                            winner) -> tuple[Optional[Combo], Optional[str]]:
    """(combo to file evidence against, skip reason). Exactly one is non-None.

    * no combo -> ``no-combo``: an authored/refined brief, or a candidate whose
      id names no combo. There is nothing to be evidence ABOUT.
    * a combo, but the winner is not its ``builder_id`` (including no winner at
      all) -> ``winner-mismatch``: the run happened, but not on the machine the
      candidate is asking about, so filing it against this combo would be a
      lie. The event still records the outcome and the winner.
    """
    if combo is None:
        return None, CAPACITY_SKIP_NO_COMBO
    if not isinstance(winner, str) or winner != combo.builder_id:
        return None, CAPACITY_SKIP_WINNER_MISMATCH
    return combo, None


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

    ``subject`` is the combo the candidate NAMES (``combo_from_candidate_id``)
    — that is what the eventual experiment-result row describes, and it is
    knowable at dispatch time because the candidate was proposed about it. It
    is emphatically NOT the gating rung: the drain gates on ``omen-arc``
    occupancy while the brief runs on a conductor builder the drain does not
    choose, so stamping the rung here would file evidence against a backend
    that never served the work. An authored/refined brief, or a candidate whose
    id names no combo, keeps all three NULL. ``task_kind`` is the brief's own
    task_class, which IS known. ``gate_opened`` is null: an idle-drain dispatch
    opens no policy gate, it spends an idle slot.
    """
    experiment_id = experiment_id_for(brief)
    combo = combo_for(brief)
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
            "builder_id": combo.builder_id if combo else None,
            "model_id": combo.model_id if combo else None,
            "backend": combo.backend if combo else None,
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


def _require_identity(field: str, value) -> str:
    """A capacity observation's combo fields must be REAL, or there is no
    observation to write.

    ``project_capacity`` buckets an observation under
    ``builder_id|model_id|backend`` and maps a missing field to the literal
    ``"unknown"`` (``_combo_key`` L35-41, ``reduce_capacity`` L100-102). A
    written artifact carrying None — or the string ``"unknown"`` itself —
    therefore lands real evidence in a ``<builder>|unknown|unknown`` bucket
    naming no machine anyone can act on. The combo derivation already refuses
    those ids; this refuses them again at the one place an artifact is built,
    so no call site can reach the projection's fallback.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"capacity observation needs a non-empty {field}; got {value!r}")
    if value == UNKNOWN_COMBO_SEGMENT:
        raise ValueError(
            f"capacity observation {field} may not be {UNKNOWN_COMBO_SEGMENT!r}: "
            "that is the projection's placeholder for a field nobody knows, "
            "not an identity to file evidence against")
    return value


def build_capacity_observation(dispatch_id: str, experiment_id: str, *,
                               timestamp: str, builder_id: str,
                               model_id: str, backend: str,
                               succeeded: bool,
                               task_kind: Optional[str] = None,
                               est_tokens: Optional[int] = None) -> dict:
    """A ``capacity-observation.v1`` for a finished drain run.

    Only ever built for a run whose winner IS the combo's builder, so all three
    identity fields are known and real; ``_require_identity`` raises otherwise.
    ``model_id``/``backend`` are REQUIRED keyword arguments, so a call site
    cannot silently forget them and leave the projection to fill in "unknown".
    A run that names no combo, or whose winner is some other builder, gets the
    observation EVENT (the outcome is on the record) and no capacity artifact —
    the experiment result then reads ``no_observation``, which is true.

    ``hardware_profile_id`` stays null: the conductor does not report the
    hardware lineage a run was taken under, and inventing one would defeat that
    field's whole purpose (not conflating pre- and post-hardware-change
    evidence).
    """
    builder_id = _require_identity("builder_id", builder_id)
    model_id = _require_identity("model_id", model_id)
    backend = _require_identity("backend", backend)
    return {
        "contract_version": OBSERVATION_CONTRACT,
        "observation_id": observation_event_id(dispatch_id),
        "decision_id": decision_id_for(dispatch_id),
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id_for(dispatch_id),
        "timestamp": timestamp,
        "builder_id": builder_id,
        "model_id": model_id,
        "backend": backend,
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
