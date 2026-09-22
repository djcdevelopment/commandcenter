"""Run explanation generator against the canonical rubric (WI-G2).

A projection over recorded state and refs — never environmental truth, never a
model's prose, never chain-of-thought (D-115). The six-part rubric:

  1. Task Intent & Submission Context
  2. Capacity Snapshot & Environmental Baseline
  3. Considered & Rejected Routes
  4. Selected Graph & Rationale
  5. Authority & Approval Trail
  6. Execution Outcome & Replay Durability

What WI-G2a changed: the WI-G2 rendering named what was AVAILABLE only by
identifier, dropped what the orchestrator BELIEVED (assumptions and
uncertainty), never rendered the validation verdict or its reason codes, printed
"No human approval was required for this run" on a run whose own recorded status
was `needs_approval`, and derived a durability sentence ending in "hashes
verified" from `len(events) > 0`. Every one of those is a statement the record
did not support. This rendering says what the record holds, names the empty case
as empty, and verifies a digest before it claims one was verified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from hearth.operator import artifacts, canonical, history, paths, replay


def _read_json(target: Path) -> Optional[dict]:
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_contract(target: Path, contract_version: str,
                   refusals: list[str]) -> Optional[dict]:
    """Read a referenced document, or record why it cannot be rendered.

    A projection is a boundary too (D-115, D-104): content from a document that
    fails its contract is never rendered as if it had passed. The refusal is
    named in the output instead, so a damaged reference is diagnosable rather
    than invisible.
    """
    document = _read_json(target)
    if document is None:
        if target.is_file():
            refusals.append(f"`{target.name}` is not valid JSON and was not rendered")
        return None
    try:
        return canonical.validate_contract(document, contract_version,
                                           label=f"`{target.name}`")
    except canonical.CanonicalError as exc:
        refusals.append(f"{exc} — not rendered")
        return None


def explain_run(run_id: str) -> str:
    """Render the narrative explanation of a run from its recorded state."""
    state_path = paths.run_state_path(run_id)
    state = _read_json(state_path)
    if state is None:
        state = replay.replay_run(run_id)

    events = history.read_run_history(run_id)
    refs_dir = paths.run_refs_dir(run_id)
    refusals: list[str] = []

    # 1. Intent, from the FROZEN envelope (D-115: never a raw prompt).
    envelope = _read_contract(refs_dir / "envelope.json", "task-envelope.v1",
                              refusals) or {}
    intent = envelope.get("intent", "No task intent recorded.")
    criteria = envelope.get("acceptance_criteria", [])
    submitted_by = envelope.get("submitted_by", "unspecified")

    proposals = [doc for doc in
                 (_read_contract(refs_dir / f"proposal_{pid}.json", "route-proposal.v1",
                                 refusals)
                  for pid in state.get("proposals", [])) if doc]
    validations = [doc for doc in
                   (_read_contract(refs_dir / f"validation_{vid}.json",
                                   "validation-result.v1", refusals)
                    for vid in state.get("validations", [])) if doc]
    approvals = sorted(refs_dir.glob("approval_*.json")) if refs_dir.is_dir() else []
    approval_docs = [doc for doc in
                     (_read_contract(target, "approval.v1", refusals)
                      for target in approvals) if doc]

    snapshot_ids = sorted({p.get("snapshot_id") for p in proposals if p.get("snapshot_id")})
    catalog_versions = sorted({p.get("catalog_version") for p in proposals
                               if p.get("catalog_version")})

    # 2. What was AVAILABLE: the capacity facts the validation records hold.
    capacity_lines: list[str] = []
    for validation in validations:
        capacity = validation.get("capacity") or {}
        capacity_lines.append(
            f"- Read at `{capacity.get('observed_at') or 'unrecorded'}` for validation "
            f"`{validation['validation_id'][:16]}`: door reachable = "
            f"`{capacity.get('door_reachable')}`"
            + (f" ({capacity.get('door_reason')})" if capacity.get("door_reason") else ""))
        for rung_id, row in sorted((capacity.get("rungs") or {}).items()):
            capacity_lines.append(
                f"  - rung `{rung_id}`: ready = `{row.get('ready')}`, fresh = "
                f"`{row.get('fresh')}` until `{row.get('fresh_until')}`, declared context "
                f"budget `{row.get('context_bytes')}` bytes"
                + (f" — {row['reason']}" if row.get("reason") else ""))
        required = capacity.get("required_fields") or []
        stale = capacity.get("stale_fields") or []
        capacity_lines.append(
            f"  - required capacity fields: {', '.join(f'`{f}`' for f in required) or 'none'};"
            f" stale at validation: {', '.join(f'`{f}`' for f in stale) or 'none'}")
        if validation.get("validation_snapshot_id"):
            capacity_lines.append(
                f"  - bound validation snapshot (D-106): "
                f"`{validation['validation_snapshot_id']}`")
    if not capacity_lines:
        capacity_lines.append("- No validation record holds capacity facts for this run.")

    # 3. Considered: eligible AND rejected.
    considered: list[str] = []
    for proposal in proposals:
        for row in proposal.get("eligible_routes", []):
            considered.append(
                f"- eligible `{row.get('route_id')}` ({row.get('route_kind')}) → target "
                f"`{row.get('target')}`, estimated cost `{row.get('estimated_cost')}`")
        for row in proposal.get("rejected_routes", []):
            considered.append(
                f"- rejected `{row.get('route_id')}` ({row.get('route_kind')}) with code "
                f"`{row.get('reason_code')}` — {row.get('reason_detail')}")
    if not considered:
        considered.append("- No alternative routes are recorded for this run.")

    # 4. Chosen, and what the orchestrator BELIEVED while choosing.
    chosen: list[str] = []
    beliefs: list[str] = []
    for proposal in proposals:
        for node in proposal.get("selected_graph", {}).get("nodes", []):
            chosen.append(f"- Node `{node.get('id')}`: target `{node.get('target')}` "
                          f"({node.get('route_kind')})")
        chosen.append(f"- Rationale: {proposal.get('rationale', 'none recorded')}")
        expected = proposal.get("expected") or {}
        chosen.append(
            f"- Expected: {expected.get('time_s')}s, {expected.get('attempts')} attempt(s), "
            f"{expected.get('context_tokens')} context tokens, resources "
            f"{', '.join(f'`{r}`' for r in expected.get('resources', [])) or 'none'}")
        for assumption in proposal.get("assumptions", []):
            beliefs.append(f"- Assumed: {assumption}")
        uncertainty = proposal.get("uncertainty") or {}
        beliefs.append(f"- Confidence: `{uncertainty.get('confidence', 'unrecorded')}`")
        for unknown in uncertainty.get("unknowns", []):
            beliefs.append(f"- Unknown: {unknown}")
        if proposal.get("mode") == "test":
            beliefs.append("- Mode: `test` (D-113 companions recorded on the proposal)")
    if not chosen:
        chosen.append("- No execution graph nodes are recorded for this run.")
    if not beliefs:
        beliefs.append("- No proposal on file, so nothing is recorded about what was "
                       "assumed or uncertain.")

    # 5. Decided: the validation verdict with its reason codes, then the approval trail.
    decided: list[str] = []
    for validation in validations:
        decided.append(
            f"- Validation `{validation['validation_id'][:16]}` → verdict "
            f"**{validation['verdict']}** (valid until "
            f"`{validation.get('expires_at') or 'unrecorded'}`)")
        for row in validation.get("checks", []):
            if row["result"] == "passed":
                continue
            decided.append(
                f"  - {row['result']} `{row.get('reason_code')}` — {row['reason']}")
            if row.get("remedy"):
                decided.append(f"    remedy: {row['remedy']}")
    if not decided:
        decided.append("- No validation record is on file for this run.")

    approval_lines: list[str] = []
    for record in approval_docs:
        receipt = record.get("receipt")
        revocation = record.get("revocation")
        line = (f"- Approval `{record['approval_id'][:16]}` binds authorities "
                f"{', '.join(f'`{a}`' for a in record.get('authorities', []))} on nodes "
                f"{', '.join(f'`{n}`' for n in record.get('node_ids', []))} "
                f"(targets {', '.join(f'`{t}`' for t in record.get('targets', []))}), "
                f"requested by `{(record.get('requested_by') or {}).get('id')}` at "
                f"`{record.get('requested_at')}`, expires `{record.get('expires_at')}`")
        approval_lines.append(line)
        if receipt:
            approval_lines.append(
                f"  - decided `{receipt.get('decision')}` by "
                f"`{(receipt.get('approving_principal') or {}).get('id')}` "
                f"(profile `{(receipt.get('approving_principal') or {}).get('profile')}`) at "
                f"`{receipt.get('decided_at')}`, receipt `{receipt.get('receipt_id', '')[:16]}`")
        else:
            approval_lines.append("  - pending: no decision receipt is on file")
        if revocation:
            approval_lines.append(
                f"  - REVOKED at `{revocation.get('revoked_at')}` by "
                f"`{(revocation.get('revoked_by') or {}).get('id')}`: {revocation.get('reason')}")
    if not approval_lines:
        gated = any(row["result"] == "needs_approval"
                    for validation in validations for row in validation.get("checks", []))
        approval_lines.append(
            "- No approval record is on file for this run."
            + (" The validation above records a human-gated authority, so the approval is "
               "OUTSTANDING — this is not a statement that none was required."
               if gated else
               " No validation on file records a human-gated authority."))
    for row in state.get("unverified_decisions", []):
        approval_lines.append(
            f"- UNVERIFIED `human.decided` at sequence {row['sequence']}: {row['reason']} "
            "— it grants nothing (D-104: a history row is not authoritative because it "
            "exists)")

    # 6. Outcome and durability, from the artifact records.
    digests = artifacts.verify_digests(run_id)
    required = [row for row in digests if row["retention_class"] == "required"]
    verified = [row for row in required if row["digest_matches"]]
    with_copy = [row for row in required
                 if row["durability"] == "verified" or row["recovery_locations"]]
    if not digests:
        durability = ("no artifact records — durability is unclassified, because a run "
                      "with nothing recorded has nothing to reconstruct from")
    elif state.get("reconstructable"):
        durability = (f"reconstructable — {len(verified)}/{len(required)} required "
                      f"artifacts recomputed to their recorded digest, all with a "
                      "verified second location")
    else:
        durability = (f"auditable_only — {len(verified)}/{len(required)} required "
                      f"artifacts recomputed to their recorded digest, "
                      f"{len(with_copy)}/{len(required)} with a verified second location")

    # Attempts & verification formatting
    attempt_files = sorted(refs_dir.glob("attempt_*.json")) if refs_dir.is_dir() else []
    attempt_docs = [doc for doc in (_read_json(f) for f in attempt_files) if doc]
    attempt_lines: list[str] = []
    for att in attempt_docs:
        attempt_lines.append(
            f"- **Attempt `{att.get('attempt_id', '')[:16]}`:** status `{att.get('status')}`, "
            f"target `{att.get('target')}` ({att.get('route_kind')}), "
            f"model `{att.get('model')}` @ `{att.get('endpoint')}`, "
            f"duration `{att.get('duration_s')}s`"
        )
        usage = att.get("usage") or {}
        tool_str = f"tools={usage.get('tool_calls', 0)}, " if "tool_calls" in usage else ""
        attempt_lines.append(
            f"  - usage: {tool_str}prompt={usage.get('prompt_tokens')}, "
            f"completion={usage.get('completion_tokens')}, "
            f"total={usage.get('total_tokens')}"
        )
        if att.get("mechnet_receipt_id"):
            attempt_lines.append(f"  - mechnet receipt: `{att.get('mechnet_receipt_id')}`")
        if att.get("artifact_sha256"):
            attempt_lines.append(f"  - produced artifact SHA-256: `{att.get('artifact_sha256')}`")

    verification_events = [e for e in events if e.get("event_type") == "verification.recorded"]
    verification_lines: list[str] = []
    for ve in verification_events:
        v_payload = ve.get("payload", {})
        verification_lines.append(
            f"- **Deterministic Verification:** verdict **{v_payload.get('verdict')}**"
        )
        for chk in v_payload.get("checks", []):
            status_str = "passed" if chk.get("passed") else "FAILED"
            verification_lines.append(f"  - check `{chk.get('check')}`: {status_str}")

    lines = [
        f"# Run Explanation: {run_id}",
        "",
        "## 1. Task Intent & Submission Context",
        f"- **Intent (frozen envelope):** {intent}",
        f"- **Envelope ID:** `{state.get('envelope_id', 'unknown')}`",
        f"- **Submitted By:** `{submitted_by}`",
        f"- **Acceptance Criteria Count:** {len(criteria)}",
        f"- **Test mode:** `{bool(state.get('test_mode'))}`",
        "",
        "## 2. Capacity Snapshot & Environmental Baseline",
        f"- **Catalog Versions:** {', '.join(f'`{v}`' for v in catalog_versions) or 'none'}",
        f"- **Planning Snapshot IDs:** {', '.join(f'`{s}`' for s in snapshot_ids) or 'none'}",
        *capacity_lines,
        "",
        "## 3. Considered & Rejected Routes",
        *considered,
        "",
        "## 4. Selected Graph & Decision Rationale",
        *chosen,
        "",
        "### What the orchestrator believed",
        *beliefs,
        "",
        "## 5. Authority & Approval Trail",
        "### Validation verdict",
        *decided,
        "### Approvals",
        *approval_lines,
        "",
        "## 6. Execution Outcome & Replay Durability",
        f"- **Final Run Status:** `{state.get('status')}`",
        f"- **Events Replayed:** {state.get('events_replayed', 0)}"
        + ("  (history ends in an interrupted write; replayed through the last complete "
           "event)" if state.get("truncated_tail") else ""),
        f"- **Recorded Events:** {len(events)}",
        f"- **Artifacts Produced:** {len(digests)}",
        f"- **Durability Classification:** {durability}",
        *attempt_lines,
        *verification_lines,
        "",
    ]
    if refusals:
        lines.extend(["## Referenced documents this projection refused to render",
                      *(f"- {refusal}" for refusal in refusals), ""])
    return "\n".join(lines)
