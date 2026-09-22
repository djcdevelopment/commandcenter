"""Pure route validation against capacity, catalog, policy and approval (WI-G2).

Validation returns validated, rejected, or needs_approval and nothing else. It
is a pure check over the proposal: it never modifies it, never substitutes
another route, and never creates synthetic stop_and_ask nodes.

What WI-G2a changed, each item reproduced as a regression test first:

* **Capacity is READ.** `current_snapshot` was a parameter the body never
  touched, so a route onto an unreachable door and a rung recorded `ready: null`
  validated clean. The snapshot is now opened, the fields the selected graph
  needs are named, and an unready door or rung is refused with
  `capacity_not_ready` naming the field and its recorded reason.
* **Two-horizon freshness (D-106).** The planning window and per-field freshness
  are checked independently. A required field past its `fresh_until` is never
  described as fresh: it is refused with `capacity_field_stale`, or — when the
  caller supplies a door to re-observe with — refreshed into a new immutable
  VALIDATION snapshot that is bound alongside the proposal's planning snapshot,
  which is never rewritten.
* **Budgets are compared.** `expected` against the cited envelope's
  `constraints`, and against the declared context budget of every rung the route
  names (`over_budget_context`, `over_budget_attempts`).
* **Contracts are gated.** `contract_version` is check zero, the schema is
  enforced, and a malformed proposal returns a rejected verdict with a reason
  code instead of raising KeyError out of the validator.
* **Approval is derived, never asserted.** The caller-supplied `approved_ids`
  set is gone. A human-gated authority passes only when a canonical approval
  record that re-hashes carries a verified decision receipt from a human-class
  principal holding `approve` (D-112 item 5).

Every failed or needs_approval row carries a reason code AND a remedy.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from hearth.operator import (approve as approve_mod, authority as authority_mod, canonical,
                             envelope as envelope_mod, history, identity as identity_mod,
                             inspection, paths, proposal as proposal_mod, work_items)
from hearth.operator.identity import Identity

CONTRACT_VERSION = "validation-result.v1"
PROPOSAL_CONTRACT_VERSION = "route-proposal.v1"
FORBIDDEN_REASONING_KEYS = frozenset({"thinking", "reasoning", "chain_of_thought"})

# The closed rejection enum a PROPOSAL may cite for a route it rejected. It is
# the orchestrator's vocabulary, enforced by route-proposal.v1; `not_applicable`
# is the only member the validator never emits, because "this route did not
# apply" is a statement about an alternative, not a verdict on the selected one.
PROPOSAL_REASON_CODES = frozenset(proposal_mod.REASON_CODES)

# What the VALIDATOR itself can emit. Every member has an emitter below, and
# `hearth/tests/operator/test_regression_budget_deadline.py` proves it: a
# declared code with no emitter is a promise the record cannot keep (WI-G2b
# condition 4).
VALIDATOR_REASON_CODES = frozenset({
    "capacity_not_ready", "snapshot_expired", "snapshot_invalidated", "catalog_mismatch",
    "over_budget_context", "over_budget_attempts", "authority_missing", "human_required",
    "lane_absent", "implementation_not_live", "policy_block", "cost_over_budget",
    "unknown_contract_version", "contract_invalid", "missing_required_field",
    "proposal_identity_mismatch", "reasoning_present", "capacity_field_stale",
    "capacity_changed", "capacity_unavailable", "envelope_unresolved",
    "envelope_mismatch", "test_mode_invalid", "approval_unverified",
    "cost_unknown", "deadline_exceeded", "proposal_superseded",
})

# The codes a validation check may carry at all.
VALIDATION_REASON_CODES = PROPOSAL_REASON_CODES | VALIDATOR_REASON_CODES

REQUIRED_PROPOSAL_FIELDS = (
    "contract_version", "proposal_id", "envelope_id", "catalog_version", "snapshot_id",
    "snapshot_expires_at", "orchestrator", "policy_version", "eligible_routes",
    "rejected_routes", "selected_graph", "assumptions", "uncertainty", "expected",
    "required_authority", "required_approvals", "rationale", "mode",
)

# A conservative floor: whatever the tokenizer, one token is at least one byte,
# so a context in tokens larger than the rung's declared context in BYTES is
# over budget under every tokenization. Stated in the reason, never hidden.
MIN_BYTES_PER_TOKEN = 1

IDENTITY_RE = re.compile(r"^[0-9a-f]{64}$")

# CANONICAL UNITS (WI-G2b condition 4). Two budgets are compared here, and both
# are compared in integers so that "equal to the limit" is a fact and not a
# floating-point opinion:
#
#   * MONEY. The wire form is the string "USD <amount>" with at most six decimal
#     places, in the envelope's `constraints.budget` and in a route's
#     `estimated_cost` (both enforced by their schemas). It is compared as an
#     integer number of MICRO-USD, 1 USD = 1,000,000 µUSD. `null` means the cost
#     or the budget is NOT DECLARED, which is never read as zero.
#   * TIME. Integer seconds. The deadline is compared against elapsed time —
#     `now` minus the envelope's `submitted_at`, floored at zero — plus the
#     proposal's `expected.time_s`, because a deadline that ignores the time
#     already spent is not a deadline.
MONEY_RE = re.compile(r"^USD (0|[1-9][0-9]*)(?:\.([0-9]{1,6}))?$")
MICRO_USD_PER_USD = 1_000_000


def _micro_usd(text: Any) -> tuple[Optional[int], Optional[str]]:
    """`"USD 1.25"` -> 1_250_000 µUSD, or (None, why it is not comparable)."""
    if not isinstance(text, str):
        return None, (f"a monetary amount is the string 'USD <amount>', got "
                      f"{type(text).__name__}")
    match = MONEY_RE.match(text)
    if not match:
        return None, (f"{text!r} is not the canonical monetary form 'USD <amount>' with at "
                      "most six decimal places")
    whole, fraction = match.group(1), (match.group(2) or "")
    micro = int(whole) * MICRO_USD_PER_USD + int((fraction + "000000")[:6])
    return micro, None


def _usd(micro: int) -> str:
    """A µUSD integer back in the canonical wire form, for the record."""
    return f"USD {micro // MICRO_USD_PER_USD}.{micro % MICRO_USD_PER_USD:06d}"


class ValidationError(ValueError):
    """Raised when validation execution itself encounters fatal errors."""


def _has_reasoning_keys(data: Any) -> bool:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in FORBIDDEN_REASONING_KEYS or _has_reasoning_keys(value):
                return True
    elif isinstance(data, list):
        return any(_has_reasoning_keys(item) for item in data)
    return False


class _Checks:
    """The check rows of one validation, each with a code and a remedy."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.reasons: list[str] = []

    def passed(self, check_id: str, reason: str) -> None:
        self.rows.append({"check_id": check_id, "result": "passed",
                          "reason_code": None, "reason": reason, "remedy": None})

    def failed(self, check_id: str, code: str, reason: str, remedy: str) -> None:
        assert code in VALIDATION_REASON_CODES, code
        self.rows.append({"check_id": check_id, "result": "failed",
                          "reason_code": code, "reason": reason, "remedy": remedy})
        self.reasons.append(f"{code}: {reason}")

    def gated(self, check_id: str, code: str, reason: str, remedy: str) -> None:
        assert code in VALIDATION_REASON_CODES, code
        self.rows.append({"check_id": check_id, "result": "needs_approval",
                          "reason_code": code, "reason": reason, "remedy": remedy})
        self.reasons.append(f"{code}: {reason}")

    @property
    def verdict(self) -> str:
        if any(row["result"] == "failed" for row in self.rows):
            return "rejected"
        if any(row["result"] == "needs_approval" for row in self.rows):
            return "needs_approval"
        return "validated"


