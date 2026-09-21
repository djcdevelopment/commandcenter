"""Approval boundary enforcement under Decision D-112 (WI-G2, hardened in WI-G2a).

Agents cannot self-approve. The `approve` capability is mapped only to
`operator_approve` and granted exclusively to the `human-operator` profile.

What WI-G2a changed, each item reproduced as a regression test first:

* **The approval identity is recomputed on every load.** `approval_id` hashes a
  named binding — run id, proposal id, validation id, the exact authority set,
  the exact selected nodes and targets, catalog version, capacity snapshot id,
  requester identity, request and expiry timestamps, and the approval contract
  and policy versions. Mutation of ANY bound field invalidates the approval. The
  WI-G2 candidate never re-hashed the record it loaded, so a pending request
  could be widened (more authorities, more nodes) before the human signed it.
* **The decision is a receipt, hashed separately.** An approval is referenced
  before it is decided, so binding the decision timestamp into `approval_id`
  would move the identity at decision time. `receipt_id` binds the decision,
  its timestamp, the deciding principal and the scope; `human.decided` carries
  that receipt id, and validation and replay re-verify the relationship. A
  history row alone never grants authority (D-104).
* **Expiry and revocation exist.** Maximum default lifetime 24 hours; usability
  ends at the EARLIEST of approval expiry, validation expiry, planning-window
  expiry, material capacity invalidation, proposal supersession and revocation.
  Revocation is append-only through `approval.revoked`, and a revoked approval
  is never restored.

What WI-G2b changed (D-112 item 2, Gate 2 condition 1):

* **The requester is the authenticated caller.** `request_approval` took a
  `requesting_principal` dictionary, so a library caller could name a requester
  who never asked — and then, being someone else, decide that request. It now
  takes an `AuthenticatedCaller`, which only `hearth.operator.identity` can mint
  from an identity the callers registry or the gateway resolved. Nothing that
  arrives as data — argv, an environment variable, request JSON, a door-tool
  argument, a history row, an approval record — can become one.
* **Deciding and revoking need an attested identity too.** An `Identity` carries
  its own capability set, so a hand-built one claiming `approve` would otherwise
  be taken at its word; the attestation mark is what distinguishes the registry's
  answer from a caller's assertion.

Tests use temporary registries and ephemeral fixture credentials only. The real
`derek-approver` credential is never minted, entered, or looked for here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hearth.operator import authority as authority_mod, canonical, history, paths
from hearth.operator.identity import AuthenticatedCaller, Identity

CONTRACT_VERSION = "approval.v1"
RECEIPT_CONTRACT_VERSION = "approval-receipt.v1"
DEFAULT_TTL_S = 86400
MAX_TTL_S = 86400


class ApprovalError(PermissionError):
    """Raised when an approval attempt violates the authority boundary."""


def _config() -> dict:
    try:
        table = paths.operator_config().get("approval", {})
    except OSError:
        table = {}
    return {
        "default_ttl_s": int(table.get("default_ttl_s", DEFAULT_TTL_S)),
        "max_ttl_s": int(table.get("max_ttl_s", MAX_TTL_S)),
        "scopes": tuple(str(scope) for scope in table.get("scopes", ("one_use",))),
    }


def principal_of(identity: Identity) -> dict:
    """The three fields an approval record binds about an AUTHENTICATED principal.

    Only an identity the trusted boundary resolved may name a principal: a
    dictionary, a payload or a hand-built `Identity` is caller-supplied data,
    and this is the function that refuses to launder it (D-112 item 2).
    """
    if not isinstance(identity, Identity):
        raise ApprovalError(
            f"a principal is derived from a resolved Identity, not a "
            f"{type(identity).__name__}; the approval boundary accepts no "
            "caller-supplied principal (D-112 item 2)")
    if not identity.attested:
        raise ApprovalError(
            "this identity was not resolved at the trusted boundary, so it cannot name a "
            "principal on an approval record (D-112 item 2)")
    caller = identity.caller
    if not caller or not caller.get("id"):
        raise ApprovalError(
            f"no caller identity is in force ({identity.reason}); an approval names the "
            "authenticated caller, and there is none")
    return {"id": str(caller["id"]),
            "profile": caller.get("profile"),
            "runner_class": caller.get("runner_class")}


def _authenticated(identity: Identity, *, what: str) -> dict:
    """The principal for a decide/revoke caller, attested before it is believed."""
    if not isinstance(identity, Identity) or not identity.attested:
        raise ApprovalError(
            f"{what} denied: the identity presented was not resolved at the trusted "
            "boundary (callers registry or gateway), so its capabilities are a claim, "
            "not a policy answer (D-112 item 2)")
    return principal_of(identity)


def _enforce_contract(record: dict, *, label: str) -> dict:
    try:
        return canonical.validate_contract(record, CONTRACT_VERSION, label=label)
    except canonical.CanonicalError as exc:
        raise ApprovalError(str(exc)) from exc


def approval_path(run_id: str, approval_id: str) -> Path:
    return paths.run_refs_dir(run_id) / f"approval_{approval_id}.json"


def _write(record: dict, run_id: str) -> Path:
    target = approval_path(run_id, record["approval_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return target


def request_approval(
    run_id: str,
    proposal_id: str,
    node_ids: list[str],
    authorities: list[str],
    *,
    requester: AuthenticatedCaller,
    validation_id: str,
    targets: Optional[list[str]] = None,
    catalog_version: str,
    snapshot_id: str,
    now=None,
    ttl_s: Optional[int] = None,
    test_mode: bool = False,
) -> dict:
    """Record an approval request and append `approval.requested`.

    Every bound field is supplied here: the request IS the binding, and the
    identity is computed over it — except `requested_by`, which is read from the
    authenticated caller. `requester` is an `AuthenticatedCaller`, the one type
    that cannot be assembled from request data (D-112 item 2, WI-G2b).
    """
    if not isinstance(requester, AuthenticatedCaller):
        raise ApprovalError(
            f"the requester of an approval is an AuthenticatedCaller minted at the "
            f"trusted boundary, not a {type(requester).__name__}: pass "
            "hearth.operator.identity.authenticated_caller(<resolved identity>) "
            "(D-112 item 2)")
    config = _config()
    moment = now or canonical.utc_now()
    ttl = min(int(ttl_s) if ttl_s is not None else config["default_ttl_s"],
              config["max_ttl_s"])
    if ttl <= 0:
        raise ApprovalError("an approval lifetime must be positive")
    if not authorities:
        raise ApprovalError("an approval request names at least one authority")

    record = {
        "contract_version": CONTRACT_VERSION,
        "run_id": str(run_id),
        "proposal_id": str(proposal_id),
        "validation_id": str(validation_id),
        "authorities": sorted(str(name) for name in authorities),
        "node_ids": sorted(str(name) for name in node_ids),
        "targets": sorted(str(name) for name in (targets or [])),
        "catalog_version": str(catalog_version),
        "snapshot_id": str(snapshot_id),
        "requested_by": requester.principal,
        "requested_at": canonical.rfc3339(moment),
        "expires_at": canonical.rfc3339(canonical.plus_seconds(moment, ttl)),
        "policy_version": authority_mod.policy_version(),
        "receipt": None,
        "revocation": None,
    }
    record["approval_id"] = canonical.approval_identity(record)
    _enforce_contract(record, label="approval request")
    target = _write(record, run_id)

    history.append(
        "approval.requested",
        {
            "approval_id": record["approval_id"],
            "proposal_id": record["proposal_id"],
            "validation_id": record["validation_id"],
            "node_ids": record["node_ids"],
            "targets": record["targets"],
            "authorities": record["authorities"],
            "catalog_version": record["catalog_version"],
            "snapshot_id": record["snapshot_id"],
            "requested_by": record["requested_by"]["id"],
            "expires_at": record["expires_at"],
        },
        refs={"approval_path": paths.repo_relative(target)},
        run_id=run_id,
        test_mode=test_mode,
    )
    return record


def load_approval(run_id: str, approval_id: str) -> dict:
    """Read an approval record and RECOMPUTE its identity before returning it.

    This is the check the WI-G2 candidate did not have: a widened, retargeted or
    otherwise mutated record no longer hashes to the id it is filed under, and
    is refused here rather than honoured downstream.
    """
    target = approval_path(run_id, approval_id)
    if not target.is_file():
        raise ApprovalError(f"no approval request found for {approval_id} in run {run_id}")
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ApprovalError(f"approval record {target} is not valid JSON: {exc}") from exc
    _enforce_contract(record, label=str(target))

    try:
        computed = canonical.approval_identity(record)
    except canonical.CanonicalError as exc:
        raise ApprovalError(f"approval record {target} cannot be re-hashed: {exc}") from exc
    if record.get("approval_id") != computed or str(approval_id) != computed:
        raise ApprovalError(
            f"approval record integrity failure: filed as approval_id {approval_id}, "
            f"declares {record.get('approval_id')}, content hashes to {computed}. "
            "A bound field changed after the request was made, so the approval is "
            "invalid (D-112 item 3).")
    if str(record["run_id"]) != str(run_id):
        raise ApprovalError(
            f"approval {approval_id} belongs to run {record['run_id']!r}, not {run_id!r}")

    receipt = record.get("receipt")
    if receipt is not None:
        computed_receipt = canonical.receipt_identity(receipt)
        if receipt.get("receipt_id") != computed_receipt:
            raise ApprovalError(
                f"decision receipt integrity failure on approval {approval_id}: "
                f"declares {receipt.get('receipt_id')}, hashes to {computed_receipt}")
        if receipt.get("approval_id") != record["approval_id"]:
            raise ApprovalError(
                f"decision receipt on approval {approval_id} references "
                f"{receipt.get('approval_id')}")
    return record


def revocation_event(run_id: str, approval_id: str) -> Optional[dict]:
    """The append-only revocation, if one exists. History is authoritative here:
    a revoked approval cannot be restored by editing the record back."""
    for event in history.read_run_history(run_id):
        if event.get("event_type") == "approval.revoked" \
                and event.get("payload", {}).get("approval_id") == approval_id:
            return event
    return None


def supersession_event(run_id: str, proposal_id: str) -> Optional[dict]:
    """The append-only supersession of a proposal, if one exists.

    Proposal supersession is one of the six things that end an approval's
    usability (D-112 item 4). Like revocation, it is read from history rather
    than from the approval record: the successor proposal is a different
    document, and the superseded approval has no way to know about it.
    """
    for event in history.read_run_history(run_id):
        if event.get("event_type") == "decision.superseded" \
                and event.get("payload", {}).get("proposal_id") == proposal_id:
            return event
    return None


def verify_decision_event(run_id: str, event: dict) -> tuple[bool, str]:
    """Is this `human.decided` row corroborated by an approval that re-hashes?

    The one place that answers "did a human really decide this?", used by replay
    when it reconstructs authority and by validation when it asks whether an
    open decision has been closed. A row is never authoritative because it
    exists (D-104); it must name an approval record that re-hashes to its filed
    identity and carry the receipt that record holds, and that receipt must
    still verify against live policy.
    """
    payload = event.get("payload", {})
    approval_id = payload.get("approval_id")
    receipt_id = payload.get("receipt_id")
    if not approval_id or not receipt_id:
        return False, ("the row names no approval record and no decision receipt, so it "
                       "asserts an authority derived from nothing")
    try:
        record = load_approval(run_id, str(approval_id))
    except ApprovalError as exc:
        return False, str(exc)
    receipt = record.get("receipt") or {}
    if receipt.get("receipt_id") != receipt_id:
        return False, (f"approval {approval_id[:16]} carries receipt "
                       f"{str(receipt.get('receipt_id'))[:16]}, but the row cites "
                       f"{str(receipt_id)[:16]}")
    if receipt.get("decision") != payload.get("decision"):
        return False, (f"approval {approval_id[:16]} records decision "
                       f"{receipt.get('decision')!r}, the row claims "
                       f"{payload.get('decision')!r}")
    if record.get("proposal_id") != payload.get("proposal_id"):
        return False, f"approval {approval_id[:16]} binds a different proposal"
    ok, reason = verify_receipt(record)
    if not ok and receipt.get("decision") == "reject":
        # A rejection needs the same principal checks, minus the "approve"
        # verdict the receipt cannot carry.
        principal = receipt.get("approving_principal") or {}
        if principal.get("runner_class") == "human" \
                and "approve" in _capabilities_of(principal.get("profile")) \
                and principal.get("id") != (record.get("requested_by") or {}).get("id"):
            return True, f"receipt {str(receipt_id)[:16]} verified (rejection)"
    return ok, reason


def _capabilities_of(profile: Optional[str]) -> frozenset[str]:
    from hearth.kernel.capabilities import LEGACY_PROFILE, load_profiles

    if not profile or profile == LEGACY_PROFILE:
        return frozenset()
    profiles = load_profiles(paths.PROFILES_PATH)
    resolved = profiles.get(profile)
    return frozenset(resolved.capabilities) if resolved else frozenset()


def verify_receipt(record: dict) -> tuple[bool, str]:
    """Re-verify a decision receipt against live policy.

    Not "a row said so": the deciding principal's profile is resolved again
    through the kernel's own profile loader and must still hold `approve`, and
    the principal must still be human-class.
    """
    receipt = record.get("receipt")
    if not receipt:
        return False, f"approval {record['approval_id']} has no decision receipt"
    if receipt.get("decision") != "approve":
        return False, (f"approval {record['approval_id']} was decided "
                       f"{receipt.get('decision')!r}, not 'approve'")
    principal = receipt.get("approving_principal") or {}
    if principal.get("runner_class") != "human":
        return False, (f"decision receipt {receipt.get('receipt_id')} names a "
                       f"{principal.get('runner_class')!r} principal; only a human-class "
                       "principal may decide (D-112)")
    if "approve" not in _capabilities_of(principal.get("profile")):
        return False, (f"decision receipt {receipt.get('receipt_id')} names profile "
                       f"{principal.get('profile')!r}, which does not hold 'approve' "
                       "under live policy")
    if principal.get("id") == (record.get("requested_by") or {}).get("id"):
        return False, "the requesting principal decided its own request"
    return True, f"receipt {receipt['receipt_id']} verified"


def decide_approval(
    run_id: str,
    approval_id: str,
    decision: str,
    approver: Identity,
    *,
    scope: str = "one_use",
    proposal: Optional[dict] = None,
    now=None,
) -> dict:
    """Process a human approval decision under D-112 rules."""
    if decision not in ("approve", "reject"):
        raise ApprovalError(f"decision must be 'approve' or 'reject', got {decision!r}")
    config = _config()
    if scope not in config["scopes"]:
        raise ApprovalError(f"scope must be one of {', '.join(config['scopes'])}, got {scope!r}")

    caller = approver.caller if isinstance(approver, Identity) else None
    if caller is None:
        raise ApprovalError("approval denied: no approver identity presented")
    _authenticated(approver, what="approval")
    if not approver.grants("approve"):
        raise ApprovalError(
            f"approval denied: principal {caller['id']!r} (profile {caller.get('profile')!r}) "
            "lacks 'approve' capability (D-112 approval boundary)")
    if caller.get("runner_class") != "human":
        raise ApprovalError(
            f"approval denied: runner_class must be 'human', got "
            f"{caller.get('runner_class')!r}")

    record = load_approval(run_id, approval_id)

    # Revocation first: a revoked approval is refused AS REVOKED, so the reason
    # names the withdrawal rather than the decision it once carried.
    if record.get("revocation") is not None or revocation_event(run_id, approval_id):
        raise ApprovalError(
            f"approval {approval_id} was revoked and can never be restored; "
            "request a new approval (D-112 item 5)")
    if record.get("receipt") is not None:
        raise ApprovalError(f"approval {approval_id} has already been decided")

    moment = now or canonical.utc_now()
    expires_at = canonical.parse_rfc3339(str(record["expires_at"]))
    if moment > expires_at:
        raise ApprovalError(
            f"approval {approval_id} expired at {record['expires_at']} "
            f"(now {canonical.rfc3339(moment)}); request a new approval")

    if record["requested_by"]["id"] == caller["id"]:
        raise ApprovalError("approval denied: self-approval against requesting principal "
                            "is forbidden")

    for document in (proposal, _stored_proposal(run_id, record["proposal_id"])):
        orchestrator = (document or {}).get("orchestrator") or {}
        if orchestrator.get("client") == caller["id"] or orchestrator.get("session") == caller["id"]:
            raise ApprovalError(
                "approval denied: self-approval is forbidden — the approver is the "
                "orchestrator that proposed this route")

    receipt = {
        "contract_version": RECEIPT_CONTRACT_VERSION,
        "approval_id": record["approval_id"],
        "decision": decision,
        "decided_at": canonical.rfc3339(moment),
        "approving_principal": principal_of(approver),
        "scope": scope,
        "policy_version": authority_mod.policy_version(),
    }
    receipt["receipt_id"] = canonical.receipt_identity(receipt)
    record["receipt"] = receipt
    _enforce_contract(record, label=f"decided approval {approval_id}")
    target = _write(record, run_id)

    history.append(
        "human.decided",
        {
            "approval_id": record["approval_id"],
            "receipt_id": receipt["receipt_id"],
            "proposal_id": record["proposal_id"],
            "validation_id": record["validation_id"],
            "decision": decision,
            "approving_principal": receipt["approving_principal"]["id"],
            "scope": scope,
        },
        refs={"approval_path": paths.repo_relative(target)},
        run_id=run_id,
    )
    return record


def revoke_approval(run_id: str, approval_id: str, revoker: Identity, reason: str,
                    *, now=None) -> dict:
    """Revoke an approval, append-only. A revoked approval is never restored."""
    if not isinstance(reason, str) or not reason.strip():
        raise ApprovalError("a revocation states its reason")
    caller = revoker.caller if isinstance(revoker, Identity) else None
    if caller is None:
        raise ApprovalError("revocation denied: no identity presented")
    _authenticated(revoker, what="revocation")
    if not revoker.grants("approve") or caller.get("runner_class") != "human":
        raise ApprovalError(
            f"revocation denied: principal {caller['id']!r} is not a human-class "
            "principal holding 'approve' (D-112)")

    record = load_approval(run_id, approval_id)
    if record.get("revocation") is not None:
        raise ApprovalError(f"approval {approval_id} is already revoked")

    moment = now or canonical.utc_now()
    record["revocation"] = {
        "revoked_at": canonical.rfc3339(moment),
        "revoked_by": principal_of(revoker),
        "reason": reason.strip()[:500],
    }
    _enforce_contract(record, label=f"revoked approval {approval_id}")
    target = _write(record, run_id)
    history.append(
        "approval.revoked",
        {
            "approval_id": record["approval_id"],
            "proposal_id": record["proposal_id"],
            "revoked_by": record["revocation"]["revoked_by"]["id"],
            "reason": record["revocation"]["reason"],
        },
        refs={"approval_path": paths.repo_relative(target)},
        run_id=run_id,
    )
    return record


def _stored_proposal(run_id: str, proposal_id: str) -> Optional[dict]:
    target = paths.run_refs_dir(run_id) / f"proposal_{proposal_id}.json"
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _stored_validation(run_id: str, validation_id: str) -> Optional[dict]:
    target = paths.run_refs_dir(run_id) / f"validation_{validation_id}.json"
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        return None


def approval_ids(run_id: str) -> list[str]:
    """The approval ids a run has records for, from their filenames."""
    refs = paths.run_refs_dir(run_id)
    if not refs.is_dir():
        return []
    return [target.stem.split("approval_", 1)[1]
            for target in sorted(refs.glob("approval_*.json"))]


def list_approvals(run_id: str) -> list[dict]:
    """Every approval record filed for a run, each verified on load.

    Raises on the first record that cannot be believed. Callers that must keep
    going (validation renders a verdict; it does not crash on a tampered file)
    use `load_approvals_with_failures`.
    """
    return [load_approval(run_id, approval_id) for approval_id in approval_ids(run_id)]


def load_approvals_with_failures(run_id: str) -> tuple[list[dict], list[str]]:
    """Every approval record that loads, and the refusal for each that does not.

    A tampered record is still refused — it just becomes a REASON rather than an
    exception, so `validate_proposal` can return a verdict that names it instead
    of raising out of the validator (the WI-G2a brief's "return rejected
    verdicts instead of raising", applied to the approval path).
    """
    records: list[dict] = []
    failures: list[str] = []
    for approval_id in approval_ids(run_id):
        try:
            records.append(load_approval(run_id, approval_id))
        except ApprovalError as exc:
            failures.append(str(exc))
    return records, failures


def effective_approval(run_id: str, context: dict, *, now=None) -> dict:
    """The approval that authorizes `context`, or why none does.

    Returns ``{"record": record|None, "reason": str}``. The context names the
    proposal, the exact authority set asked about, the exact selected nodes and
    targets, the catalog version and the capacity snapshot id — every bound
    field. Usability ends at the earliest of approval expiry, revocation,
    a binding mismatch (a changed route, catalog or snapshot IS a mismatch), a
    missing or unverifiable validation, and an unverifiable receipt.
    """
    moment = now or canonical.utc_now()
    wanted = {
        "proposal_id": str(context["proposal_id"]),
        "authorities": sorted(str(name) for name in context.get("authorities", [])),
        "node_ids": sorted(str(name) for name in context.get("node_ids", [])),
        "targets": sorted(str(name) for name in context.get("targets", [])),
        "catalog_version": str(context["catalog_version"]),
        "snapshot_id": str(context["snapshot_id"]),
    }
    records, failures = load_approvals_with_failures(run_id)
    reasons: list[str] = list(failures)
    # Proposal supersession ends usability for every approval bound to the old
    # proposal at once (D-112 item 4), so it is read before the records are.
    superseded = supersession_event(run_id, wanted["proposal_id"])
    for record in records:
        if record["proposal_id"] != wanted["proposal_id"]:
            continue
        approval_id = record["approval_id"]

        if superseded is not None:
            successor = str(superseded["payload"].get("successor_proposal_id", "unknown"))
            reasons.append(
                f"approval {approval_id[:16]} is bound to proposal "
                f"{wanted['proposal_id'][:16]}, which was superseded by "
                f"{successor[:16]}; a superseding proposal needs its own approval")
            continue

        if record.get("revocation") is not None or revocation_event(run_id, approval_id):
            reasons.append(f"approval {approval_id[:16]} was revoked")
            continue
        if moment > canonical.parse_rfc3339(str(record["expires_at"])):
            reasons.append(f"approval {approval_id[:16]} expired at {record['expires_at']}")
            continue

        mismatched = [field for field in ("authorities", "node_ids", "targets",
                                          "catalog_version", "snapshot_id")
                      if record[field] != wanted[field]]
        if mismatched:
            reasons.append(
                f"approval {approval_id[:16]} binds a different "
                f"{', '.join(mismatched)}; a changed route, authority set, catalog or "
                "snapshot requires a new approval")
            continue

        validation = _stored_validation(run_id, record["validation_id"])
        if validation is None:
            reasons.append(
                f"approval {approval_id[:16]} cites validation "
                f"{record['validation_id'][:16]}, which is not on file")
            continue
        try:
            canonical.validate_contract(validation, "validation-result.v1",
                                        label=f"validation {record['validation_id'][:16]}")
        except canonical.CanonicalError as exc:
            reasons.append(f"approval {approval_id[:16]}: {exc}")
            continue
        if canonical.identity_of(validation, "validation_id") != record["validation_id"] \
                or validation.get("proposal_id") != record["proposal_id"]:
            reasons.append(
                f"approval {approval_id[:16]} cites a validation whose identity does "
                "not match its content")
            continue

        ok, reason = verify_receipt(record)
        if not ok:
            reasons.append(reason)
            continue
        return {"record": record, "reason": reason}

    if not reasons:
        reasons.append("no approval record covers this proposal")
    return {"record": None, "reason": "; ".join(reasons)}
