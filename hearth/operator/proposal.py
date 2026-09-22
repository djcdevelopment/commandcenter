"""Route proposal structuring, step declarations, bounds (WI-G2).

Every orchestration decision references a frozen TaskEnvelope, an exact catalog
version, a hashed expiring capacity snapshot, the orchestrator identity, policy
version, eligible and rejected routes with structured reason codes, the selected
graph, assumptions, expectations, and required authority.

Any proposal containing thinking, reasoning, or chain_of_thought fields is
rejected per D-115 — at BUILD time and, since WI-G2a, at INGESTION time too:
`store_proposal` was an unguarded door through which the WI-G2 candidate
accepted a reasoning-bearing proposal carrying an invalid reason code and an
identity that did not match its content, and recorded `route.proposed` for it.
Every ingestion now runs the D-115 scan, the reason-code check, the schema, the
contract-version gate and an identity recomputation before anything is written.

D-107: `resolve_door_rung` resolves the door rung a door-assisted proposal used —
`gcp-gemini` by default, configurable in `hearth/etc/operator.toml` — and records
the resolved rung, backend and model identity. An unavailable default fails
explicitly; there is no silent fallback. (Until WI-G2b condition 7 this
paragraph named a function that has never existed in this package; the obsolete
symbol is recorded in `hearth/tests/operator/test_regression_obsolete_symbols.py`,
which fails if it reappears anywhere in code, schemas, examples or docs.)

WI-G2b condition 2: a proposal may declare `supersedes`, the `proposal_id` of an
earlier proposal in the same run. The reference is RESOLVED at ingestion — a
proposal this run does not hold is refused — and `store_proposal` then appends
`decision.superseded` naming both ids, exactly once, which is what makes
proposal supersession one of the automatic D-112 item 4 terminators rather than
a function nobody called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hearth.operator import canonical, history, paths

CONTRACT_VERSION = "route-proposal.v1"
FORBIDDEN_REASONING_KEYS = frozenset({"thinking", "reasoning", "chain_of_thought"})

REASON_CODES = frozenset({
    "capacity_not_ready",
    "snapshot_expired",
    "snapshot_invalidated",
    "catalog_mismatch",
    "over_budget_context",
    "over_budget_attempts",
    "authority_missing",
    "human_required",
    "lane_absent",
    "implementation_not_live",
    "policy_block",
    "cost_over_budget",
    "not_applicable",
})


class ProposalError(ValueError):
    """Raised when a route proposal cannot be built, ingested, or stored."""


def _enforce_contract(document: dict, *, label: str) -> dict:
    try:
        return canonical.validate_contract(document, CONTRACT_VERSION, label=label)
    except canonical.CanonicalError as exc:
        raise ProposalError(str(exc)) from exc


def _check_no_reasoning(data: Any, path: str = "$") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in FORBIDDEN_REASONING_KEYS:
                raise ProposalError(f"{path}: reasoning field {key!r} is forbidden (D-115)")
            _check_no_reasoning(value, f"{path}.{key}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _check_no_reasoning(item, f"{path}[{index}]")


def build_proposal(
    envelope_id: str,
    catalog_version: str,
    snapshot_id: str,
    snapshot_expires_at: str,
    orchestrator: dict[str, str],
    policy_version: str,
    eligible_routes: list[dict[str, Any]],
    rejected_routes: list[dict[str, Any]],
    selected_graph: dict[str, Any],
    assumptions: list[str],
    uncertainty: dict[str, Any],
    expected: dict[str, Any],
    required_authority: list[str],
    required_approvals: list[str],
    rationale: str,
    mode: str = "normal",
    test_mode_fields: Optional[dict[str, Any]] = None,
    supersedes: Optional[str] = None,
) -> dict:
    """Build and stamp a RouteProposal document."""
    if len(rationale) > 2000:
        raise ProposalError(f"rationale exceeds 2000 characters ({len(rationale)})")
    if mode not in ("normal", "test"):
        raise ProposalError(f"invalid mode {mode!r}; must be 'normal' or 'test'")

    for rej in rejected_routes:
        code = rej.get("reason_code")
        if code not in REASON_CODES:
            raise ProposalError(f"invalid rejected route reason_code {code!r}")

    proposal_body = {
        "contract_version": CONTRACT_VERSION,
        "envelope_id": str(envelope_id),
        "catalog_version": str(catalog_version),
        "snapshot_id": str(snapshot_id),
        "snapshot_expires_at": str(snapshot_expires_at),
        "orchestrator": {
            "provider": str(orchestrator.get("provider", "")),
            "model": str(orchestrator.get("model", "")),
            "endpoint_or_version": str(orchestrator.get("endpoint_or_version", "")),
            "client": str(orchestrator.get("client", "")),
            "harness": str(orchestrator.get("harness", "")),
            "session": str(orchestrator.get("session", "")),
        },
        "policy_version": str(policy_version),
        "eligible_routes": list(eligible_routes),
        "rejected_routes": list(rejected_routes),
        "selected_graph": dict(selected_graph),
        "assumptions": list(assumptions),
        "uncertainty": dict(uncertainty),
        "expected": dict(expected),
        "required_authority": list(required_authority),
        "required_approvals": list(required_approvals),
        "rationale": str(rationale),
        "mode": str(mode),
    }

    if test_mode_fields is not None:
        proposal_body["test_mode_fields"] = dict(test_mode_fields)
    if supersedes is not None:
        # Present only when it means something: an absent key keeps every
        # existing proposal identity exactly where it was.
        proposal_body["supersedes"] = str(supersedes)

    _check_no_reasoning(proposal_body)

    stamped = canonical.stamp_identity(proposal_body, "proposal_id")
    return _enforce_contract(stamped, label="built proposal")


def draft_route(
    envelope: dict,
    snapshot: dict,
    catalog: dict,
    orchestrator: dict,
    policy_version: str,
    mode: str = "normal",
    preferred_route: Optional[str] = None,
    test_mode_fields: Optional[dict[str, Any]] = None,
    target_rung: Optional[str] = None,
) -> dict:
    """Generate a deterministic route proposal draft from envelope and capacity.

    ``target_rung`` pins a catalog rung (e.g. ``am4-dense``) as the direct-inference
    target: the selected node names the rung, so the validator reads that rung's
    readiness and budget from the snapshot, and ``execute`` dispatches to it.
    """
    envelope_id = envelope["envelope_id"]
    catalog_version = catalog["catalog_version"]
    snapshot_id = snapshot["snapshot_id"]
    snapshot_expires_at = snapshot["planning_valid_until"]

    eligible = []
    rejected = []

    # Evaluate loop implementations in catalog
    for loop in catalog.get("loops", []):
        for impl in loop.get("implementations", []):
            impl_id = impl["id"]
            impl_status = impl.get("status", "ABSENT")
            if impl_status != "LIVE" and mode != "test":
                rejected.append({
                    "route_id": impl_id,
                    "route_kind": loop["id"],
                    "target": impl.get("entrypoint", impl_id),
                    "reason_code": "implementation_not_live",
                    "reason_detail": f"status is {impl_status}, requires LIVE in normal mode",
                })
            else:
                eligible.append({
                    "route_id": impl_id,
                    "route_kind": loop["id"],
                    "target": impl.get("entrypoint", impl_id),
                    # Canonical monetary wire form (WI-G2b): "USD <amount>",
                    # compared as integer micro-USD. A deterministic draft onto
                    # a sunk-cost local rung really is zero; a route whose cost
                    # is unknown declares null and is refused against a budget.
                    "estimated_cost": "USD 0.00",
                })

    # Pick the selected graph. `direct_hearth` when it is eligible: it is the one
    # LIVE implementation of direct inference, and a draft that names a loop the
    # catalog happens to list first is a coincidence, not a decision.
    by_id = {row["route_id"]: row for row in eligible}
    if target_rung:
        rungs = {str(row["id"]): row for row in catalog.get("rungs", [])}
        if target_rung not in rungs:
            raise ProposalError(
                f"rung {target_rung!r} is not in catalog {catalog_version}; "
                "recompile the catalog or name a declared rung. No silent fallback.")
        if rungs[target_rung].get("retired"):
            raise ProposalError(
                f"rung {target_rung!r} is retired: a tombstone, not an offer.")
        if "direct_hearth" not in by_id:
            raise ProposalError(
                "direct_inference has no LIVE implementation to carry a rung pin")
        # The rung is a route of its own: the validator prices the selected node
        # from the eligible row that names it, and reads readiness by rung id.
        # (When loops.toml already lists the rung as a LIVE implementation, that
        # row is the one; only an unlisted rung gets a synthesized row.)
        if target_rung not in by_id:
            eligible.append({
                "route_id": target_rung,
                "route_kind": "direct_inference",
                "target": target_rung,
                "estimated_cost": "USD 0.00",
            })
            by_id[target_rung] = eligible[-1]
        selected_node_target = target_rung
    elif preferred_route and preferred_route in by_id:
        selected_node_target = preferred_route
    elif "direct_hearth" in by_id:
        selected_node_target = "direct_hearth"
    elif eligible:
        selected_node_target = eligible[0]["route_id"]
    else:
        raise ProposalError(
            f"catalog {catalog_version} lists no eligible implementation to draft onto; "
            "compile the catalog or propose a route by hand")

    selected_graph = {
        "nodes": [
            {
                "id": "step-1",
                "route_kind": str(by_id[selected_node_target]["route_kind"]),
                "target": selected_node_target,
                # The envelope by REFERENCE. Intent prose lives in the frozen
                # envelope and is read from there (D-115); copying it into every
                # derived document multiplies the places it has to be redacted.
                "inputs": {"envelope_id": str(envelope_id)},
                "expected": {"attempts": 1},
            }
        ],
        "edges": [],
    }

    assumptions = [
        "Capacity snapshot remains valid for planning duration.",
        "Target execution environment is clean.",
    ]
    uncertainty = {
        "confidence": "high",
        "unknowns": [],
    }
    # The route's context claim is sized to the envelope's constraint, never above
    # it: a draft that claims more than the envelope allows is rejected as
    # over_budget_context, correctly.
    envelope_max_context = (envelope.get("constraints") or {}).get("max_context_tokens")
    context_tokens = 8192
    if isinstance(envelope_max_context, int) and envelope_max_context > 0:
        context_tokens = min(context_tokens, envelope_max_context)
    expected = {
        "time_s": 120,
        "attempts": 1,
        "context_tokens": context_tokens,
        "resources": [target_rung] if target_rung else ["omen-arc"],
    }
    required_authority = ["call_door_generate"]
    required_approvals = []

    rationale = f"Drafted deterministic route targeting {selected_node_target} based on available capacity."
    if target_rung:
        rationale = (f"Drafted deterministic direct_inference route pinned to rung "
                     f"{target_rung} (caller's --target) based on available capacity.")

    if mode == "test":
        if test_mode_fields is None:
            raise ProposalError(
                "a mode:test draft needs its D-113 companions, and this drafter will not "
                "invent them: pass test_mode_fields with an existing released work_item, "
                "isolated_inputs and isolated_outputs outside master, base_commit, "
                "base_tree, bounded_authorities, rollback_ref, recovery_instructions and "
                "explicit test_targets.")
        required_approvals.append("human-operator")

    return build_proposal(
        envelope_id=envelope_id,
        catalog_version=catalog_version,
        snapshot_id=snapshot_id,
        snapshot_expires_at=snapshot_expires_at,
        orchestrator=orchestrator,
        policy_version=policy_version,
        eligible_routes=eligible,
        rejected_routes=rejected,
        selected_graph=selected_graph,
        assumptions=assumptions,
        uncertainty=uncertainty,
        expected=expected,
        required_authority=required_authority,
        required_approvals=required_approvals,
        rationale=rationale,
        mode=mode,
        test_mode_fields=test_mode_fields,
    )


DEFAULT_DOOR_RUNG = "gcp-gemini"


def resolve_door_rung(catalog: dict, snapshot: Optional[dict] = None,
                      requested: Optional[str] = None) -> dict:
    """Resolve the rung a door-assisted proposal or explanation runs on (D-107).

    Returns ``{rung, backend, model, endpoint, node, source}``. Raises
    ProposalError when the requested (or default) rung is not authorized, not in
    the catalog, or not recorded ready in the capacity snapshot — explicitly,
    naming the rung and the reason. There is no silent fallback: falling back to
    another rung would spend a different lane's budget under the first lane's
    name, which is exactly what D-107 forbids.
    """
    config = paths.operator_config().get("door", {})
    default_rung = str(config.get("default_rung", DEFAULT_DOOR_RUNG))
    allowed = [str(name) for name in config.get("allowed_rungs", (default_rung,))]
    rung_id = str(requested or default_rung)
    source = "caller" if requested else "hearth/etc/operator.toml [door].default_rung"

    if rung_id not in allowed:
        raise ProposalError(
            f"rung {rung_id!r} is not an authorized door rung "
            f"({', '.join(allowed)}); D-107 makes the default configurable, not the "
            "route privileged. No silent fallback.")
    rungs = {str(row["id"]): row for row in catalog.get("rungs", [])}
    if rung_id not in rungs:
        raise ProposalError(
            f"rung {rung_id!r} is not in catalog {catalog.get('catalog_version')}; "
            "recompile the catalog or select another authorized rung. No silent fallback.")
    rung = rungs[rung_id]
    if rung.get("retired"):
        raise ProposalError(
            f"rung {rung_id!r} is retired: a tombstone, not an offer. No silent fallback.")

    ready, reason = None, "no capacity snapshot was read"
    if snapshot is not None:
        field = ((snapshot.get("rungs") or {}).get(rung_id) or {}).get("ready") or {}
        ready = field.get("value")
        reason = field.get("reason") or "the snapshot records no reason"
    if ready is not True:
        raise ProposalError(
            f"the door rung {rung_id!r} (resolved from {source}) is recorded "
            f"ready={ready!r} ({reason}). An unavailable default fails explicitly — "
            "select another authorized rung deliberately. No silent fallback.")

    models = [str(name) for name in rung.get("models", []) or []]
    return {
        "rung": rung_id,
        "backend": str(rung.get("backend") or rung_id),
        "model": models[0] if models else str(rung.get("model") or ""),
        "endpoint": str(rung.get("endpoint") or ""),
        "node": str(rung.get("node") or ""),
        "source": source,
    }


def ingest_proposal(document: Any, *, label: str = "proposal") -> dict:
    """Every guard an ingested proposal passes before it is believed.

    Contract version, schema, D-115 reasoning scan, the closed reason-code enum,
    and an identity recomputation — in that order, so an unreadable contract is
    never re-hashed and a tampered document is never recorded.
    """
    if not isinstance(document, dict):
        raise ProposalError(f"{label}: a route proposal is an object, "
                            f"got {type(document).__name__}")
    # D-115 first: a reasoning field is refused BY NAME, whatever else is wrong
    # with the document, so the refusal says why it was really refused.
    _check_no_reasoning(document)
    _enforce_contract(document, label=label)
    for rejected in document.get("rejected_routes", []):
        code = rejected.get("reason_code")
        if code not in REASON_CODES:
            raise ProposalError(f"{label}: invalid rejected route reason_code {code!r}")
    computed = canonical.identity_of(document, "proposal_id")
    if document["proposal_id"] != computed:
        raise ProposalError(
            f"{label}: proposal_id mismatch — declared {document['proposal_id']}, "
            f"content hashes to {computed}. The bytes changed after the identity was "
            "assigned; re-stamp the proposal or re-propose it.")
    return document


def stored_proposal_path(run_id: str, proposal_id: str) -> Path:
    return paths.run_refs_dir(run_id) / f"proposal_{proposal_id}.json"


def _resolve_supersedes(proposal: dict, run_id: str) -> Optional[str]:
    """The proposal this one replaces, resolved — or a refusal.

    A supersession that names nothing is not a supersession: it would close no
    decision and invalidate no approval, while reading as though it had.
    """
    superseded = proposal.get("supersedes")
    if superseded is None:
        return None
    superseded = str(superseded)
    if superseded == str(proposal["proposal_id"]):
        raise ProposalError(
            f"proposal {superseded[:16]} supersedes itself; a supersession names an "
            "EARLIER proposal of the same run")
    if not stored_proposal_path(run_id, superseded).is_file():
        raise ProposalError(
            f"supersedes {superseded} names no proposal stored in run {run_id}: the "
            f"reference is resolved, not assumed. Store the proposal being superseded "
            "first, or drop the field.")
    return superseded


def _already_superseded(run_id: str, superseded: str, successor: str) -> bool:
    """Has this exact supersession already been recorded? Re-ingesting the same
    document must not append a second identical row (idempotence)."""
    try:
        events = history.read_run_history(run_id)
    except history.HistoryError:
        return False
    for event in events:
        payload = event.get("payload", {})
        if event.get("event_type") == "decision.superseded" \
                and payload.get("proposal_id") == superseded \
                and payload.get("successor_proposal_id") == successor:
            return True
    return False


def store_proposal(proposal: dict, run_id: str, *,
                   via_door: Optional[dict] = None) -> Path:
    """Store a proposal in runs/operator/<run_id>/refs/ and record `route.proposed`.

    An ingestion boundary, not a file copy: every guard in `ingest_proposal` runs
    before a byte is written or an event is appended. A proposal that declares
    `supersedes` also appends `decision.superseded` — once — naming both ids
    (WI-G2b condition 2, D-112 item 4).
    """
    ingest_proposal(proposal, label=f"proposal for run {run_id}")
    superseded = _resolve_supersedes(proposal, run_id)
    proposal_id = proposal["proposal_id"]
    refs_dir = paths.run_refs_dir(run_id)
    refs_dir.mkdir(parents=True, exist_ok=True)
    target = refs_dir / f"proposal_{proposal_id}.json"
    target.write_text(json.dumps(proposal, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    payload = {
        "proposal_id": proposal_id,
        "envelope_id": proposal["envelope_id"],
        "snapshot_id": proposal["snapshot_id"],
        "catalog_version": proposal["catalog_version"],
        "mode": proposal["mode"],
        "selected_nodes": len(proposal["selected_graph"]["nodes"]),
        "eligible_routes": len(proposal["eligible_routes"]),
        "rejected_routes": len(proposal["rejected_routes"]),
        "required_authority": sorted(set(map(str, proposal["required_authority"]))),
    }
    if via_door is not None:
        payload["via_door"] = dict(via_door)
    if superseded is not None:
        payload["supersedes"] = superseded
    history.append(
        "route.proposed",
        payload,
        refs={"proposal_path": paths.repo_relative(target)},
        run_id=run_id,
        envelope_id=proposal["envelope_id"],
        test_mode=proposal["mode"] == "test",
    )
    if superseded is not None and not _already_superseded(run_id, superseded, proposal_id):
        from hearth.operator import validate as validate_mod

        validate_mod.supersede_decision(
            run_id,
            superseded_proposal_id=superseded,
            successor_proposal_id=proposal_id,
            reason=("a superseding proposal was accepted for this run; every open "
                    "validation and approval bound to the superseded proposal ends here "
                    "(D-112 item 4)"),
            test_mode=proposal["mode"] == "test")
    return target