def _empty_capacity() -> dict:
    return {"door_reachable": None, "door_reason": None, "rungs": {},
            "required_fields": [], "stale_fields": [], "observed_at": None}


def _result(proposal: dict, checks: _Checks, *, now_dt: datetime, mode: str,
            capacity: dict, catalog_version: Optional[str], snapshot_id: Optional[str],
            validation_snapshot_id: Optional[str], expires_at: Optional[str],
            approvals: list[dict], work_item_policy: Optional[dict] = None) -> dict:
    document = {
        "contract_version": CONTRACT_VERSION,
        "proposal_id": proposal.get("proposal_id") if isinstance(proposal, dict) else None,
        "validated_at": canonical.rfc3339(now_dt),
        "expires_at": expires_at,
        "mode": mode,
        "test_mode": mode == "test",
        "catalog_version": catalog_version,
        "snapshot_id": snapshot_id,
        "validation_snapshot_id": validation_snapshot_id,
        # WHICH work-item register judged this run (D-113, WI-G2b condition 3).
        # Null in normal mode: there is no work item to judge against, and a
        # digest recorded where nothing was checked would be decoration.
        "work_item_policy": work_item_policy,
        "capacity": capacity,
        "approvals": approvals,
        "checks": checks.rows,
        "verdict": checks.verdict,
        "reasons": checks.reasons,
        "proposal_copy": copy.deepcopy(proposal) if isinstance(proposal, dict) else {},
    }
    for field in ("proposal_id", "catalog_version", "snapshot_id"):
        value = document[field]
        if not isinstance(value, str) or not IDENTITY_RE.match(value):
            # A refusal can name no identity the document did not carry. Null is
            # the honest answer; inventing one would make the refusal unciteable.
            document[field] = None
    stamped = canonical.stamp_identity(document, "validation_id")
    return canonical.validate_contract(stamped, CONTRACT_VERSION, label="validation result")


def _field(snapshot: dict, path: str) -> Optional[dict]:
    node: Any = snapshot
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) and "fresh_until" in node else None


def _expected_cost(proposal: dict) -> tuple[Optional[int], list[str]]:
    """The selected graph's expected monetary cost in µUSD, or why it is unknown.

    Each selected node's cost is the `estimated_cost` of the eligible route that
    names it. A node with no matching route row, or a row that declares `null`,
    makes the TOTAL unknown — never a zero.
    """
    by_route_id: dict[str, dict] = {}
    by_target: dict[str, dict] = {}
    for row in proposal.get("eligible_routes") or []:
        by_route_id.setdefault(str(row.get("route_id")), row)
        by_target.setdefault(str(row.get("target")), row)

    total = 0
    unknown: list[str] = []
    for node in (proposal.get("selected_graph") or {}).get("nodes", []):
        target = str(node.get("target"))
        row = by_route_id.get(target) or by_target.get(target)
        if row is None:
            unknown.append(f"node {node.get('id')} routes onto {target}, which no eligible "
                           "route declares a cost for")
            continue
        raw = row.get("estimated_cost")
        if raw is None:
            unknown.append(f"node {node.get('id')} ({target}) declares estimated_cost null")
            continue
        micro, why = _micro_usd(raw)
        if micro is None:
            unknown.append(f"node {node.get('id')} ({target}): {why}")
            continue
        total += micro
    return (None if unknown else total), unknown


def _known_lanes(catalog: dict) -> set[str]:
    """Every id the catalog declares that a route may name as a target or a
    resource: rungs, hosts, loop implementations and deterministic tools."""
    lanes = {str(rung["id"]) for rung in catalog.get("rungs", [])}
    lanes |= {str(host["id"]) for host in catalog.get("hosts", [])}
    lanes |= {str(tool["id"]) for tool in catalog.get("tools", [])}
    for loop in catalog.get("loops", []):
        lanes.add(str(loop.get("id")))
        lanes |= {str(impl["id"]) for impl in loop.get("implementations", [])}
    return lanes


def _required_capacity(proposal: dict, catalog: dict) -> tuple[list[str], bool]:
    """The rungs the selected graph needs, and whether it needs the door."""
    rung_ids = {str(rung["id"]) for rung in catalog.get("rungs", [])}
    entrypoints = {}
    for loop in catalog.get("loops", []):
        for impl in loop.get("implementations", []):
            entrypoints[str(impl["id"])] = str(impl.get("entrypoint", ""))

    rungs = {str(name) for name in (proposal.get("expected") or {}).get("resources", [])
             if str(name) in rung_ids}
    needs_door = False
    for node in (proposal.get("selected_graph") or {}).get("nodes", []):
        target = str(node.get("target", ""))
        if target in rung_ids:
            rungs.add(target)
        if entrypoints.get(target, "").startswith("door:"):
            needs_door = True
    return sorted(rungs), needs_door


def _capacity_facts(snapshot: dict, required_rungs: list[str], needs_door: bool,
                    catalog: dict, now_dt: datetime) -> dict:
    budgets = {str(rung["id"]): rung.get("context_bytes")
               for rung in catalog.get("rungs", [])}
    door_field = _field(snapshot, "door.reachable")
    facts = {
        "door_reachable": door_field["value"] if door_field else None,
        "door_reason": (door_field.get("reason") if door_field else
                        "the snapshot carries no door.reachable field"),
        "rungs": {},
        "required_fields": [],
        "stale_fields": [],
        "observed_at": str(snapshot.get("observed_at")) if snapshot.get("observed_at") else None,
    }
    if needs_door:
        facts["required_fields"].append("door.reachable")
        if door_field and now_dt > canonical.parse_rfc3339(str(door_field["fresh_until"])):
            facts["stale_fields"].append("door.reachable")

    for rung_id in required_rungs:
        field = _field(snapshot, f"rungs.{rung_id}.ready")
        path = f"rungs.{rung_id}.ready"
        facts["required_fields"].append(path)
        if field is None:
            facts["rungs"][rung_id] = {
                "ready": None, "reason": "the snapshot does not describe this rung",
                "fresh_until": canonical.rfc3339(now_dt), "fresh": False,
                "context_bytes": budgets.get(rung_id)}
            facts["stale_fields"].append(path)
            continue
        fresh = now_dt <= canonical.parse_rfc3339(str(field["fresh_until"]))
        facts["rungs"][rung_id] = {
            "ready": field["value"] if isinstance(field["value"], bool) else None,
            "reason": field.get("reason"),
            "fresh_until": str(field["fresh_until"]),
            "fresh": fresh,
            "context_bytes": budgets.get(rung_id),
        }
        if not fresh:
            facts["stale_fields"].append(path)
    return facts


