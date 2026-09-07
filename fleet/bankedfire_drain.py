#!/usr/bin/env python3
"""bankedfire-drain — Banked Fire P5 · Delta4 idle-drain (the last phase).

TWO-ECONOMIES-WIND-TUNNEL.html authored the idea: "When a sunk-economy node
goes idle, the scheduler pulls the highest-value experiment candidate and runs
it as a normal gated dispatch." HEARTH-BANKED-FIRE-STRATEGY.html's P5 row
makes it concrete: the 46 candidates priced in ``knowledge/candidate_worth.json``
(D1 economics, ratified 2026-07-04, ``fad3be9``) queue-serve into idle windows
under the ratified ``knowledge/operating-budget.json``.

One drain tick, in order (every check is ledgered, every tick, no-op or not):

  1. ARM state — an authored, suspendable toggle (the same pattern
     ``operating-budget.json`` already uses: authored_by/reason/suspended,
     flipped by a human, never inferred). Default DISARMED. State file:
     ``hearth/var/bankedfire_drain_arm.json`` (gitignored, like the rest of
     hearth/var/). Disarmed -> no-op, reason "disarmed". Since v2 the file also
     carries a ``scope`` naming which backlog sources unattended dispatch may
     draw from; a file with ``armed: true`` and NO scope key loads as DISARMED
     (no silent grandfathering — see load_arm_state).
  2. THE SLOT — one in-flight dispatch, tracked as an ``in_flight`` RECORD in
     the arm-state file (B-04; it was a bare plan-id string, which could not say
     whether the dispatch had actually been submitted or where its corpus
     records were). Checked via ``task_lane.task_status``. Still running ->
     no-op, reason "in-flight". Finished -> this tick WRITES THE RESULT BACK
     (an observation event into the run's own events.jsonl, then the slot is
     freed) and dispatches nothing: a tick either records a result or starts a
     dispatch, never both, because the next selection has to see a knowledge
     store rebuilt from the result. Conductor unreachable -> the slot STAYS
     HELD ("in-flight:status-unreachable"); clearing it on an unreadable status,
     as the drain used to, is how a second dispatch lands beside a live run.
     The write-back also runs while DISARMED: the kill switch stops spending
     the fleet, not telling the truth about what was already spent.
  3. Idle — TWO questions, both of which must say idle:
     (a) the conductor's own queue via ``task_lane.queue_status``: ``running``
         and ``queued`` must both be 0. An unreadable queue (ok:false, a raise,
         or counts that are not integers) -> no-op, reason
         "busy:queue-unreadable" — never "available by default".
     (b) the P2 occupancy probe (``occupancy.check_occupancy``) against
         ``omen-arc``. "Idle" requires ``occupancy == "available"``; "unknown"
         is NOT idle (fail-closed for this lane — Banked Fire design principle
         #4, "mechnet jobs always win", plus the P2 module's own
         opportunistic-call rule: unknown resolves to busy).
     The backend is ``omen-arc`` because it is the only rung with a REAL probe:
     the am4-oxen/am4-moe probes were removed on 2026-08-21 when the B70s left
     AM4, so the old ``am4-oxen`` gate had been decorative ever since —
     ``check_occupancy`` on a backend with no registered probe returns
     "available" unconditionally.
  4. Operating budget — ``knowledge/operating-budget.json``, validated with
     the existing ``tools.workflow.validate_budget`` schema check (never a
     hand-rolled parse). Honored to the extent the object actually expresses:
     ``suspended`` must be false, and ``unattended_dispatch_allowed`` must be
     true, and if ``active_hours`` is set the current UTC time must fall
     inside it. The current budget object has no spend counter or thermal
     telemetry feed (thermal/power fields are ceilings for a future live
     sensor, not something this tick can read today) — so those fields are
     ledgered as "declared but not live-checked" rather than silently ignored
     or invented. Any budget gate fails -> no-op, reason "no-budget".
  5. Brief selection — ``hearth.backlog.select_next(scope, sources)`` over the
     three pluggable sources (authored files, promoted refined intents, priced
     experiment candidates), priority authored > refined > candidate, scoped by
     the arm file. Nothing to run -> no-op, reason "no-candidates".
  6. Dispatch — acquire a ``hearth.toolsurface.occupancy.Lease`` for
     ``omen-arc`` (P2's reusable helper), then write, IN THIS ORDER, before the
     only network step: the experiment plan (unpublished), the dispatch event,
     the slot, the attempt flag — and only then
     ``task_lane.submit_task(**brief.submit_kwargs())`` with the brief's BODY as
     the prompt, so exactly one CCMETA header reaches the inbox and the brief's
     ``requires``/``max_age_s`` ride it. Persist-first at every step: see
     ``CRASH_POINTS`` for the numbered points and what the next tick makes of
     each. Every tick (dispatch or no-op) appends one ``bankedfire_drain`` event
     to the HEARTH kernel ledger, the same ledger P4's watchdog uses (separate
     from the knowledge/belief-projection sources, so drain BOOKKEEPING can
     never pollute beliefs — Banked Fire's "why not a second connector" rule).
     What DOES reach the belief layer is the corpus write-back in step 2, which
     goes through ``runs/hearth-drain/`` and the normal projection, not through
     a side channel.

Run:
    python -m fleet.bankedfire_drain              # one tick
    python -m fleet.bankedfire_drain --json        # machine-readable
    python -m fleet.bankedfire_drain --arm "reason" [--scope all]  # arm (authored)
    python -m fleet.bankedfire_drain --disarm "reason" # disarm (authored)
    python -m fleet.bankedfire_drain --status      # show current arm state + scope

Stdlib + hearth.backlog + hearth.kernel.ledger +
hearth.toolsurface.{occupancy,task_lane} + tools.workflow.validate_budget. No
new network surface: occupancy, queue_status and submit_task are the exact
P2/P3/G3 primitives, reused, not rebuilt (design principle #1: one scheduler;
the conductor owns the queue, this only decides *whether* to knock on its door
this tick).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hearth.backlog import dispatch as backlog_dispatch  # noqa: E402
from hearth.backlog import select as backlog_select  # noqa: E402
from hearth.backlog import sources as backlog_sources  # noqa: E402
from hearth.toolsurface import occupancy as occ_mod  # noqa: E402
from hearth.toolsurface import task_lane  # noqa: E402
from tools.workflow.validate_budget import ValidationError, validate_budget  # noqa: E402

DRAIN_CALLER = {"id": "bankedfire-drain", "runner_class": "human", "node": "omen"}
# The rung this lane gates on. Was "am4-oxen" until 2026-09-06: the AM4 probes
# were deleted on 2026-08-21 when the B70s moved to OMEN, and check_occupancy on
# a backend with NO registered probe returns "available" unconditionally — so
# the gate had been answering "idle" without measuring anything. omen-arc is the
# rung that actually has a probe (slot/KV goodput + tenancy fence).
DRAIN_BACKEND = "omen-arc"
PLAN_ID_PREFIX = "hearth-drain-"
# Candidate dispatches are PROOFING runs (retests/experiments on sunk idle
# compute), not production build work. The tag rides submit_task(task_class=) so
# ledger consumers — capacity buckets, scheduler hindsight — can separate them
# from real jobs instead of reading an empty retest lap as a 20s "build".
# Defined once, in hearth.backlog.sources, and re-exported here.
DRAIN_TASK_CLASS = backlog_sources.CANDIDATE_TASK_CLASS

ARM_STATE_FILENAME = "bankedfire_drain_arm.json"
DEFAULT_ARM_STATE_PATH = _REPO_ROOT / "hearth" / "var" / ARM_STATE_FILENAME
DEFAULT_BUDGET_PATH = _REPO_ROOT / "knowledge" / "operating-budget.json"
DEFAULT_CANDIDATE_WORTH_PATH = backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH
DEFAULT_EXPERIMENT_RESULTS_PATH = backlog_sources.DEFAULT_EXPERIMENT_RESULTS_PATH
DEFAULT_QUEUED_DIR = backlog_sources.DEFAULT_QUEUED_DIR
DEFAULT_DISPATCHED_DIR = backlog_sources.DEFAULT_DISPATCHED_DIR
DEFAULT_DONE_DIR = backlog_sources.DEFAULT_DONE_DIR
DEFAULT_REFINE_DIR = backlog_sources.DEFAULT_REFINE_DIR
# Where runs/<run_id>/ lives. The drain only ever writes under
# runs/hearth-drain/, and only for a dispatch it is actually making.
DEFAULT_CORPUS_ROOT = _REPO_ROOT


def default_arm_state_path() -> Path:
    """The arm file, honouring ``$HEARTH_ROOT`` at CALL time.

    The module constant is evaluated at import and so can never follow an env
    var a caller sets later; the CLI resolves through here instead, which is
    what lets a benign no-op tick run entirely inside a temp root (the ledger
    already respects HEARTH_ROOT the same way). With HEARTH_ROOT unset this
    returns exactly ``DEFAULT_ARM_STATE_PATH``.
    """
    env = os.environ.get("HEARTH_ROOT")
    if env:
        return Path(env).resolve() / "var" / ARM_STATE_FILENAME
    return DEFAULT_ARM_STATE_PATH


ARM_CONTRACT_VERSION = "bankedfire-drain-arm.v2"
ARM_CONTRACT_VERSION_V1 = "bankedfire-drain-arm.v1"

# Which backlog sources an armed drain may draw from. The enum is the one in
# hearth.backlog.select — a scope the chooser does not know is not a scope.
ARM_SCOPES = tuple(sorted(backlog_select.SCOPES))
# Derek's decision (2026-09-06): unattended dispatch may draw from authored AND
# self-generated sources from day one, so an --arm with no --scope writes "all"
# EXPLICITLY. The default is materialized in the file, never implied by absence.
DEFAULT_SCOPE = "all"
MISSING_SCOPE_REASON = (
    'arm file predates the scope contract; re-arm with --arm "<reason>" --scope all')


# ---------------------------------------------------------------------------
# ARM state — authored toggle, same shape/spirit as operating-budget.json.
# ---------------------------------------------------------------------------

LEGACY_PLAN_ID_KEY = "last_dispatch_plan_id"


def _default_arm_state() -> dict:
    return {
        "contract_version": ARM_CONTRACT_VERSION,
        "armed": False,
        "scope": DEFAULT_SCOPE,
        "authored_by": None,
        "reason": "default: idle-drain ships disarmed until a human arms it",
        "updated": None,
        # B-04: the single dispatch slot. Was the bare string
        # ``last_dispatch_plan_id``, which could only say "something is out
        # there" -- not what it was, whether it had actually been submitted, or
        # where its corpus records live, so a crashed tick could not be
        # reconstructed and a finished run could not be written back. See
        # hearth.backlog.dispatch.new_in_flight for the record's shape.
        "in_flight": None,
    }


def load_arm_state(path: Path = DEFAULT_ARM_STATE_PATH) -> dict:
    """Read the arm-state file, defaulting to DISARMED if absent/corrupt.

    A corrupt or unreadable file is treated as disarmed, never as armed —
    fail-safe, mirroring the occupancy probe's fail-open-to-busy discipline
    for opportunistic work (P2 module docstring): when in doubt, don't spend
    the mechnet unattended.

    SCOPE (v2, 2026-09-06). The arm file now says WHICH backlog sources
    unattended dispatch may draw from. Two ways that can be wrong, both of which
    load as DISARMED rather than being repaired here:

      * ``armed: true`` with NO ``scope`` key — a v1 file, written before the
        sources existed. Grandfathering it to "all" would silently widen a
        human's authorization from "run priced experiment candidates" to "run
        anything anyone drops in a directory". The reason names the exact
        re-arm command instead, so the human re-authorizes explicitly.
      * ``armed: true`` with a scope the chooser does not know — a typo must
        fail the tick, never fall back to a default.

    A file that is already disarmed keeps its own reason: there is nothing to
    fail closed about.

    IN_FLIGHT (v2, B-04). A valid file with no ``in_flight`` key loads as
    ``None`` -- an absent slot is an empty slot. The ONE exception is a file
    still carrying the pre-B-04 ``last_dispatch_plan_id`` string: that is
    adopted as a submitted slot rather than dropped, because dropping it would
    forget a run the conductor is still executing and let this tick dispatch a
    second one. Adoption is in memory only; nothing is rewritten behind the
    human's back.
    """
    if not path.is_file():
        return _default_arm_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_arm_state()
    if not isinstance(data, dict) or not isinstance(data.get("armed"), bool):
        return _default_arm_state()
    state = _default_arm_state()
    state.update(data)
    state["in_flight"] = _load_in_flight(data)
    state.pop(LEGACY_PLAN_ID_KEY, None)
    if data.get("armed"):
        if "scope" not in data:
            state["armed"] = False
            state["scope"] = None
            state["reason"] = MISSING_SCOPE_REASON
            state["disarmed_by"] = "missing-scope"
        elif data.get("scope") not in backlog_select.SCOPES:
            state["armed"] = False
            state["reason"] = (
                f"arm file names an unknown scope {data.get('scope')!r}; "
                f"re-arm with --arm \"<reason>\" --scope {'|'.join(ARM_SCOPES)}")
            state["disarmed_by"] = "unknown-scope"
    return state


def _load_in_flight(data: dict) -> Optional[dict]:
    """The slot as read off disk, or None. Never raises.

    A non-dict ``in_flight`` is treated as an empty slot: an unreadable slot
    must not be honoured as "something is running" (that would wedge the lane
    forever) nor silently allow a dispatch on top of a real run -- and the only
    shape that could mean a real run is a record, so anything else is empty.
    """
    record = data.get("in_flight")
    if isinstance(record, dict) and record:
        return dict(record)
    legacy = data.get(LEGACY_PLAN_ID_KEY)
    if isinstance(legacy, str) and legacy.strip():
        # A v1/early-v2 slot. It only ever got written AFTER a successful
        # submit, so it is a submitted slot with no corpus records behind it.
        return {
            "contract_version": backlog_dispatch.IN_FLIGHT_CONTRACT,
            "dispatch_id": None,
            "run_id": None,
            "experiment_id": None,
            "decision_id": None,
            "source": None,
            "source_ref": None,
            "plan_id": legacy,
            "inbox_path": None,
            "submit_attempted": True,
            "submitted": True,
            "adopted_from": LEGACY_PLAN_ID_KEY,
        }
    return None


def save_arm_state(state: dict, path: Path = DEFAULT_ARM_STATE_PATH,
                   replace_fn: Optional[Callable[[str, str], None]] = None) -> None:
    """Persist the arm state ATOMICALLY (temp file + ``os.replace``).

    The file now carries the dispatch slot, so a torn write is no longer a
    cosmetic problem: half a slot is either a forgotten run (double dispatch) or
    a phantom one (a wedged lane). ``replace_fn`` is injectable so a test can
    crash between the write and the swap and prove the old file survives whole.
    """
    backlog_dispatch.write_text_atomic(
        Path(path), json.dumps(state, indent=2) + "\n", replace_fn)


def set_armed(armed: bool, reason: str, authored_by: str = "derek",
             path: Path = DEFAULT_ARM_STATE_PATH,
             scope: Optional[str] = None) -> dict:
    """Authored ARM/DISARM ceremony: a human names a reason, it's timestamped
    and persisted. Never flips itself — callers are the CLI (--arm/--disarm)
    or, eventually, a kernel_change-style tool; there is no auto-arm path.

    Arming ALWAYS writes an explicit ``scope`` (default ``"all"``) and bumps the
    file to the v2 contract, so the file that authorizes a dispatch always says
    what it authorizes.

    NEITHER arming nor disarming touches ``in_flight``. Disarming is a kill
    switch for FUTURE dispatches; the run already in flight belongs to the
    conductor, and forgetting it here would neither stop it nor let anyone find
    its result. ``--status`` reports that combination as
    ``disarmed_with_in_flight`` so it is visible rather than implied."""
    state = load_arm_state(path)
    if armed:
        chosen = DEFAULT_SCOPE if scope is None else scope
    else:
        chosen = scope if scope is not None else (state.get("scope") or DEFAULT_SCOPE)
    if chosen not in backlog_select.SCOPES:
        raise ValueError(f"scope must be one of {ARM_SCOPES}; got {chosen!r}")
    state["contract_version"] = ARM_CONTRACT_VERSION
    state["armed"] = bool(armed)
    state["scope"] = chosen
    state["authored_by"] = authored_by
    state["reason"] = reason
    state["updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state.pop("disarmed_by", None)
    save_arm_state(state, path)
    return state


def status_report(path: Path = DEFAULT_ARM_STATE_PATH) -> dict:
    """The LOADED arm state plus the derived flags an operator needs.

    ``disarmed_with_in_flight`` is the one a human asks about after pulling the
    kill switch: the drain will start nothing new, and a run is still out there
    (the conductor owns it). ``in_flight_phase`` says whether that run was
    actually submitted -- see ``dispatch.in_flight_phase``.
    """
    state = load_arm_state(path)
    record = state.get("in_flight")
    return {
        **state,
        "in_flight_phase": backlog_dispatch.in_flight_phase(record),
        "disarmed_with_in_flight": bool(record) and not state["armed"],
    }


# ---------------------------------------------------------------------------
# Budget gate
# ---------------------------------------------------------------------------

def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


def _within_active_hours(active_hours: Optional[dict], now: datetime) -> bool:
    if active_hours is None:
        return True
    start_h, start_m = _parse_hhmm(active_hours["start"])
    end_h, end_m = _parse_hhmm(active_hours["end"])
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now.hour * 60 + now.minute
    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes  # wraps midnight


def check_budget(path: Path = DEFAULT_BUDGET_PATH,
                 now: Optional[datetime] = None) -> tuple[bool, dict]:
    """Return (has_headroom, detail). detail always includes what was checked,
    so a drain tick can ledger exactly what the budget object was asked.

    Honors only what operating-budget.json actually expresses today:
    ``suspended`` (must be false), ``unattended_dispatch_allowed`` (must be
    true), and ``active_hours`` (must contain now, if set). max_gpu_temp_c /
    max_power_w / max_fan_rpm are ceilings authored for a future live sensor
    feed (see the budget's own "reason" field) — there is no telemetry source
    wired to this tick yet, so they are reported as declared-but-not-live-
    checked rather than silently skipped or faked.
    """
    if not path.is_file():
        return False, {"error": f"operating budget not found: {path}",
                       "fail_field": "unreadable"}
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
        validate_budget(budget)
    except (json.JSONDecodeError, ValidationError) as exc:
        return False, {"error": f"invalid operating budget: {exc}",
                       "fail_field": "unreadable"}

    detail = {
        "budget_id": budget.get("budget_id"),
        "suspended": budget.get("suspended"),
        "unattended_dispatch_allowed": budget.get("unattended_dispatch_allowed"),
        "active_hours": budget.get("active_hours"),
        "thermal_wear_limits_declared": {
            "max_gpu_temp_c": budget.get("max_gpu_temp_c"),
            "max_power_w": budget.get("max_power_w"),
            "max_fan_rpm": budget.get("max_fan_rpm"),
        },
        "thermal_wear_limits_live_checked": False,
    }
    # fail_field is the FIELD NAME, low-cardinality and machine-readable, beside
    # the human sentence: a tick that refuses on the budget must say which of
    # the three checks refused, not just that "the budget" did.
    if budget.get("suspended"):
        detail["fail_reason"] = "budget.suspended is true"
        detail["fail_field"] = "suspended"
        return False, detail
    if not budget.get("unattended_dispatch_allowed"):
        detail["fail_reason"] = "budget.unattended_dispatch_allowed is false"
        detail["fail_field"] = "unattended_dispatch_allowed"
        return False, detail
    moment = now or datetime.now(timezone.utc)
    if not _within_active_hours(budget.get("active_hours"), moment):
        detail["fail_reason"] = "outside budget.active_hours"
        detail["fail_field"] = "active_hours"
        return False, detail
    return True, detail


# ---------------------------------------------------------------------------
# Candidate selection — MOVED to hearth.backlog.sources (B-03).
#
# The logic now lives beside the other two backlog sources so all three obey one
# ordering/reporting contract, and so the "already run" comparison could be
# fixed in one place: it used to compare candidate_id to candidate_id on
# experiment_results.json rows, which are experiment-result.v1 and carry
# experiment_id — no candidate_id key exists on them, so the skip set was always
# empty. See sources.RESULT_CANDIDATE_FIELDS for which fields are honoured now.
#
# This name is kept as a thin re-export (NOT a reimplementation) so any importer
# of ``drain.select_candidate`` keeps working with the fixed behaviour.
# ---------------------------------------------------------------------------

select_candidate = backlog_sources.select_candidate


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

# Every no-op branch a healthy tick can take. Reaching one of these means the
# drain evaluated its gates and correctly decided not to dispatch -- that IS the
# tick doing its job, so it is ok:true. Only a malfunction is ok:false.
# "busy:queue-unreadable" is here on purpose. It IS the tick doing its job: the
# conductor's queue could not be read, so the drain fail-closed and dispatched
# nothing. `ok` has meant "this tick did its job" since the 592-false-alarm fix,
# not "everything is healthy" — and the condition is NOT hidden, because it
# carries its own low-cardinality `outcome` label, which is what the projection
# buckets on. A conductor that has been unreachable all day shows up as a stack
# of busy:queue-unreadable ticks, not as an invisible zero.
#
# B-04 adds four more, all of them "the tick evaluated its gates and correctly
# declined to dispatch":
#   observed                        -- it wrote a finished run's result back to
#                                      the corpus. That IS the work of a tick.
#   in-flight:status-unreachable    -- same shape as busy:queue-unreadable: the
#                                      conductor could not be asked, so the slot
#                                      stays held and nothing is dispatched.
#   in-flight:submit-outcome-unknown -- fail-closed hold after a crash inside
#                                      the submit call (see in_flight_phase).
#   reconciled:never-submitted      -- it recovered a slot whose dispatch never
#                                      reached the conductor.
# Each keeps its own low-cardinality `outcome` label, so none of them hides in
# an aggregate: a lane stuck on an unreachable conductor shows as a stack of
# in-flight:status-unreachable ticks, never as an invisible zero.
BENIGN_OUTCOMES = frozenset({
    "disarmed", "busy", "busy:queue-unreadable", "no-budget", "no-candidates",
    "in-flight", "observed", "in-flight:status-unreachable",
    "in-flight:submit-outcome-unknown", "reconciled:never-submitted",
})

REASON_QUEUE_UNREADABLE = "busy:queue-unreadable"
REASON_STATUS_UNREACHABLE = "in-flight:status-unreachable"
REASON_SUBMIT_UNKNOWN = "in-flight:submit-outcome-unknown"
REASON_RECONCILED_NEVER_SUBMITTED = "reconciled:never-submitted"
# Bound what a no-op ledgers about a rejected backlog: a queued dir full of
# broken files must not turn one tick record into an unbounded blob.
MAX_REJECTED_REPORTED = 20


def _outcome_for(reason: str) -> str:
    """Map a tick `reason` onto its stable `outcome` label.

    `reason` is human-facing and carries the plan_id on a dispatch
    ("dispatched:hearth-drain-..."); `outcome` is the low-cardinality label the
    projection buckets on, so it must not embed an id."""
    if reason.startswith("dispatched"):
        return "dispatched"
    if reason.startswith("observed"):
        # reason carries which way the run went ("observed:succeeded"); the
        # bucket label must not, or every terminal outcome forks the series.
        return "observed"
    if reason == "no-op:dispatch-failed":
        return "dispatch-failed"
    return reason


def _record_tick(reason: str, detail: dict, ledger=None) -> Optional[str]:
    """Append one bankedfire_drain event. Best-effort: a ledger hiccup must
    never crash the scheduled task (same discipline as mechnet_watchdog's
    _record).

    `ok` means "this tick did its job", NOT "this tick dispatched". The drain is
    armed and fires every 1800s, and on an idle fleet with no unrun candidates
    the overwhelmingly common branch is a benign no-op -- so keying ok on
    "dispatched" made a perfectly healthy drain project an ok_rate of 0.0084
    over 592 ticks and read as a catastrophic outage in knowledge/capacity.json.
    Worse, those events set error=None, so they claimed a failure while naming
    none: structurally indistinguishable from a real fault.

    Which branch was taken now rides `outcome` (a top-level ledger field, NOT
    `result`) because the ledger stores only a result *digest* -- anything put
    in `result` is unrecoverable from history. This is the same reason
    mechnet_watchdog._record_hindsight routes its summary through `args`.

    NOTE on duration: this deliberately does not stamp a measured duration_ms.
    Ticks land in a null-task_class bucket that sorts first, and
    scheduler/ontology.py:_bucket_p90 matches the first bucket with p90 > 0 for
    a job whose task_class is None -- a real duration here would silently enlist
    the drain's heartbeat as a zero-cost machine estimate. Zero keeps it inert.
    """
    outcome = _outcome_for(reason)
    ok = outcome == "dispatched" or outcome in BENIGN_OUTCOMES
    try:
        from hearth.kernel.ledger import Ledger, new_event
        led = ledger or Ledger()
        return led.append(new_event(
            DRAIN_CALLER, "bankedfire_drain.tick",
            args={"backend": DRAIN_BACKEND, "outcome": outcome},
            result={"reason": reason, **detail},
            ok=ok,
            outcome=outcome,
            error=None if ok else (detail.get("submit_error") or reason),
        ))
    except Exception as exc:  # pragma: no cover - audit is best-effort
        print(f"[bankedfire-drain] ledger append failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------

def _read_queue(queue_status_fn: Callable[[], dict]) -> tuple[Optional[dict], Optional[str]]:
    """(counts, error). Fail-closed: anything but two integers is an error.

    A missing key, a None, or a raise all mean the same thing operationally —
    we do not know whether the conductor is idle — and the one answer this gate
    must never give in that state is "available by default".
    """
    try:
        queue = queue_status_fn()
    except Exception as exc:  # noqa: BLE001 - an SSH/probe fault must not crash the task
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(queue, dict):
        return None, f"queue_status returned {type(queue).__name__}, expected a dict"
    if not queue.get("ok"):
        return None, str(queue.get("error") or "queue_status returned ok:false")
    running, queued = queue.get("running"), queue.get("queued")
    if not isinstance(running, int) or isinstance(running, bool) \
            or not isinstance(queued, int) or isinstance(queued, bool):
        return None, "queue_status returned no integer queued/running counts"
    return {"queued": queued, "running": running}, None


class InjectedCrash(RuntimeError):
    """Raised by the test-only ``crash_after`` hook.

    Fault injection here is a NAMED PERSIST POINT, never a sleep or a race: the
    tick is killed deterministically after a specific write, and the assertion
    is about what the next tick reconstructs from disk.
    """


# The numbered persist points, in the order one dispatching tick reaches them.
# Every one of them is a point at which the process may die in production
# (scheduled task killed, machine reset, SSH hang), so every one has a stated
# next-tick behaviour and a test.
CRASH_POINTS = (
    "lease",            # 1. lease granted, nothing written yet
    "plan_artifact",    # 2. plan written as <stem>.json.pending
    "dispatch_event",   # 3. dispatch event appended
    "slot",             # 4. in_flight persisted (phase "prepared")
    "submit_attempt",   # 4b. in_flight flipped to submit_attempted=True
    "submit",           # 5. submit_task returned, slot not yet updated
    "slot_submitted",   # 6. in_flight carries plan_id + inbox_path
    "publish",          # 7. plan published onto the referenced path
    "observation",      # 8. write-back: observation event appended
    "observed_marker",  # 9. write-back: .observed marker written
)


def _write_back(state: dict, record: dict, outcome: str, *, arm_state_path: Path,
                corpus_root: Path, queued_dir: Path, dispatched_dir: Path,
                done_dir: Path, payload: dict, now, crash) -> dict:
    """Record a terminal outcome for the slot, then free it. Persist-first.

    Order, and why: publish any plan the dispatch tick did not get to ->
    append the observation (guarded) -> write the ``.observed`` marker ->
    retire the authored file -> clear the slot. The slot is cleared LAST, so a
    crash anywhere in here replays on the next tick instead of losing the
    result; and the append is guarded by BOTH the marker (fast) and the
    deterministic event_id already being in the file (true), so a crash between
    the append and the marker cannot append twice.
    """
    dispatch_id = record.get("dispatch_id")
    experiment_id = record.get("experiment_id")
    extra: dict = {"observation_outcome": outcome}
    timestamp = backlog_dispatch.utc_now_iso(now)

    if not dispatch_id:
        # A slot adopted from the pre-B-04 ``last_dispatch_plan_id`` string.
        # There is no run directory behind it, so there is nothing to write
        # back INTO -- say so rather than inventing a corpus record for a
        # dispatch whose intent was never written down.
        state["in_flight"] = None
        save_arm_state(state, arm_state_path)
        return {**extra, "legacy_slot": True, "observation_written": False}

    events = backlog_dispatch.events_path(corpus_root, dispatch_id)
    marker = backlog_dispatch.observed_marker_path(corpus_root, dispatch_id, experiment_id)
    event_id = backlog_dispatch.observation_event_id(dispatch_id)

    # A run that reached the conductor gets its plan published, so the corpus
    # grows the experiment-result row that makes the item ineligible. A run that
    # never did keeps its plan unpublished on purpose (see dispatch.py's
    # .pending rule) -- an experiment that never ran must not suppress itself.
    if record.get("submitted"):
        extra["plan_published"] = backlog_dispatch.publish_plan(
            corpus_root, dispatch_id, experiment_id)

    already = marker.is_file() or backlog_dispatch.event_already_recorded(events, event_id)
    extra["observation_already_recorded"] = already
    extra["observation_written"] = not already
    if not already:
        observation_ref = None
        winner = payload.get("winner")
        if isinstance(winner, str) and winner.strip():
            observation = backlog_dispatch.build_capacity_observation(
                dispatch_id, experiment_id, timestamp=timestamp, builder_id=winner,
                succeeded=outcome == backlog_dispatch.OUTCOME_SUCCEEDED,
                task_kind=record.get("task_class"), est_tokens=record.get("est_tokens"))
            observation_path = backlog_dispatch.observation_artifact_path(
                corpus_root, dispatch_id, experiment_id)
            backlog_dispatch.write_json_atomic(observation_path, observation)
            observation_ref = backlog_dispatch.corpus_ref(corpus_root, observation_path)
            extra["capacity_observation"] = observation_ref
        backlog_dispatch.append_corpus_event(events, backlog_dispatch.build_observation_event(
            dispatch_id, experiment_id, timestamp=timestamp, outcome=outcome,
            payload=payload, observation_ref=observation_ref))
        extra["observation_event_id"] = event_id
    crash("observation")

    if not marker.is_file():
        backlog_dispatch.write_text_atomic(marker, f"{timestamp}\n")
    crash("observed_marker")

    if record.get("source") == "authored" and record.get("plan_id"):
        extra["backlog_move"] = _retire_authored(record, queued_dir, dispatched_dir, done_dir)

    state["in_flight"] = None
    save_arm_state(state, arm_state_path)
    return extra


def _retire_authored(record: dict, queued_dir: Path, dispatched_dir: Path,
                     done_dir: Path) -> dict:
    """Move the authored file queued -> dispatched -> done.

    Both moves are idempotent (``already_moved``/``missing``), which is what
    lets the dispatch tick's ``mark_dispatched`` be finished here when the
    process died between the submit and that move: the file is looked up again
    in ``queued/`` and moved if it is still there. A failure to move must not
    lose the result, so it is reported, never raised.
    """
    plan_id = record.get("plan_id")
    out: dict = {}
    try:
        scan = backlog_sources.authored_source(queued_dir)
        brief = next((b for b in scan.briefs
                      if b.source_ref == record.get("source_ref")), None)
        if brief is not None:
            out["dispatched"] = backlog_sources.mark_dispatched(
                brief, plan_id, queued_dir, dispatched_dir)["status"]
        out["done"] = backlog_sources.mark_done(plan_id, dispatched_dir, done_dir)["status"]
    except (ValueError, OSError) as exc:  # noqa: BLE001 - bookkeeping, not the result
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _resolve_in_flight(state: dict, record: dict, *, arm_state_path: Path,
                       corpus_root: Path, queued_dir: Path, dispatched_dir: Path,
                       done_dir: Path, task_status_fn: Callable[..., dict],
                       now, crash) -> tuple[str, dict]:
    """What this tick does about the slot. Returns (reason, detail).

    Every branch ENDS the tick: a tick either writes a result back or starts a
    dispatch, never both. Doing both would select the next brief from a
    knowledge store that has not been rebuilt since the write-back -- which for
    a candidate means re-dispatching the one that just finished.
    """
    phase = backlog_dispatch.in_flight_phase(record)
    base = {
        "in_flight_phase": phase,
        "in_flight_plan_id": record.get("plan_id"),
        "in_flight_experiment_id": record.get("experiment_id"),
        "in_flight_run_id": record.get("run_id"),
        "in_flight_source": record.get("source"),
        "in_flight_source_ref": record.get("source_ref"),
    }
    write_back = dict(arm_state_path=arm_state_path, corpus_root=corpus_root,
                      queued_dir=queued_dir, dispatched_dir=dispatched_dir,
                      done_dir=done_dir, now=now, crash=crash)

    if phase == backlog_dispatch.PHASE_ATTEMPTED:
        # The submit call was entered and never came back with a plan_id. The
        # inbox write may or may not have landed, and there is no primitive that
        # can tell us. Fail closed: hold the slot, dispatch nothing, and say so.
        return REASON_SUBMIT_UNKNOWN, base

    if phase == backlog_dispatch.PHASE_PREPARED:
        extra = _write_back(
            state, record, backlog_dispatch.OUTCOME_NEVER_SUBMITTED,
            payload={"reconciled": "never-submitted",
                     "detail": "the slot was persisted but the submit call was "
                               "never entered, so nothing reached the conductor"},
            **write_back)
        return REASON_RECONCILED_NEVER_SUBMITTED, {**base, **extra}

    try:
        status = task_status_fn(record["plan_id"])
    except Exception as exc:  # noqa: BLE001 - an SSH fault must not crash the task
        return REASON_STATUS_UNREACHABLE, {**base,
                                           "status_error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(status, dict):
        return REASON_STATUS_UNREACHABLE, {
            **base, "status_error": f"task_status returned {type(status).__name__}"}
    if not status.get("ok"):
        # The old behaviour CLEARED the slot here, so an SSH hiccup could let a
        # second dispatch start beside a live run. Unreachable is not resolved.
        return REASON_STATUS_UNREACHABLE, {
            **base, "status_error": str(status.get("error") or "task_status returned ok:false")}
    if not status.get("done"):
        return "in-flight", base

    result_ok, winner, result_path = backlog_dispatch.read_result(status)
    outcome = backlog_dispatch.completion_outcome(result_ok, winner)
    extra = _write_back(state, record, outcome, payload={
        "plan_id": record.get("plan_id"),
        "result_path": result_path or record.get("result_path"),
        "winner": winner,
        "result_ok": result_ok,
        "source": record.get("source"),
        "source_ref": record.get("source_ref"),
    }, **write_back)
    return f"observed:{outcome}", {**base, **extra}


def run_tick(arm_state_path: Path = DEFAULT_ARM_STATE_PATH,
            budget_path: Path = DEFAULT_BUDGET_PATH,
            worth_path: Path = DEFAULT_CANDIDATE_WORTH_PATH,
            results_path: Path = DEFAULT_EXPERIMENT_RESULTS_PATH,
            queued_dir: Path = DEFAULT_QUEUED_DIR,
            refine_dir: Path = DEFAULT_REFINE_DIR,
            dispatched_dir: Path = DEFAULT_DISPATCHED_DIR,
            done_dir: Path = DEFAULT_DONE_DIR,
            corpus_root: Path = DEFAULT_CORPUS_ROOT,
            occupancy_check: Callable[[str], dict] = occ_mod.check_occupancy,
            acquire_lease: Callable[..., occ_mod.Lease] = occ_mod.acquire_lease,
            submit_task_fn: Callable[..., dict] = task_lane.submit_task,
            task_status_fn: Callable[..., dict] = task_lane.task_status,
            queue_status_fn: Callable[[], dict] = task_lane.queue_status,
            ledger=None, write_ledger: bool = True,
            now=None, crash_after: Optional[str] = None) -> dict:
    """Run exactly one drain tick and return its report. Every path through
    this function ledgers exactly one bankedfire_drain.tick event (unless
    write_ledger=False, for offline unit tests).

    ``crash_after`` is the test-only fault-injection hook: it names one of
    ``CRASH_POINTS`` and raises ``InjectedCrash`` immediately after that persist
    point, so the crash matrix asserts against real on-disk state rather than a
    simulated one. It defaults to None and nothing in production sets it.
    """
    state = load_arm_state(arm_state_path)
    detail: dict = {"armed": state["armed"], "scope": state.get("scope")}

    def _finish(reason: str, extra: Optional[dict] = None) -> dict:
        detail.update(extra or {})
        event_id = _record_tick(reason, detail, ledger=ledger) if write_ledger else None
        return {"reason": reason, "detail": detail, "ledger_event_id": event_id}

    def _crash(point: str) -> None:
        if crash_after == point:
            raise InjectedCrash(f"injected crash after persist point {point!r}")

    # THE SLOT COMES FIRST, and it is checked even while disarmed. Writing down
    # what a finished run did is not a dispatch: the kill switch stops the drain
    # from spending the fleet, not from telling the truth about what it already
    # spent. Nothing below this block runs while disarmed.
    #
    # The one slot a DISARMED tick leaves strictly alone is one with no run
    # directory behind it -- a slot adopted from the pre-B-04
    # ``last_dispatch_plan_id`` string. There is nothing to write back into, so
    # resolving it would buy an SSH round trip and a rewrite of a human's arm
    # file for no record at all, and load_arm_state's rule is that a read never
    # repairs that file behind the human's back. Re-arming migrates it (set_armed
    # persists the adopted slot), and the first ARMED tick then resolves it.
    record = state.get("in_flight")
    if record and not state["armed"] and not record.get("dispatch_id"):
        return _finish("disarmed", {"disarmed_with_in_flight": True,
                                    "in_flight_action": "untouched:legacy-slot",
                                    "in_flight_plan_id": record.get("plan_id"),
                                    "disarmed_by": state.get("disarmed_by"),
                                    "arm_reason": state.get("reason")})
    if record:
        reason, extra = _resolve_in_flight(
            state, record, arm_state_path=arm_state_path, corpus_root=corpus_root,
            queued_dir=queued_dir, dispatched_dir=dispatched_dir, done_dir=done_dir,
            task_status_fn=task_status_fn, now=now, crash=_crash)
        if not state["armed"]:
            return _finish("disarmed", {**extra,
                                        "disarmed_with_in_flight": True,
                                        "in_flight_action": reason,
                                        "disarmed_by": state.get("disarmed_by"),
                                        "arm_reason": state.get("reason")})
        return _finish(reason, extra)

    if not state["armed"]:
        return _finish("disarmed", {"disarmed_by": state.get("disarmed_by"),
                                    "arm_reason": state.get("reason")})

    # Defensive duplicate of load_arm_state's scope rule, stated locally so this
    # function is total: select_next raises on an unknown scope, and a raise here
    # would skip the ledger row that every tick owes.
    scope = state.get("scope")
    if scope not in backlog_select.SCOPES:
        return _finish("disarmed", {"disarmed_by": "unknown-scope",
                                    "arm_reason": state.get("reason")})

    # Idle, question 1: is the conductor's own queue empty? Occupancy answers
    # for the RUNG; this answers for the QUEUE, and unattended work must not
    # jump a backlog a human is already waiting on.
    queue, queue_error = _read_queue(queue_status_fn)
    if queue is None:
        return _finish(REASON_QUEUE_UNREADABLE, {"queue_error": queue_error})
    detail["queue"] = queue
    if queue["running"] or queue["queued"]:
        return _finish("busy", {"busy_reason": "conductor-queue-not-idle"})

    # Idle, question 2: the P2 occupancy probe on the rung that HAS one.
    occ_result = occupancy_check(DRAIN_BACKEND)
    occupancy = occ_result.get("occupancy", "unknown")
    detail["occupancy"] = occupancy
    if occupancy != "available":
        return _finish("busy", {"occupancy_detail": occ_result})

    has_headroom, budget_detail = check_budget(budget_path, now=now)
    detail["budget"] = budget_detail
    if not has_headroom:
        # The gate names the failing FIELD, not just itself. `reason` stays the
        # low-cardinality "no-budget" the ledger buckets on.
        return _finish("no-budget",
                       {"budget_fail_field": budget_detail.get("fail_field"),
                        "budget_fail_reason": budget_detail.get("fail_reason")
                        or budget_detail.get("error")})

    scans = {
        "authored": backlog_sources.authored_source(queued_dir),
        "refined": backlog_sources.refined_source(refine_dir),
        "candidate": backlog_sources.candidate_source(worth_path, results_path),
    }
    detail["backlog_counts"] = {name: len(scan) for name, scan in scans.items()}
    brief = backlog_select.select_next(scope, scans)
    if brief is None:
        rejected = [row for scan in scans.values() for row in scan.rejected]
        return _finish("no-candidates",
                       {"backlog_rejected": rejected[:MAX_REJECTED_REPORTED],
                        "backlog_rejected_total": len(rejected)})
    detail["source"] = brief.source
    detail["source_ref"] = brief.source_ref
    detail["slug"] = brief.slug

    # --- 1. the lease ------------------------------------------------------
    lease = acquire_lease(DRAIN_BACKEND, pinned=False)
    if not lease.granted:
        return _finish("busy", {"occupancy_detail": {"occupancy": lease.occupancy_at_grant}})
    _crash("lease")

    # Token hole #1 (M3): every submit_task call site stamps task_class AND
    # est_tokens. B-03 sent brief.render() as the prompt, so submit_task's own
    # CCMETA header was prepended to a body that already had one and the inbox
    # file carried TWO (the conductor's _extract_ccmeta uses .search, so the
    # brief's became inert body text). The body is what goes on the wire now;
    # builders/task_class/requires/max_age_s ride submit_kwargs() and end up in
    # the one header submit_task writes. est_tokens is re-derived from the bytes
    # actually sent, so the ledger row and the header still describe one thing.
    kwargs = brief.submit_kwargs()
    kwargs["prompt"] = brief.body
    if brief.est_tokens is None:
        kwargs["est_tokens"] = task_lane.estimate_tokens(brief.body, brief.task_class)
    detail["est_tokens"] = kwargs["est_tokens"]

    dispatch_id = backlog_dispatch.new_dispatch_id(brief)
    experiment_id = backlog_dispatch.experiment_id_for(brief)
    timestamp = backlog_dispatch.utc_now_iso(now)
    detail["dispatch_id"] = dispatch_id
    detail["experiment_id"] = experiment_id
    detail["run_id"] = backlog_dispatch.run_id_for(dispatch_id)

    # --- 2. the plan artifact (unpublished) --------------------------------
    plan = backlog_dispatch.build_drain_experiment_plan(
        brief, dispatch_id, timestamp=timestamp, scope=scope, backend=DRAIN_BACKEND)
    backlog_dispatch.write_pending_plan(corpus_root, dispatch_id, plan)
    plan_ref = backlog_dispatch.corpus_ref(
        corpus_root,
        backlog_dispatch.plan_artifact_path(corpus_root, dispatch_id, experiment_id))
    detail["plan_artifact"] = plan_ref
    _crash("plan_artifact")

    # --- 3. the dispatch event ---------------------------------------------
    events = backlog_dispatch.events_path(corpus_root, dispatch_id)
    backlog_dispatch.append_corpus_event(events, backlog_dispatch.build_dispatch_event(
        brief, dispatch_id, timestamp=timestamp, plan_ref=plan_ref,
        est_tokens=kwargs["est_tokens"], scope=scope, backend=DRAIN_BACKEND))
    detail["dispatch_event_id"] = backlog_dispatch.dispatch_event_id(dispatch_id)
    _crash("dispatch_event")

    # --- 4. the slot, before the only network step -------------------------
    record = backlog_dispatch.new_in_flight(
        brief, dispatch_id, dispatched_at=timestamp, plan_artifact=plan_ref,
        dispatch_event_id=backlog_dispatch.dispatch_event_id(dispatch_id),
        corpus_root=corpus_root, est_tokens=kwargs["est_tokens"])
    state["in_flight"] = record
    save_arm_state(state, arm_state_path)
    _crash("slot")

    # 4b. The attempt flag is what separates "certainly never submitted" from
    # "unknown". It is written BEFORE the call so a crash inside submit_task can
    # never be mistaken for a crash before it.
    record["submit_attempted"] = True
    state["in_flight"] = record
    save_arm_state(state, arm_state_path)
    _crash("submit_attempt")

    # --- 5. the dispatch ----------------------------------------------------
    submit_result = submit_task_fn(**kwargs)
    _crash("submit")

    if not submit_result.get("ok"):
        # Nothing reached the inbox. Record the failure against the same run,
        # free the slot, and drop the lease (it is a probe result, not a
        # server-side reservation -- see the module note in occupancy.py).
        extra = _write_back(
            state, record, backlog_dispatch.OUTCOME_DISPATCH_FAILED,
            payload={"submit_error": submit_result.get("error"),
                     "source": brief.source, "source_ref": brief.source_ref},
            arm_state_path=arm_state_path, corpus_root=corpus_root,
            queued_dir=queued_dir, dispatched_dir=dispatched_dir, done_dir=done_dir,
            now=now, crash=_crash)
        return _finish("no-op:dispatch-failed",
                       {**extra, "submit_error": submit_result.get("error")})

    # --- 6. the slot again, now naming the conductor's run -----------------
    plan_id = submit_result["plan_id"]
    record.update({
        "plan_id": plan_id,
        "inbox_path": submit_result.get("inbox_path"),
        "result_path": submit_result.get("result_path"),
        "submitted": True,
        "submitted_at": backlog_dispatch.utc_now_iso(now),
    })
    state["in_flight"] = record
    save_arm_state(state, arm_state_path)
    _crash("slot_submitted")

    # --- 7. publish the plan: the dispatch happened, so the corpus may say so
    detail["plan_published"] = backlog_dispatch.publish_plan(
        corpus_root, dispatch_id, experiment_id)
    _crash("publish")

    if brief.source == "authored":
        detail["backlog_move"] = backlog_sources.mark_dispatched(
            brief, plan_id, queued_dir, dispatched_dir)["status"]

    return _finish(f"dispatched:{plan_id}", {"submit_result": submit_result})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable tick output")
    ap.add_argument("--arm", metavar="REASON", help="authored ARM (requires a reason)")
    ap.add_argument("--disarm", metavar="REASON", help="authored DISARM (requires a reason)")
    ap.add_argument("--status", action="store_true", help="print current arm state, run nothing")
    ap.add_argument("--authored-by", default="derek")
    ap.add_argument("--scope", choices=ARM_SCOPES, default=None,
                    help=(f"backlog sources an armed drain may draw from "
                          f"(default {DEFAULT_SCOPE!r}, written explicitly)"))
    args = ap.parse_args(argv)

    # Resolved at CALL time so $HEARTH_ROOT redirects the whole CLI (arm file
    # and ledger both) into one root -- which is what makes a benign no-op tick
    # provable without touching a single live file.
    arm_path = default_arm_state_path()

    if args.status:
        # --status prints the LOADED state, so a v1 file with no scope shows the
        # armed:false + re-arm reason the tick would actually see, not the raw
        # bytes on disk. It also names the slot's phase and flags the
        # disarmed-with-a-run-still-out-there combination explicitly.
        print(json.dumps(status_report(arm_path), indent=2))
        return 0
    if args.arm is not None:
        state = set_armed(True, args.arm, authored_by=args.authored_by,
                          path=arm_path, scope=args.scope)
        print(json.dumps(state, indent=2))
        return 0
    if args.disarm is not None:
        state = set_armed(False, args.disarm, authored_by=args.authored_by,
                          path=arm_path, scope=args.scope)
        print(json.dumps(state, indent=2))
        return 0

    report = run_tick(arm_state_path=arm_path)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"bankedfire-drain tick: {report['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
