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
     hearth/var/). Disarmed -> no-op, reason "disarmed".
  2. Occupancy — reuses the existing P2 probe
     (``hearth.toolsurface.occupancy.check_occupancy``) against the
     ``am4-oxen`` backend. "Idle" requires ``occupancy == "available"``;
     "unknown" is NOT idle (fail-closed for this lane — Banked Fire design
     principle #4, "mechnet jobs always win", plus the P2 module's own
     opportunistic-call rule: unknown resolves to busy). Busy/unknown -> no-op,
     reason "busy".
  3. Operating budget — ``knowledge/operating-budget.json``, validated with
     the existing ``tools.workflow.validate_budget`` schema check (never a
     hand-rolled parse). Honored to the extent the object actually expresses:
     ``suspended`` must be false, and ``unattended_dispatch_allowed`` must be
     true, and if ``active_hours`` is set the current UTC time must fall
     inside it. The current budget object has no spend counter or thermal
     telemetry feed (thermal/power fields are ceilings for a future live
     sensor, not something this tick can read today) — so those fields are
     ledgered as "declared but not live-checked" rather than silently ignored
     or invented. Any budget gate fails -> no-op, reason "no-budget".
  4. Candidate selection — the highest ``worth_points`` entry in
     ``knowledge/candidate_worth.json`` whose ``candidate_id`` has not already
     appeared in ``knowledge/experiment_results.json``'s ``results[]``. Ties
     break on candidate_id (deterministic). None left -> no-op, reason
     "no-candidates".
  5. Single in-flight dispatch — the drain's own last dispatch (tracked in the
     same arm-state file) is checked via ``task_lane.task_status`` before a
     new one is allowed. Not yet done -> no-op, reason "in-flight". This is
     the drain's own lease discipline, layered on top of (not replacing) the
     occupancy Lease from P2.
  6. Dispatch — acquire a ``hearth.toolsurface.occupancy.Lease`` for
     ``am4-oxen`` (P2's reusable helper, built explicitly for this), then
     ``hearth.toolsurface.task_lane.submit_task`` with a ``hearth-drain-``
     prefixed plan_id hint carrying the candidate's worth. Every tick (dispatch
     or no-op) appends one ``bankedfire_drain`` event to the HEARTH kernel
     ledger, the same ledger P4's watchdog uses (separate from the
     knowledge/belief-projection sources, so drain bookkeeping can never
     pollute beliefs — Banked Fire's "why not a second connector" rule, same
     spirit).

Run:
    python -m fleet.bankedfire_drain              # one tick
    python -m fleet.bankedfire_drain --json        # machine-readable
    python -m fleet.bankedfire_drain --arm "reason"    # arm (authored)
    python -m fleet.bankedfire_drain --disarm "reason" # disarm (authored)
    python -m fleet.bankedfire_drain --status      # show current arm state

Stdlib + hearth.kernel.ledger + hearth.toolsurface.{occupancy,task_lane} +
tools.workflow.validate_budget. No new network surface: occupancy and
submit_task are the exact P2/P3 primitives, reused, not rebuilt (design
principle #1: one scheduler; the conductor owns the queue, this only decides
*whether* to knock on its door this tick).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hearth.toolsurface import occupancy as occ_mod  # noqa: E402
from hearth.toolsurface import task_lane  # noqa: E402
from tools.workflow.validate_budget import ValidationError, validate_budget  # noqa: E402

DRAIN_CALLER = {"id": "bankedfire-drain", "runner_class": "human", "node": "omen"}
DRAIN_BACKEND = "am4-oxen"
PLAN_ID_PREFIX = "hearth-drain-"
# Drain dispatches are PROOFING runs (retests/experiments on sunk idle compute),
# not production build work. The tag rides submit_task(task_class=) so ledger
# consumers — capacity buckets, scheduler hindsight — can separate them from
# real jobs instead of reading an empty retest lap as a 20s "build".
DRAIN_TASK_CLASS = "proofing"