def _freshness_horizon(snapshot: dict, facts: dict) -> Optional[str]:
    horizons = []
    for path in facts["required_fields"]:
        field = _field(snapshot, path)
        if field:
            horizons.append(str(field["fresh_until"]))
    planning = snapshot.get("planning_valid_until")
    if planning:
        horizons.append(str(planning))
    return min(horizons) if horizons else None


def _unverifiable_approval(run_id: str, proposal_id: str) -> Optional[str]:
    """Why an approval record filed for this proposal cannot be believed, if any.

    Deliberately narrow: a record that is merely PENDING is not unverifiable, it
    is undecided, and reporting the two the same way is how "the decision on
    file is forged" reads as "waiting for a human".
    """
    refs = paths.run_refs_dir(run_id)
    if not refs.is_dir():
        return None
    for target in sorted(refs.glob("approval_*.json")):
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except ValueError as exc:
            return f"{target.name} is not valid JSON ({exc})"
        if str(document.get("proposal_id")) != proposal_id:
            continue
        approval_id = target.stem.split("approval_", 1)[1]
        try:
            record = approve_mod.load_approval(run_id, approval_id)
        except approve_mod.ApprovalError as exc:
            return str(exc)
        if record.get("receipt") is not None:
            ok, reason = approve_mod.verify_receipt(record)
            if not ok:
                return reason
    return None


