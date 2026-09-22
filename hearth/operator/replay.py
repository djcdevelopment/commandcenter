"""Deterministic event replayer and state reconstruction (WI-G2).

Replaying a run's history deterministically reconstructs RUN-STATE.json without
wall-clock dependencies: running twice produces byte-identical output. A history
whose final record is an interrupted partial write replays through the last
complete event and REPORTS the truncated tail.

What WI-G2a changed, each item reproduced as a regression test first:

* **Replay verifies before it believes.** The stream is read through
  `history.read_with_status`, which recomputes every `event_id`, checks 1..N
  sequence continuity, enforces the event contract and refuses a row belonging
  to another run. The WI-G2 candidate counted an event of type
  `quantum.teleported` as replayed and read a history declaring
  `operator-history-event.v9` as if it were v1.
* **A history row never grants authority (D-104, D-112 item 5).** A
  `human.decided` row is honoured only when the approval record it names
  re-hashes, carries the receipt the row cites, and that receipt is from a
  human-class principal that still holds `approve`. An uncorroborated row is
  counted, reported in `unverified_decisions`, and grants nothing.
* **Durability comes from artifact records**, not from `len(events) > 0`.

Replay reads no clock and calls nothing outside this package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hearth.operator import approve, artifacts, canonical, history, paths

STATE_CONTRACT_VERSION = "run-state.v1"


class ReplayError(ValueError):
    """Raised when replay cannot process history."""


def _verify_decision(run_id: str, event: dict) -> tuple[bool, str]:
    """Is this `human.decided` row corroborated by an approval that re-hashes?

    One implementation, in `approve.verify_decision_event`, so replay and
    validation cannot disagree about whether a human really decided (WI-G2b
    condition 2).
    """
    return approve.verify_decision_event(run_id, event)


def replay_run(run_id: str, path: Optional[Path | str] = None, *,
               check: bool = False) -> dict:
    """Deterministically replay a run's history and reconstruct RUN-STATE.json."""
    history_file = Path(path) if path else paths.run_history_path(run_id)
    try:
        events, status = history.read_with_status(history_file, run_id)
    except history.HistoryError as exc:
        raise ReplayError(f"history for run {run_id} cannot be trusted: {exc}") from exc

    state: dict[str, Any] = {
        "contract_version": STATE_CONTRACT_VERSION,
        "run_id": run_id,
        "envelope_id": None,
        "events_replayed": len(events),
        "last_sequence": 0,
        "status": "initialized",
        "history_verified": True,
        "truncated_tail": bool(status["truncated_tail"]),
        "test_mode": False,
        "proposals": [],
        "validations": [],
        "approvals": [],
        "revoked_approvals": [],
        "unverified_decisions": [],
        "produced_artifacts": [],
        "reconstructable": False,
        "auditable_only": True,
    }

    for event in events:
        state["last_sequence"] = int(event.get("sequence", 0))
        etype = event.get("event_type")
        payload = event.get("payload", {})
        if event.get("test_mode"):
            state["test_mode"] = True
        if event.get("envelope_id"):
            state["envelope_id"] = event["envelope_id"]

        if etype == "task.received":
            state["status"] = "received"
            if payload.get("envelope_id"):
                state["envelope_id"] = payload["envelope_id"]

        elif etype == "route.proposed":
            state["status"] = "proposed"
            pid = payload.get("proposal_id")
            if pid and pid not in state["proposals"]:
                state["proposals"].append(pid)

        elif etype in ("route.validated", "route.rejected"):
            verdict = payload.get("verdict")
            state["status"] = verdict or ("rejected" if etype == "route.rejected"
                                          else "validated")
            vid = payload.get("validation_id")
            if vid and vid not in state["validations"]:
                state["validations"].append(vid)

        elif etype == "approval.requested":
            state["status"] = "needs_approval"

        elif etype == "human.decided":
            verified, reason = _verify_decision(run_id, event)
            if not verified:
                state["unverified_decisions"].append({
                    "sequence": int(event["sequence"]),
                    "approval_id": payload.get("approval_id"),
                    "reason": reason,
                })
                continue
            approval_id = str(payload["approval_id"])
            if payload.get("decision") == "approve":
                state["status"] = "approved"
                if approval_id not in state["approvals"]:
                    state["approvals"].append(approval_id)
            else:
                state["status"] = "rejected"

        elif etype == "approval.revoked":
            approval_id = str(payload.get("approval_id"))
            if approval_id not in state["revoked_approvals"]:
                state["revoked_approvals"].append(approval_id)
            if approval_id in state["approvals"]:
                state["approvals"].remove(approval_id)
            if not state["approvals"]:
                state["status"] = "needs_approval"

        elif etype == "step.dispatched":
            state["status"] = "dispatching"

        elif etype == "artifact.produced":
            sha = payload.get("sha256")
            if sha and sha not in state["produced_artifacts"]:
                state["produced_artifacts"].append(sha)

        elif etype == "outcome.final":
            state["status"] = payload.get("verdict", "completed")

        elif etype == "decision.invalidated":
            state["status"] = "invalidated"

        elif etype == "decision.superseded":
            state["status"] = "superseded"

    # Durability from the artifact records themselves: every REQUIRED artifact
    # present, its digest recomputed against the stored blob, and at least one
    # verified recovery location. No artifact record, no durability claim.
    digests = artifacts.verify_digests(run_id)
    required = [row for row in digests if row["retention_class"] == "required"]
    state["artifact_digests"] = sorted(
        ({"sha256": row["sha256"], "present": row["present"],
          "digest_matches": row["digest_matches"],
          "retention_class": row["retention_class"],
          "recovery_locations_verified": len(row["recovery_locations"])}
         for row in digests), key=lambda row: row["sha256"])
    if required:
        state["reconstructable"] = all(
            row["digest_matches"] and (row["durability"] == "verified"
                                       or row["recovery_locations"])
            for row in required)
    else:
        state["reconstructable"] = False
    state["auditable_only"] = not state["reconstructable"]

    for key in ("proposals", "validations", "approvals", "revoked_approvals",
                "produced_artifacts"):
        state[key].sort()
    state["unverified_decisions"].sort(key=lambda row: row["sequence"])

    output_path = paths.run_state_path(run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if check and output_path.is_file():
        existing_content = output_path.read_text(encoding="utf-8")
        if existing_content != serialized:
            raise ReplayError(
                f"replay mismatch: reconstructed state differs from {output_path}")

    output_path.write_text(serialized, encoding="utf-8")
    return state