DEFAULT_ARM_STATE_PATH = _REPO_ROOT / "hearth" / "var" / "bankedfire_drain_arm.json"
DEFAULT_BUDGET_PATH = _REPO_ROOT / "knowledge" / "operating-budget.json"
DEFAULT_CANDIDATE_WORTH_PATH = _REPO_ROOT / "knowledge" / "candidate_worth.json"
DEFAULT_EXPERIMENT_RESULTS_PATH = _REPO_ROOT / "knowledge" / "experiment_results.json"

ARM_CONTRACT_VERSION = "bankedfire-drain-arm.v1"


# ---------------------------------------------------------------------------
# ARM state — authored toggle, same shape/spirit as operating-budget.json.
# ---------------------------------------------------------------------------

def _default_arm_state() -> dict:
    return {
        "contract_version": ARM_CONTRACT_VERSION,
        "armed": False,
        "authored_by": None,
        "reason": "default: idle-drain ships disarmed until a human arms it",
        "updated": None,
        "last_dispatch_plan_id": None,
    }


def load_arm_state(path: Path = DEFAULT_ARM_STATE_PATH) -> dict:
    """Read the arm-state file, defaulting to DISARMED if absent/corrupt.

    A corrupt or unreadable file is treated as disarmed, never as armed —
    fail-safe, mirroring the occupancy probe's fail-open-to-busy discipline
    for opportunistic work (P2 module docstring): when in doubt, don't spend
    the mechnet unattended.
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
    return state


def save_arm_state(state: dict, path: Path = DEFAULT_ARM_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def set_armed(armed: bool, reason: str, authored_by: str = "derek",
             path: Path = DEFAULT_ARM_STATE_PATH) -> dict:
    """Authored ARM/DISARM ceremony: a human names a reason, it's timestamped
    and persisted. Never flips itself — callers are the CLI (--arm/--disarm)
    or, eventually, a kernel_change-style tool; there is no auto-arm path."""
    state = load_arm_state(path)
    state["armed"] = bool(armed)
    state["authored_by"] = authored_by
    state["reason"] = reason
    state["updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_arm_state(state, path)
    return state


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
        return False, {"error": f"operating budget not found: {path}"}
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
        validate_budget(budget)
    except (json.JSONDecodeError, ValidationError) as exc:
        return False, {"error": f"invalid operating budget: {exc}"}

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
    if budget.get("suspended"):
        detail["fail_reason"] = "budget.suspended is true"
        return False, detail
    if not budget.get("unattended_dispatch_allowed"):
        detail["fail_reason"] = "budget.unattended_dispatch_allowed is false"
        return False, detail
    moment = now or datetime.now(timezone.utc)
    if not _within_active_hours(budget.get("active_hours"), moment):
        detail["fail_reason"] = "outside budget.active_hours"
        return False, detail
    return True, detail


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def select_candidate(worth_path: Path = DEFAULT_CANDIDATE_WORTH_PATH,
                     results_path: Path = DEFAULT_EXPERIMENT_RESULTS_PATH) -> Optional[dict]:
    """Highest-worth priced candidate not yet present in experiment_results.json.

    Deterministic tie-break on candidate_id so two ticks with an identical
    worth table always pick the same candidate (no hidden randomness in an
    unattended dispatch).
    """
    worth_doc = _load_json(worth_path, {"entries": []})
    results_doc = _load_json(results_path, {"results": []})
    already_run = {r.get("candidate_id") for r in results_doc.get("results", [])
                  if isinstance(r, dict)}
    entries = [e for e in worth_doc.get("entries", [])
              if isinstance(e, dict) and e.get("candidate_id") not in already_run]
    if not entries:
        return None
    entries.sort(key=lambda e: (-int(e.get("worth_points", 0)), e["candidate_id"]))
    return entries[0]


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

# Every no-op branch a healthy tick can take. Reaching one of these means the
# drain evaluated its gates and correctly decided not to dispatch -- that IS the
# tick doing its job, so it is ok:true. Only a malfunction is ok:false.
BENIGN_OUTCOMES = frozenset({
    "disarmed", "busy", "no-budget", "no-candidates", "in-flight",
})


def _outcome_for(reason: str) -> str:
    """Map a tick `reason` onto its stable `outcome` label.

    `reason` is human-facing and carries the plan_id on a dispatch
    ("dispatched:hearth-drain-..."); `outcome` is the low-cardinality label the
    projection buckets on, so it must not embed an id."""
    if reason.startswith("dispatched"):
        return "dispatched"
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

def run_tick(arm_state_path: Path = DEFAULT_ARM_STATE_PATH,
            budget_path: Path = DEFAULT_BUDGET_PATH,
            worth_path: Path = DEFAULT_CANDIDATE_WORTH_PATH,
            results_path: Path = DEFAULT_EXPERIMENT_RESULTS_PATH,
            occupancy_check: Callable[[str], dict] = occ_mod.check_occupancy,
            acquire_lease: Callable[..., occ_mod.Lease] = occ_mod.acquire_lease,
            submit_task_fn: Callable[..., dict] = task_lane.submit_task,
            task_status_fn: Callable[..., dict] = task_lane.task_status,
            ledger=None, write_ledger: bool = True) -> dict:
    """Run exactly one drain tick and return its report. Every path through
    this function ledgers exactly one bankedfire_drain.tick event (unless
    write_ledger=False, for offline unit tests)."""
    state = load_arm_state(arm_state_path)
    detail: dict = {"armed": state["armed"]}

    def _finish(reason: str, extra: Optional[dict] = None) -> dict:
        detail.update(extra or {})
        event_id = _record_tick(reason, detail, ledger=ledger) if write_ledger else None
        return {"reason": reason, "detail": detail, "ledger_event_id": event_id}

    if not state["armed"]:
        return _finish("disarmed")

    # Single-in-flight rule: a prior drain dispatch with no result yet blocks
    # this tick outright, before even probing occupancy again.
    last_plan_id = state.get("last_dispatch_plan_id")
    if last_plan_id:
        status = task_status_fn(last_plan_id)
        if status.get("ok") and not status.get("done"):
            return _finish("in-flight", {"in_flight_plan_id": last_plan_id})
        # done (or an unreachable conductor -> treat as resolved so the drain
        # doesn't wedge forever on a transient SSH hiccup) clears the slot.
        state["last_dispatch_plan_id"] = None
        save_arm_state(state, arm_state_path)

    occ_result = occupancy_check(DRAIN_BACKEND)
    occupancy = occ_result.get("occupancy", "unknown")
    detail["occupancy"] = occupancy
    if occupancy != "available":
        return _finish("busy", {"occupancy_detail": occ_result})

    has_headroom, budget_detail = check_budget(budget_path)
    detail["budget"] = budget_detail
    if not has_headroom:
        return _finish("no-budget")

    candidate = select_candidate(worth_path, results_path)
    if candidate is None:
        return _finish("no-candidates")
    detail["candidate"] = {"candidate_id": candidate["candidate_id"],
                           "worth_points": candidate.get("worth_points")}

    lease = acquire_lease(DRAIN_BACKEND, pinned=False)
    if not lease.granted:
        return _finish("busy", {"occupancy_detail": {"occupancy": lease.occupancy_at_grant}})

    prompt = (
        f"Idle-drain dispatch (Banked Fire P5). Run experiment candidate "
        f"{candidate['candidate_id']!r} (worth_points={candidate.get('worth_points')}): "
        f"{candidate.get('reason', '')}\n\n"
        f"This is unattended, gated, opportunistic fleet work — treat it as a normal build."
    )
    submit_result = submit_task_fn(
        prompt, plan_id_hint=f"drain-{candidate['candidate_id'][:40]}",
        task_class=DRAIN_TASK_CLASS,
    )
    if not submit_result.get("ok"):
        return _finish("no-op:dispatch-failed", {"submit_error": submit_result.get("error")})

    plan_id = submit_result["plan_id"]
    state["last_dispatch_plan_id"] = plan_id
    save_arm_state(state, arm_state_path)
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
    args = ap.parse_args(argv)

    if args.status:
        print(json.dumps(load_arm_state(), indent=2))
        return 0
    if args.arm is not None:
        state = set_armed(True, args.arm, authored_by=args.authored_by)
        print(json.dumps(state, indent=2))
        return 0
    if args.disarm is not None:
        state = set_armed(False, args.disarm, authored_by=args.authored_by)
        print(json.dumps(state, indent=2))
        return 0

    report = run_tick()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"bankedfire-drain tick: {report['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