def _resolve_envelope(proposal: dict, envelope: Optional[dict],
                      run_id: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    if envelope is not None:
        return envelope, None
    if not run_id:
        return None, ("no run id was given, so the cited envelope could not be resolved "
                      "and the proposal's budgets could not be compared")
    target = paths.run_refs_dir(run_id) / "envelope.json"
    if not target.is_file():
        return None, f"no envelope on file for run {run_id} at {paths.repo_relative(target)}"
    try:
        return envelope_mod.load_envelope(target), None
    except envelope_mod.EnvelopeError as exc:
        return None, str(exc)


def validate_proposal(
    proposal: dict,
    caller_identity: Identity,
    *,
    catalog: Optional[dict] = None,
    current_snapshot: Optional[dict] = None,
    now: Optional[datetime] = None,
    run_id: Optional[str] = None,
    envelope: Optional[dict] = None,
    refresh_door: Optional[Callable] = None,
    refresh_cli_runner: Optional[Callable] = None,
) -> dict:
    """Validate a route proposal against capacity, catalog, policy and approval.

    Pure with respect to the proposal. When a required capacity field is past
    its `fresh_until` and `refresh_door` is supplied, a new immutable validation
    snapshot is captured and written to the snapshot store (content-addressed,
    written once); the proposal's own planning snapshot is never rewritten.
    """
    now_dt = now or canonical.utc_now()
    checks = _Checks()
    approvals: list[dict] = []

    # 0. Contract gate. An unknown contract version is refused before anything
    #    in the document is believed.
    if not isinstance(proposal, dict):
        checks.failed("proposal_contract", "contract_invalid",
                      f"a route proposal is an object, got {type(proposal).__name__}",
                      "submit a route-proposal.v1 document")
        return _result({}, checks, now_dt=now_dt, mode="normal", capacity=_empty_capacity(),
                       catalog_version=None, snapshot_id=None, validation_snapshot_id=None,
                       expires_at=None, approvals=approvals)

    mode = proposal.get("mode") if proposal.get("mode") in ("normal", "test") else "normal"
    declared_version = proposal.get("contract_version")
    if declared_version != PROPOSAL_CONTRACT_VERSION:
        checks.failed("proposal_contract", "unknown_contract_version",
                      f"proposal declares contract_version {declared_version!r}; this "
                      f"control plane validates {PROPOSAL_CONTRACT_VERSION} only",
                      f"re-issue the proposal under {PROPOSAL_CONTRACT_VERSION}")
        return _result(proposal, checks, now_dt=now_dt, mode=mode, capacity=_empty_capacity(),
                       catalog_version=None, snapshot_id=None, validation_snapshot_id=None,
                       expires_at=None, approvals=approvals)

    missing = [name for name in REQUIRED_PROPOSAL_FIELDS if name not in proposal]
    if missing:
        checks.failed("proposal_fields", "missing_required_field",
                      f"proposal is missing required field(s): {', '.join(missing)}",
                      "rebuild the proposal with `operator route draft` or supply the "
                      "missing fields; a validator never guesses a missing field")
        return _result(proposal, checks, now_dt=now_dt, mode=mode, capacity=_empty_capacity(),
                       catalog_version=None, snapshot_id=None, validation_snapshot_id=None,
                       expires_at=None, approvals=approvals)

    try:
        canonical.validate_contract(proposal, PROPOSAL_CONTRACT_VERSION, label="proposal")
        checks.passed("proposal_contract",
                      f"proposal validates against {PROPOSAL_CONTRACT_VERSION}")
    except canonical.CanonicalError as exc:
        checks.failed("proposal_contract", "contract_invalid", str(exc),
                      "correct the proposal against hearth/contracts/"
                      "route-proposal.v1.schema.json and re-propose")
        return _result(proposal, checks, now_dt=now_dt, mode=mode, capacity=_empty_capacity(),
                       catalog_version=None, snapshot_id=None, validation_snapshot_id=None,
                       expires_at=None, approvals=approvals)

    # 1. D-115 reasoning fields.
    if _has_reasoning_keys(proposal):
        checks.failed("no_reasoning_fields", "reasoning_present",
                      "proposal contains prohibited reasoning, thinking, or "
                      "chain_of_thought fields (D-115)",
                      "remove the reasoning field; hidden reasoning is never stored")
    else:
        checks.passed("no_reasoning_fields", "no prohibited reasoning fields found")

    # 2. Identity integrity.
    computed_pid = canonical.identity_of(proposal, "proposal_id")
    if computed_pid != proposal["proposal_id"]:
        checks.failed("proposal_identity_integrity", "proposal_identity_mismatch",
                      f"proposal_id tampered: declared {proposal['proposal_id']}, "
                      f"computed {computed_pid}",
                      "re-stamp the proposal from its content (`operator verify-ids`)")
    else:
        checks.passed("proposal_identity_integrity", "proposal_id matches computed identity")

    # 3. Catalog.
    cat = catalog
    if cat is None:
        from hearth.operator import core

        cat = core.catalog_document()
    live_catalog_version = str(cat["catalog_version"])
    if proposal["catalog_version"] != live_catalog_version:
        checks.failed("catalog_freshness", "catalog_mismatch",
                      f"catalog_mismatch: proposal cited {proposal['catalog_version']}, "
                      f"live is {live_catalog_version}",
                      "recompile (`operator catalog`) and re-propose against the live "
                      "catalog version")
    else:
        checks.passed("catalog_freshness", "catalog version matches the live catalog")

    # 4. Planning window (D-106 horizon one).
    try:
        expires_at_dt = canonical.parse_rfc3339(str(proposal["snapshot_expires_at"]))
        expired = now_dt > expires_at_dt
    except canonical.CanonicalError:
        expired = True
    if expired:
        checks.failed("snapshot_freshness", "snapshot_expired",
                      f"snapshot_expired: planning window expired at "
                      f"{proposal['snapshot_expires_at']}",
                      "`operator inspect --refresh`, then re-propose against the new "
                      "planning snapshot")
    else:
        checks.passed("snapshot_freshness", "snapshot planning window is unexpired")

    # 4b. Supersession of the PROPOSAL itself (D-112 item 4). A superseded
    #     proposal is withdrawn: re-validating it would revive a route the
    #     orchestrator replaced.
    superseded_by = None
    if run_id:
        try:
            superseded_by = approve_mod.supersession_event(run_id, str(proposal["proposal_id"]))
        except history.HistoryError as exc:
            checks.failed("proposal_supersession", "contract_invalid",
                          f"the run history could not be read to check supersession: {exc}",
                          "`operator history <run_id> --verify` and reconcile the damage "
                          "before validating against this run")
    if superseded_by is not None:
        successor = str(superseded_by["payload"].get("successor_proposal_id", "unknown"))
        checks.failed("proposal_supersession", "proposal_superseded",
                      f"proposal {str(proposal['proposal_id'])[:16]} was superseded by "
                      f"{successor[:16]} at sequence {superseded_by.get('sequence')}",
                      f"validate the successor proposal {successor[:16]}; a superseded "
                      "proposal is withdrawn, and its approvals ended with it "
                      "(D-112 item 4)")
    else:
        checks.passed("proposal_supersession", "no later proposal supersedes this one")

    # 5. Supersession of the cited snapshot.
    invalidated = next(
        (event for event in history.read_all(paths.history_path())
         if event.get("event_type") == "snapshot.invalidated"
         and event.get("payload", {}).get("snapshot_id") == proposal["snapshot_id"]), None)
    if invalidated:
        changed = ", ".join(invalidated["payload"].get("changed_fields", [])) or "capacity"
        checks.failed("snapshot_validity", "snapshot_invalidated",
                      f"snapshot_invalidated: snapshot {proposal['snapshot_id']} was "
                      f"superseded by a material change in {changed}",
                      "re-propose against the successor snapshot "
                      f"{invalidated['payload'].get('successor_snapshot_id', '(see history)')}")
    else:
        checks.passed("snapshot_validity", "snapshot has not been invalidated")

    # 6. Capacity (D-106 horizon two): read the snapshot, name the fields.
    required_rungs, needs_door = _required_capacity(proposal, cat)
    snapshot = current_snapshot
    snapshot_source = "caller"
    if snapshot is None:
        try:
            snapshot = inspection.read_current(now=now_dt, require_valid=False)["snapshot"]
            snapshot_source = "CURRENT.json"
        except inspection.InspectError as exc:
            snapshot = None
            checks.failed("capacity_available", "capacity_unavailable",
                          f"no capacity snapshot could be read: {exc}",
                          "`operator inspect --refresh` to observe capacity, then validate again")

    capacity = _empty_capacity()
    validation_snapshot_id = None
    horizon = None
    if snapshot is not None:
        capacity = _capacity_facts(snapshot, required_rungs, needs_door, cat, now_dt)
        if capacity["stale_fields"] and refresh_door is not None:
            refreshed = inspection.capture(cat, door=refresh_door,
                                           cli_runner=refresh_cli_runner, now=now_dt)
            inspection.write_snapshot(refreshed)
            validation_snapshot_id = str(refreshed["snapshot_id"])
            # Freshness of a just-observed snapshot is judged at the moment it was
            # observed. Every probe stamps its own `observed_at` from the observing
            # process's clock, so measuring a re-observation against a caller's
            # injected clock would call a fact stale in the instant it was taken.
            capacity = _capacity_facts(refreshed, required_rungs, needs_door, cat,
                                       canonical.utc_now())
            snapshot = refreshed
            snapshot_source = "validation snapshot (D-106 refresh)"
            checks.passed("capacity_freshness",
                          f"required fields re-observed into validation snapshot "
                          f"{validation_snapshot_id}")
        if capacity["stale_fields"]:
            checks.failed(
                "capacity_freshness", "capacity_field_stale",
                "required capacity field(s) past fresh_until: "
                + ", ".join(capacity["stale_fields"])
                + f" (read from {snapshot_source} at {canonical.rfc3339(now_dt)})",
                "`operator inspect --refresh` and re-validate, or validate with a door "
                "to re-observe the required fields into a bound validation snapshot "
                "(D-106); a field the record calls stale is never treated as fresh")
        elif not capacity["stale_fields"] and validation_snapshot_id is None:
            checks.passed("capacity_freshness",
                          "every required capacity field is inside its freshness horizon")

        if needs_door and capacity["door_reachable"] is not True:
            checks.failed("capacity_door", "capacity_not_ready",
                          "the selected route runs through the door, and the snapshot "
                          f"records door.reachable={capacity['door_reachable']!r} "
                          f"({capacity['door_reason'] or 'no reason recorded'})",
                          "bring the gateway up (`/checkmcp`) and re-observe capacity "
                          "before validating this route")
        elif needs_door:
            checks.passed("capacity_door", "the door is reachable in the snapshot read")

        # Material change between the snapshot the orchestrator planned against
        # and the one this validation read. Eligibility is judged against what
        # was READ either way; this check says whether the world moved under the
        # proposal, which is a different fact and belongs on the record.
        cited_id = str(proposal["snapshot_id"])
        if str(snapshot.get("snapshot_id")) != cited_id:
            cited_path = inspection.snapshot_path(cited_id)
            if cited_path.is_file():
                changed = inspection.material_changes(
                    inspection.load_snapshot(cited_path), snapshot)
                if changed:
                    checks.failed(
                        "capacity_changed", "capacity_changed",
                        f"capacity moved since the proposal's planning snapshot "
                        f"{cited_id[:16]}: {', '.join(changed)} differ in the snapshot "
                        f"read ({str(snapshot.get('snapshot_id'))[:16]})",
                        "re-propose against the current planning snapshot; the "
                        "orchestrator chose this route against capacity that no longer "
                        "holds")
                else:
                    checks.passed("capacity_changed",
                                  "no material field differs between the cited planning "
                                  "snapshot and the snapshot read")
            else:
                checks.passed(
                    "capacity_changed",
                    f"the cited planning snapshot {cited_id[:16]} is not on file, so no "
                    "field-level comparison was possible; eligibility was judged against "
                    f"the snapshot read ({str(snapshot.get('snapshot_id'))[:16]})")

        for rung_id in required_rungs:
            row = capacity["rungs"].get(rung_id, {})
            check_id = f"capacity_rung_{rung_id}"
            if row.get("ready") is not True:
                checks.failed(check_id, "capacity_not_ready",
                              f"rung {rung_id} is recorded ready={row.get('ready')!r} "
                              f"({row.get('reason') or 'no reason recorded'})",
                              f"make {rung_id} ready, or propose a route onto a rung the "
                              "snapshot records ready")
            else:
                checks.passed(check_id, f"rung {rung_id} is ready in the snapshot read")
        horizon = _freshness_horizon(snapshot, capacity)

    # 7. Budgets, against the cited envelope and the rungs' declared budgets.
    resolved_envelope, envelope_reason = _resolve_envelope(proposal, envelope, run_id)
    if resolved_envelope is None:
        checks.failed("budget_envelope", "envelope_unresolved",
                      f"the proposal's budgets could not be compared: {envelope_reason}",
                      "submit the task envelope first (`operator task submit`) or pass "
                      "the envelope explicitly; an uncompared budget is not a budget")
    elif str(resolved_envelope.get("envelope_id")) != str(proposal["envelope_id"]):
        checks.failed("budget_envelope", "envelope_mismatch",
                      f"the proposal cites envelope {proposal['envelope_id']}, but run "
                      f"{run_id} holds {resolved_envelope.get('envelope_id')}",
                      "re-propose against the envelope this run actually froze")
    else:
        constraints = resolved_envelope["constraints"]
        expected = proposal["expected"]
        if int(expected["context_tokens"]) > int(constraints["max_context_tokens"]):
            checks.failed("budget_context", "over_budget_context",
                          f"expected.context_tokens {expected['context_tokens']} exceeds the "
                          f"envelope's max_context_tokens {constraints['max_context_tokens']}",
                          "narrow the route's context or raise the envelope's constraint "
                          "in a superseding envelope")
        else:
            checks.passed("budget_context",
                          f"expected.context_tokens {expected['context_tokens']} is within "
                          f"the envelope's {constraints['max_context_tokens']}")
        if int(expected["attempts"]) > int(constraints["max_attempts"]):
            checks.failed("budget_attempts", "over_budget_attempts",
                          f"expected.attempts {expected['attempts']} exceeds the envelope's "
                          f"max_attempts {constraints['max_attempts']}",
                          "reduce the attempt budget or supersede the envelope")
        else:
            checks.passed("budget_attempts",
                          f"expected.attempts {expected['attempts']} is within the "
                          f"envelope's {constraints['max_attempts']}")

        # 7b. MONEY (WI-G2b condition 4). Integer µUSD, so the equal case is
        #     exact; an undeclared cost against a finite budget is a refusal.
        budget_text = constraints.get("budget")
        cost_micro, cost_unknown = _expected_cost(proposal)
        if budget_text is None:
            checks.passed("budget_cost",
                          "the envelope declares no monetary budget, so there is nothing "
                          "to compare; an absent budget is not a budget of zero")
        else:
            budget_micro, why = _micro_usd(budget_text)
            if budget_micro is None:
                checks.failed("budget_cost", "cost_over_budget",
                              f"the envelope's budget {budget_text!r} cannot be compared: "
                              f"{why}",
                              "state the budget in the canonical form 'USD <amount>' (at "
                              "most six decimals); an uncomparable budget is not a budget")
            elif cost_micro is None:
                checks.failed("budget_cost", "cost_unknown",
                              f"the envelope sets a budget of {budget_text} but the "
                              "expected monetary cost is not declared: "
                              + "; ".join(cost_unknown)
                              + ". An undeclared cost is not a zero cost",
                              "declare estimated_cost for every selected route in the "
                              "canonical form 'USD <amount>', or remove the monetary "
                              "budget from the envelope and say so")
            elif cost_micro > budget_micro:
                checks.failed("budget_cost", "cost_over_budget",
                              f"expected monetary cost {_usd(cost_micro)} "
                              f"({cost_micro} microUSD) exceeds the envelope's budget "
                              f"{budget_text} ({budget_micro} microUSD)",
                              "choose a cheaper route, or raise the budget in a "
                              "superseding envelope")
            else:
                checks.passed("budget_cost",
                              f"expected monetary cost {_usd(cost_micro)} is within the "
                              f"envelope's budget {budget_text} "
                              f"({cost_micro} of {budget_micro} microUSD)")

        # 7c. TIME. Elapsed plus expected, in whole seconds, against the deadline.
        deadline_s = int(constraints["deadline_s"])
        expected_s = int(expected["time_s"])
        try:
            submitted_at = canonical.parse_rfc3339(str(resolved_envelope["submitted_at"]))
            elapsed_s = max(0, int((now_dt - submitted_at).total_seconds()))
        except (canonical.CanonicalError, KeyError, TypeError, ValueError) as exc:
            checks.failed("budget_deadline", "contract_invalid",
                          f"the cited envelope's submitted_at is not a readable timestamp "
                          f"({exc}), so the deadline cannot be compared",
                          "re-submit the task envelope; an uncompared deadline is not a "
                          "deadline")
        else:
            total_s = elapsed_s + expected_s
            if total_s > deadline_s:
                checks.failed("budget_deadline", "deadline_exceeded",
                              f"{elapsed_s} s elapsed since the envelope was submitted plus "
                              f"{expected_s} s expected for this route is {total_s} s, over "
                              f"the envelope's deadline_s of {deadline_s} s",
                              "choose a faster route, or supersede the envelope with a "
                              "deadline that accounts for the time already spent")
            else:
                checks.passed("budget_deadline",
                              f"{elapsed_s} s elapsed plus {expected_s} s expected is "
                              f"{total_s} s, within the envelope's deadline_s of "
                              f"{deadline_s} s")

    for rung_id in required_rungs:
        budget = capacity["rungs"].get(rung_id, {}).get("context_bytes")
        if budget is None:
            continue
        tokens = int(proposal["expected"]["context_tokens"])
        if tokens * MIN_BYTES_PER_TOKEN > int(budget):
            checks.failed(f"budget_rung_{rung_id}", "over_budget_context",
                          f"expected.context_tokens {tokens} exceeds rung {rung_id}'s "
                          f"declared context budget of {budget} bytes (at least one byte "
                          "per token under any tokenization)",
                          f"pack less context, or route onto a rung whose declared budget "
                          "holds this payload; the door refuses an over-budget pin rather "
                          "than truncating it (ADR-0031)")
        else:
            checks.passed(f"budget_rung_{rung_id}",
                          f"expected.context_tokens {tokens} fits rung {rung_id}'s declared "
                          f"{budget}-byte budget")

    # 7d. Lanes that do not exist (WI-G2b condition 4). A resource the catalog
    #     does not declare was silently dropped from the capacity check, so a
    #     route could name a rung that does not exist and be judged on the rungs
    #     that do.
    lanes = _known_lanes(cat)
    absent_resources = sorted({str(name) for name in
                               (proposal.get("expected") or {}).get("resources", [])}
                              - lanes)
    if absent_resources:
        checks.failed("lane_present", "lane_absent",
                      "expected.resources names lane(s) catalog "
                      f"{live_catalog_version[:16]} does not declare: "
                      + ", ".join(absent_resources),
                      "name a rung, host, loop, implementation or tool the catalog "
                      "declares, or recompile the catalog if the lane is new; a resource "
                      "nobody declares cannot be checked for capacity")
    else:
        checks.passed("lane_present",
                      "every resource the route names is a lane the catalog declares")

    # 8. Catalog lifecycle, and the ONLY thing test mode relaxes.
    test_targets: set[str] = set()
    if mode == "test":
        test_targets = {str(name) for name in
                        (proposal.get("test_mode_fields") or {}).get("test_targets", [])}
    statuses = {}
    for loop in cat.get("loops", []):
        for impl in loop.get("implementations", []):
            statuses[str(impl["id"])] = str(impl.get("status", "ABSENT"))
    testable = set(str(name) for name in
                   paths.operator_config().get("test_mode", {}).get("testable_statuses", ()))

    for node in proposal["selected_graph"]["nodes"]:
        target = str(node.get("target"))
        check_id = f"implementation_status_{target}"
        if target not in lanes:
            # Not "not live" — not there at all. The two are different answers
            # and the remedies are different.
            checks.failed(f"lane_{target}", "lane_absent",
                          f"node {node.get('id')} routes onto {target}, which catalog "
                          f"{live_catalog_version[:16]} declares as no rung, host, loop, "
                          "implementation or tool",
                          "route onto a target the catalog declares, or recompile the "
                          "catalog if the lane is new")
            continue
        status = statuses.get(target, "LIVE" if target == "direct_hearth" else "ABSENT")
        if status == "LIVE":
            checks.passed(check_id, f"target {target} is LIVE")
        elif mode == "test" and target in test_targets and status in testable:
            checks.passed(check_id,
                          f"target {target} has status {status} and is named in "
                          "test_targets; test mode relaxes catalog lifecycle eligibility "
                          "for exactly these nodes (D-113)")
        else:
            checks.failed(check_id, "implementation_not_live",
                          f"implementation_not_live: target {target} has status {status} "
                          f"(requires LIVE in mode {mode}"
                          + ("; test mode relaxes lifecycle only for the exact test_targets)"
                             if mode == "test" else ")"),
                          "route onto a LIVE implementation, or submit a mode:test "
                          "proposal naming this target in test_targets under D-113")

    # 9. Test-mode companions, resolved and verified (D-113, presence is not enough).
    work_item_policy = None
    if mode == "test":
        work_item_policy = _check_test_mode(proposal, checks, statuses, testable)

    # 10. Authority, and approval derived from the authoritative decision path.
    authority_eval = authority_mod.evaluate(caller_identity, live_catalog_version)
    authorities = authority_eval.get("authorities", {})
    human_required = sorted(name for name in proposal["required_authority"]
                            if (authorities.get(name) or {}).get("result") == "human_required")
    node_ids = sorted(str(node["id"]) for node in proposal["selected_graph"]["nodes"])
    targets = sorted({str(node["target"]) for node in proposal["selected_graph"]["nodes"]})

    # A mode:test run is human-gated in its own right (D-113), even when every
    # authority it names is granted: the approval then binds the whole authority
    # set the run may use.
    approval_authorities = human_required or (
        sorted(set(map(str, proposal["required_authority"]))) if mode == "test" else [])

    found = {"record": None, "reason": "no human-gated authority was required"}
    if approval_authorities:
        if run_id:
            found = approve_mod.effective_approval(
                run_id,
                {"proposal_id": proposal["proposal_id"], "authorities": approval_authorities,
                 "node_ids": node_ids, "targets": targets,
                 "catalog_version": proposal["catalog_version"],
                 "snapshot_id": proposal["snapshot_id"]},
                now=now_dt)
        else:
            found = {"record": None,
                     "reason": "no run id was given, so no approval record could be read"}

    if mode == "test":
        if found["record"] is not None:
            checks.passed("test_mode_policy",
                          "the mode:test run carries a verified human-operator approval "
                          f"({found['record']['approval_id'][:16]})")
        else:
            checks.gated("test_mode_policy", "human_required",
                         "a mode:test proposal requires human-operator approval bound "
                         f"under D-112 ({found['reason']})",
                         "a human-operator principal approves the recorded request before "
                         "this route may run")

    # An approval record that is on file but cannot be believed is its own
    # answer: "no human has decided yet" and "the decision on file does not
    # verify" are different states, and the second one was silently reported as
    # the first (WI-G2b condition 4, the `approval_unverified` emitter).
    if approval_authorities and found["record"] is None and run_id:
        detail = _unverifiable_approval(run_id, str(proposal["proposal_id"]))
        if detail:
            checks.gated("approval_verification", "approval_unverified",
                         f"an approval record is on file for this proposal and does not "
                         f"verify: {detail}",
                         "a mutated record and a receipt that no longer authenticates can "
                         "never be repaired in place (D-112 item 3): request a new "
                         "approval and have a human decide it")

    if found["record"] is not None:
        receipt = found["record"]["receipt"]
        approvals.append({
            "approval_id": found["record"]["approval_id"],
            "receipt_id": receipt["receipt_id"],
            "authorities": list(found["record"]["authorities"]),
            "decided_by": receipt["approving_principal"]["id"],
            "expires_at": found["record"]["expires_at"],
        })

    for name in proposal["required_authority"]:
        decision = authorities.get(name)
        check_id = f"authority_{name}"
        if not decision:
            checks.failed(check_id, "authority_missing",
                          f"authority_missing: unknown authority {name}",
                          "name one of the nine authorities declared in "
                          "hearth/etc/authority-map.toml")
            continue
        result = decision["result"]
        if result == "denied":
            checks.failed(check_id, "authority_missing",
                          f"authority_missing: authority {name} is denied "
                          f"({decision['reason']})",
                          "an approval never adds a capability (G2-C7): the caller needs a "
                          "profile that holds it, which is a policy change, not an approval")
        elif result == "human_required":
            if found["record"] is not None:
                receipt = found["record"]["receipt"]
                checks.passed(check_id,
                              f"authority {name} is authorized by approval "
                              f"{found['record']['approval_id'][:16]} and decision receipt "
                              f"{receipt['receipt_id'][:16]}, decided by "
                              f"{receipt['approving_principal']['id']}")
            else:
                checks.gated(check_id, "human_required",
                             f"human_required: authority {name} requires a human decision "
                             f"({found['reason']})",
                             "a human-operator principal runs `operator approve <run_id> "
                             "<approval_id>`; storing this validation records the approval "
                             "request bound to it")
        else:
            checks.passed(check_id, f"authority {name} granted")

    expires_candidates = [value for value in
                          (horizon, proposal.get("snapshot_expires_at")) if value]
    if found["record"] is not None:
        expires_candidates.append(found["record"]["expires_at"])
    expires_at = min(expires_candidates) if expires_candidates else None

    return _result(proposal, checks, now_dt=now_dt, mode=mode, capacity=capacity,
                   catalog_version=proposal.get("catalog_version"),
                   snapshot_id=proposal.get("snapshot_id"),
                   validation_snapshot_id=validation_snapshot_id,
                   expires_at=expires_at, approvals=approvals,
                   work_item_policy=work_item_policy)


TEST_MODE_FIELDS = ("work_item", "isolated_inputs", "isolated_outputs", "base_commit",
                    "base_tree", "bounded_authorities", "rollback_ref",
                    "recovery_instructions", "test_targets")


def _inside_master(path_text: str) -> bool:
    """Is this path inside the control plane repository (D-113: isolation)?"""
    config = paths.operator_config()["catalog"]["worktree_policy"]
    master = Path(str(config["control_plane_repository"]))
    candidate = Path(str(path_text))
    if not candidate.is_absolute():
        return True  # a repo-relative path IS inside master by definition
    try:
        candidate.resolve().relative_to(master.resolve())
        return True
    except (ValueError, OSError):
        return False


def _check_test_mode(proposal: dict, checks: _Checks, statuses: dict,
                     testable: set[str]) -> Optional[dict]:
    """D-113 as amended: every companion RESOLVED and verified, never counted.

    Returns the work-item policy binding the validation result carries (the
    register's contract version, path and digest, plus the entry's own digest),
    or None when the register could not be read at all — in which case the check
    has already failed, because a mode:test proposal judged against no register
    is judged on shape.
    """
    fields = proposal.get("test_mode_fields") or {}
    missing = [name for name in TEST_MODE_FIELDS if not fields.get(name)]
    if missing:
        checks.failed("test_mode_fields", "test_mode_invalid",
                      "test mode proposal is missing or empties required field(s): "
                      + ", ".join(missing),
                      "supply every D-113 companion: an existing released work item, "
                      "isolated inputs and outputs outside master, an immutable base "
                      "commit and tree, a bounded authority list, a resolvable rollback "
                      "commit with recovery instructions, and explicit test_targets")
        return None

    declared_item = str(fields["work_item"])
    try:
        policy = work_items.load_policy()
    except work_items.WorkItemPolicyError as exc:
        checks.failed("test_mode_work_item", "test_mode_invalid",
                      f"the work-item register could not be read: {exc}",
                      "repair hearth/etc/work-items.toml (contract work-items.v1); a "
                      "mode:test proposal judged against no register is judged on shape")
        return None

    entry = work_items.find(policy, declared_item)
    if entry is None:
        checks.failed("test_mode_work_item", "test_mode_invalid",
                      f"work item {declared_item!r} is in no entry of "
                      f"{policy['path']} (declared: "
                      f"{', '.join(sorted(row['id'] for row in policy['work_items']))})",
                      "name an existing gate work item, or declare it in "
                      "hearth/etc/work-items.toml with its roots, its prohibitions and "
                      "its digest")
    elif entry["status"] != "released":
        checks.failed("test_mode_work_item", "test_mode_invalid",
                      f"work item {declared_item} is {entry['status']}, not released; "
                      "D-113 requires an existing, RELEASED gate-specific work item",
                      "release the work item (its gate decision) before a mode:test run "
                      "may name it")
    else:
        checks.passed("test_mode_work_item",
                      f"work item {declared_item} is released in {policy['path']} "
                      f"(entry digest {str(entry['digest'])[:16]})")

    binding = work_items.binding(policy, declared_item, entry)

    if entry is not None:
        master = Path(str(paths.operator_config()["catalog"]["worktree_policy"]
                          ["control_plane_repository"]))
        problems = []
        for key in ("isolated_inputs", "isolated_outputs"):
            for value in fields[key]:
                reason = work_items.check_isolated_path(entry, str(value), master=master)
                if reason:
                    problems.append(f"{key}: {reason}")
        if problems:
            checks.failed("test_mode_isolation", "test_mode_invalid",
                          "isolated paths are resolved, not assumed; " + "; ".join(problems),
                          f"point the run at a location inside {entry['id']}'s declared "
                          "roots that exists on this machine")
        else:
            checks.passed("test_mode_isolation",
                          f"every declared input and output exists, is outside the control "
                          f"plane repository, and is inside {entry['id']}'s permitted roots")

        for key, kind in (("base_commit", "commit"), ("base_tree", "tree"),
                          ("rollback_ref", "commit")):
            try:
                reason = work_items.resolve_object(str(entry["repository"]),
                                                   str(fields[key]), kind)
            except work_items.WorkItemPolicyError as exc:
                reason = str(exc)
            if reason:
                checks.failed(f"test_mode_{key}", "test_mode_invalid",
                              f"{key} does not resolve: {reason}",
                              "record the immutable commit/tree this run starts from and "
                              f"the commit it rolls back to, as they exist in "
                              f"{entry['repository']}")
            else:
                checks.passed(f"test_mode_{key}",
                              f"{key} {str(fields[key])[:12]} resolves to a {kind} in "
                              f"{entry['repository']}")

        requested = ({str(name) for name in proposal["required_authority"]}
                     | {str(name) for name in fields["bounded_authorities"]})
        forbidden = work_items.prohibited(entry, requested)
        if forbidden:
            checks.failed("test_mode_prohibited_authorities", "policy_block",
                          f"work item {entry['id']} prohibits {', '.join(forbidden)}, and "
                          "this proposal requests or bounds them",
                          "a prohibited authority is a policy denial, not a human-approvable "
                          f"gate: remove it, or change {entry['id']}'s declaration in "
                          "hearth/etc/work-items.toml, which is a policy change with its "
                          "own review")
        else:
            checks.passed("test_mode_prohibited_authorities",
                          f"no requested or bounded authority is prohibited by {entry['id']}")
    else:
        # Without an entry there are no roots, no repository and no prohibitions
        # to resolve against. Say so once rather than reporting shape checks that
        # would pass for the wrong reason.
        checks.failed("test_mode_isolation", "test_mode_invalid",
                      f"no register entry for {declared_item}, so its isolated paths, base "
                      "objects and prohibited authorities cannot be resolved",
                      "declare the work item in hearth/etc/work-items.toml")

    bounded = set(str(name) for name in fields["bounded_authorities"])
    required = set(str(name) for name in proposal["required_authority"])
    if not required <= bounded:
        checks.failed("test_mode_bounded_authorities", "test_mode_invalid",
                      "the proposal requires authority outside the bounded list: "
                      + ", ".join(sorted(required - bounded)),
                      "bounded_authorities is a MAXIMUM set, not a grant: every required "
                      "authority must be inside it and is still evaluated normally")
    else:
        checks.passed("test_mode_bounded_authorities",
                      "required authority is inside the bounded maximum set, and every "
                      "one of them is still evaluated normally")

    unknown = [name for name in fields["test_targets"] if str(name) not in statuses]
    not_testable = [name for name in fields["test_targets"]
                    if str(name) in statuses and statuses[str(name)] not in testable
                    and statuses[str(name)] != "LIVE"]
    if unknown or not_testable:
        checks.failed("test_mode_targets", "test_mode_invalid",
                      "test targets must exist in the catalog and be testable or "
                      "built-but-not-live; unknown: "
                      f"{', '.join(map(str, unknown)) or 'none'}; not testable: "
                      f"{', '.join(map(str, not_testable)) or 'none'}",
                      "name catalog implementations marked "
                      + " or ".join(sorted(testable)))
    else:
        checks.passed("test_mode_targets",
                      "every test target exists in the catalog and is testable")

    graph_targets = {str(node["target"]) for node in proposal["selected_graph"]["nodes"]}
    outside_targets = sorted(target for target in graph_targets
                             if statuses.get(target, "ABSENT") != "LIVE"
                             and target not in set(map(str, fields["test_targets"])))
    if outside_targets:
        checks.failed("test_mode_target_membership", "test_mode_invalid",
                      "the selected graph routes onto non-LIVE target(s) not named in "
                      f"test_targets: {', '.join(outside_targets)}",
                      "test mode relaxes catalog lifecycle for the EXACT test_targets only")
    else:
        checks.passed("test_mode_target_membership",
                      "every non-LIVE selected target is named in test_targets")
    return binding


def store_validation(validation: dict, run_id: str, *, requester: Identity) -> Path:
    """Store the validation result, record the event, and — when the verdict is
    needs_approval — record the bound approval REQUEST it refers to.

    The WI-G2 candidate appended a bare `approval.requested` with no approval id,
    which is why `explain` could not find the approval it had just asked for.

    `requester` is the identity that ran this validation, as the trusted boundary
    resolved it; the approval request binds THAT caller and nothing a caller can
    type (D-112 item 2, WI-G2b). It is checked here, before anything is written.
    """
    if not isinstance(requester, Identity) or not requester.attested:
        raise ValidationError(
            f"store_validation needs the identity the trusted boundary resolved for this "
            f"validation, not a {type(requester).__name__}: the requester of an approval "
            "is never caller-supplied data (D-112 item 2)")
    canonical.validate_contract(validation, CONTRACT_VERSION, label="validation result")
    computed = canonical.identity_of(validation, "validation_id")
    if validation["validation_id"] != computed:
        raise ValidationError(
            f"validation_id mismatch: declared {validation['validation_id']}, computed "
            f"{computed}; refusing to store a document whose identity does not match")

    validation_id = validation["validation_id"]
    refs_dir = paths.run_refs_dir(run_id)
    refs_dir.mkdir(parents=True, exist_ok=True)
    target = refs_dir / f"validation_{validation_id}.json"
    target.write_text(json.dumps(validation, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")

    test_mode = bool(validation["test_mode"])
    event_type = "route.rejected" if validation["verdict"] == "rejected" else "route.validated"
    history.append(
        event_type,
        {
            "validation_id": validation_id,
            "proposal_id": validation["proposal_id"],
            "verdict": validation["verdict"],
            "reason_codes": sorted({row["reason_code"] for row in validation["checks"]
                                    if row["reason_code"]}),
            "catalog_version": validation["catalog_version"],
            "snapshot_id": validation["snapshot_id"],
            "validation_snapshot_id": validation["validation_snapshot_id"],
            "expires_at": validation["expires_at"],
            "passed_checks": sum(1 for row in validation["checks"] if row["result"] == "passed"),
            "failed_checks": sum(1 for row in validation["checks"] if row["result"] == "failed"),
            "needs_approval_checks": sum(1 for row in validation["checks"]
                                         if row["result"] == "needs_approval"),
            "approval_ids": [row["approval_id"] for row in validation["approvals"]],
        },
        refs={"validation_path": paths.repo_relative(target)},
        run_id=run_id,
        test_mode=test_mode,
    )

    if validation["verdict"] == "needs_approval" and not validation["approvals"]:
        proposal = validation["proposal_copy"]
        gated = sorted({row["check_id"].removeprefix("authority_")
                        for row in validation["checks"]
                        if row["result"] == "needs_approval"
                        and row["check_id"].startswith("authority_")})
        if not gated and validation["mode"] == "test":
            gated = sorted(set(map(str, proposal.get("required_authority", []))))
        if gated:
            approve_mod.request_approval(
                run_id=run_id,
                proposal_id=validation["proposal_id"],
                validation_id=validation_id,
                node_ids=[str(node["id"])
                          for node in proposal["selected_graph"]["nodes"]],
                targets=sorted({str(node["target"])
                                for node in proposal["selected_graph"]["nodes"]}),
                authorities=gated,
                catalog_version=validation["catalog_version"],
                snapshot_id=validation["snapshot_id"],
                requester=identity_mod.authenticated_caller(requester),
                test_mode=test_mode,
            )
    return target


def invalidate_decision(run_id: str, proposal_id: str, reason: str,
                        *, test_mode: bool = False) -> dict:
    """Append `decision.invalidated` for one proposal (G2-C9)."""
    return history.append(
        "decision.invalidated",
        {"proposal_id": str(proposal_id), "reason": str(reason)},
        run_id=run_id,
        test_mode=test_mode,
    )


def supersede_decision(run_id: str, *, superseded_proposal_id: str,
                       successor_proposal_id: str, reason: str,
                       test_mode: bool = False) -> dict:
    """Append `decision.superseded` when a later proposal replaces an earlier one.

    Called automatically by `proposal.store_proposal` when an ingested proposal
    declares `supersedes` (WI-G2b condition 2). The payload names BOTH proposal
    ids; the transition is deterministic (the payload is a function of the two
    ids) and idempotent (the caller records it once per pair).
    """
    return history.append(
        "decision.superseded",
        {"proposal_id": str(superseded_proposal_id),
         "successor_proposal_id": str(successor_proposal_id),
         "reason": str(reason)},
        run_id=run_id,
        test_mode=test_mode,
    )


def _closes_decision(run_id: str, event: dict) -> bool:
    """Does this event CLOSE an open decision about its proposal?

    Only three things do: an invalidation, a supersession, and a human rejection
    that verifies (D-112 item 5). The WI-G2a candidate also counted
    `route.rejected` — any caller's deterministic validation rejection — which
    let one caller's authority refusal suppress the `decision.invalidated` owed
    to a decision that was still open. A validation rejection rejects that
    validation attempt; it does not answer the human's question, and it must not
    impersonate the answer (WI-G2b condition 2).
    """
    etype = event.get("event_type")
    if etype in ("decision.invalidated", "decision.superseded"):
        return True
    if etype != "human.decided" or event.get("payload", {}).get("decision") != "reject":
        return False
    verified, _reason = approve_mod.verify_decision_event(run_id, event)
    return verified


def invalidate_superseded_decisions(snapshot_id: str, successor_snapshot_id: str,
                                    changed_fields: list[str]) -> list[dict]:
    """Invalidate every open decision that cited a now-superseded snapshot.

    Called from `core.refresh` where the material change is already computed:
    G2-C9 asks for `decision.invalidated` referencing the affected proposal, not
    only for the indirect refusal a later re-validation would produce.
    """
    runs_dir = paths.runs_dir()
    if not runs_dir.is_dir():
        return []
    appended = []
    for run_path in sorted(runs_dir.iterdir()):
        if not run_path.is_dir() or run_path.name == history.SYSTEM_RUN_ID:
            continue
        run_id = run_path.name
        try:
            events = history.read_run_history(run_id)
        except history.HistoryError:
            continue  # a damaged run history is reported by `history --verify`, not rewritten
        open_decisions: dict[str, dict] = {}
        for event in events:
            payload = event.get("payload", {})
            etype = event.get("event_type")
            if etype == "route.validated" and payload.get("snapshot_id") == snapshot_id \
                    and payload.get("verdict") in ("validated", "needs_approval"):
                open_decisions[str(payload.get("proposal_id"))] = event
            elif _closes_decision(run_id, event):
                open_decisions.pop(str(payload.get("proposal_id")), None)
        for proposal_id, event in sorted(open_decisions.items()):
            appended.append(history.append(
                "decision.invalidated",
                {"proposal_id": proposal_id,
                 "validation_id": event["payload"].get("validation_id"),
                 "snapshot_id": snapshot_id,
                 "successor_snapshot_id": successor_snapshot_id,
                 "changed_fields": sorted(changed_fields),
                 "reason": "the capacity snapshot this decision cited was superseded by a "
                           "material change in " + (", ".join(sorted(changed_fields)) or "capacity")},
                run_id=run_id,
                test_mode=bool(event.get("test_mode")),
            ))
    return appended
