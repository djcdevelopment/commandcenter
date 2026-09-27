"""Condition 2 (WI-G2b): supersession is automatic, and only a human closes a
human approval decision.

Two defects the WI-G2a verification found in the same place:

* `decision.superseded` had a working emitter that no product code ever called
  (`supersede_decision`), so proposal supersession — one of the six D-112 item 4
  terminators — was the only one that was not automatic. A test called it; the
  control plane never did.
* `invalidate_superseded_decisions` treated ANY caller's `route.rejected` row as
  closing a proposal's open decision. Verifier A watched a research caller's
  authority rejection suppress a later `decision.invalidated` for a decision
  that was still open. A deterministic validation rejection is not a human
  approval rejection, and a row is not a decision (D-104).

Derek's Gate 2 decision: appending `decision.superseded` automatically, naming
both proposal ids, invalidating every open validation and approval bound to the
old proposal, deterministically and idempotently; and an approval decision
closed only by a valid authenticated decision receipt from a current human-class
principal holding `approve`.
"""

from __future__ import annotations

import ast
import json
import unittest
from datetime import timedelta
from pathlib import Path

from hearth.operator import (approve, canonical, catalog as catalog_mod, core,
                             envelope as envelope_mod, history, inspection, paths,
                             proposal as proposal_mod, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (FakeDoor, HUMAN_APPROVER_KEY, OperatorTestCase,
                                           RESEARCH_KEY, UNRESTRICTED_KEY, fake_cli_runner)


class SupersessionBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-supersession"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    # --- harness ----------------------------------------------------------
    def requester(self, identity=None):
        """The authenticated requester context store_validation binds (WI-G2b)."""
        return identity if identity is not None else self.caller

    def proposal(self, **overrides):
        fields = {"envelope_id": self.envelope["envelope_id"]}
        fields.update(overrides)
        return self.make_proposal(self.catalog, self.snapshot, **fields)

    def successor_of(self, earlier: dict, **overrides) -> dict:
        """A proposal that explicitly supersedes `earlier`."""
        document = json.loads(json.dumps(earlier))
        document["supersedes"] = earlier["proposal_id"]
        document["rationale"] = ("A superseding proposal: the first route is withdrawn "
                                 "in favour of this one.")
        document.update(overrides)
        document.pop("proposal_id", None)
        return canonical.stamp_identity(document, "proposal_id")

    def validated(self, proposal: dict, *, caller=None):
        who = caller or self.caller
        result = validate.validate_proposal(proposal, who, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.store_validation_as(result, self.run_id, who)
        return result

    def events(self, event_type: str) -> list[dict]:
        return [ev for ev in history.read_run_history(self.run_id)
                if ev["event_type"] == event_type]

    def material_refresh(self) -> None:
        """Observe capacity again with a material change, as `operator inspect
        --refresh` does; this is what invalidates open decisions (G2-C9)."""
        inspection.write_snapshot(self.snapshot)
        inspection.write_current(self.catalog, self.snapshot)
        door = FakeDoor()
        door.responses = dict(door.responses, capture_resource_snapshot={
            "omen-arc": {"observed_at": "2026-09-17T12:05:00Z", "ready": False,
                         "loaded_models": [], "reason": "arc-maintenance.stop"}})
        core.refresh(door=door, cli_runner=fake_cli_runner(), catalog=self.catalog,
                     now=self.now + timedelta(seconds=300))

    def approver(self):
        self.set_key(HUMAN_APPROVER_KEY)
        who = resolve_from_env()
        self.set_key(UNRESTRICTED_KEY)
        return who


class AutomaticSupersessionTests(SupersessionBase):
    def test_supersede_decision_has_a_product_caller(self) -> None:
        """The emitter existed; nothing shipped ever called it."""
        package = Path(paths.REPO_ROOT) / "hearth" / "operator"
        callers = []
        for module in sorted(package.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else None)
                if name == "supersede_decision":
                    callers.append(f"{module.name}:{node.lineno}")
        self.assertTrue(
            callers,
            "supersede_decision is defined in hearth/operator/validate.py but no product "
            "module calls it: proposal supersession is not automatic (D-112 item 4)")

    def test_storing_a_superseding_proposal_appends_decision_superseded(self) -> None:
        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        self.validated(first)
        successor = self.successor_of(first)
        proposal_mod.store_proposal(successor, self.run_id)

        rows = self.events("decision.superseded")
        self.assertEqual(len(rows), 1,
                         "storing a superseding proposal appended no decision.superseded")
        payload = rows[-1]["payload"]
        self.assertEqual(payload["proposal_id"], first["proposal_id"])
        self.assertEqual(payload["successor_proposal_id"], successor["proposal_id"])
        self.assertTrue(payload.get("reason"))

    def test_supersession_is_deterministic_and_idempotent(self) -> None:
        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        self.validated(first)
        successor = self.successor_of(first)
        proposal_mod.store_proposal(successor, self.run_id)
        proposal_mod.store_proposal(successor, self.run_id)
        proposal_mod.store_proposal(successor, self.run_id)

        rows = self.events("decision.superseded")
        self.assertEqual(len(rows), 1,
                         "re-ingesting the same superseding proposal appended the "
                         "supersession again")
        self.assertEqual(rows[0]["payload"]["proposal_id"], first["proposal_id"])

    def test_a_supersedes_reference_that_names_no_stored_proposal_is_refused(self) -> None:
        """Resolution, not shape: a supersession names a proposal this run holds."""
        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        stranger = self.successor_of(first, supersedes="9" * 64)
        with self.assertRaises(proposal_mod.ProposalError) as ctx:
            proposal_mod.store_proposal(stranger, self.run_id)
        self.assertIn("supersedes", str(ctx.exception))
        self.assertEqual(self.events("decision.superseded"), [])
        self.assertFalse((paths.run_refs_dir(self.run_id)
                          / f"proposal_{stranger['proposal_id']}.json").is_file())

    def test_a_self_referencing_supersedes_cannot_survive_the_identity_check(self) -> None:
        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        successor = self.successor_of(first)
        tampered = json.loads(json.dumps(successor))
        tampered["supersedes"] = tampered["proposal_id"]
        with self.assertRaises(proposal_mod.ProposalError) as ctx:
            proposal_mod.store_proposal(tampered, self.run_id)
        self.assertIn("proposal_id", str(ctx.exception))

    def test_supersession_ends_an_approval_bound_to_the_old_proposal(self) -> None:
        first = self.proposal(required_authority=["merge_push_deploy"])
        proposal_mod.store_proposal(first, self.run_id)
        result = self.validated(first)
        self.assertEqual(result["verdict"], "needs_approval")
        approval_id = self.events("approval.requested")[-1]["payload"]["approval_id"]
        record = approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                         decision="approve", approver=self.approver(),
                                         now=self.now)
        context = {"proposal_id": record["proposal_id"],
                   "authorities": list(record["authorities"]),
                   "node_ids": list(record["node_ids"]),
                   "targets": list(record["targets"]),
                   "catalog_version": record["catalog_version"],
                   "snapshot_id": record["snapshot_id"]}
        live = approve.effective_approval(self.run_id, context, now=self.now)
        self.assertIsNotNone(live["record"], live["reason"])

        successor = self.successor_of(first)
        proposal_mod.store_proposal(successor, self.run_id)

        after = approve.effective_approval(self.run_id, context, now=self.now)
        self.assertIsNone(after["record"],
                          "an approval bound to a superseded proposal is still usable")
        self.assertIn("supersed", after["reason"])

    def test_a_superseded_proposal_no_longer_validates(self) -> None:
        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        self.assertEqual(self.validated(first)["verdict"], "validated")
        successor = self.successor_of(first)
        proposal_mod.store_proposal(successor, self.run_id)

        again = validate.validate_proposal(first, self.caller, catalog=self.catalog,
                                           current_snapshot=self.snapshot, now=self.now,
                                           run_id=self.run_id)
        self.assertEqual(again["verdict"], "rejected")
        self.assertIn("proposal_superseded",
                      [row["reason_code"] for row in again["checks"]])
        self.assertTrue(all(row["remedy"] for row in again["checks"]
                            if row["result"] != "passed"))


class DecisionTerminationTests(SupersessionBase):
    """Who may close an open approval decision, and who may not."""

    def test_a_route_rejected_row_does_not_close_another_callers_open_decision(self) -> None:
        """The observed suppression: one caller's authority rejection silenced
        the `decision.invalidated` owed to a decision that was still open."""
        proposal = self.proposal(required_authority=["write_worktree"])
        proposal_mod.store_proposal(proposal, self.run_id)
        self.assertEqual(self.validated(proposal)["verdict"], "validated")

        self.set_key(RESEARCH_KEY)
        research = resolve_from_env()
        rejected = self.validated(proposal, caller=research)
        self.assertEqual(rejected["verdict"], "rejected",
                         "this test needs a caller whose authority is denied")
        self.set_key(UNRESTRICTED_KEY)
        self.assertTrue(self.events("route.rejected"))

        self.material_refresh()
        invalidated = self.events("decision.invalidated")
        self.assertTrue(
            invalidated,
            "a route.rejected row from another caller closed an open decision, so the "
            "superseded snapshot never invalidated it")
        self.assertEqual(invalidated[-1]["payload"]["proposal_id"], proposal["proposal_id"])

    def test_a_forged_human_decided_rejection_does_not_close_an_open_decision(self) -> None:
        proposal = self.proposal(required_authority=["write_worktree"])
        proposal_mod.store_proposal(proposal, self.run_id)
        self.assertEqual(self.validated(proposal)["verdict"], "validated")

        target = paths.run_history_path(self.run_id)
        rows = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        forged = {
            "contract_version": "operator-history-event.v1",
            "sequence": len(rows) + 1,
            "timestamp": "2026-09-17T12:02:00Z",
            "run_id": self.run_id,
            "event_type": "human.decided",
            "refs": {},
            "payload": {"approval_id": "f" * 64, "receipt_id": "e" * 64,
                        "proposal_id": proposal["proposal_id"], "validation_id": "b" * 64,
                        "decision": "reject", "approving_principal": "fixture-approver",
                        "scope": "one_use"},
        }
        forged["event_id"] = canonical.sha256_hex(canonical.canonical_json(forged))
        with open(target, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")

        self.material_refresh()
        invalidated = self.events("decision.invalidated")
        self.assertTrue(invalidated,
                        "a forged human.decided rejection closed an open decision: a "
                        "history row is not authoritative because it exists (D-104)")
        self.assertEqual(invalidated[-1]["payload"]["proposal_id"], proposal["proposal_id"])

    def test_a_verified_human_rejection_closes_the_open_decision(self) -> None:
        proposal = self.proposal(required_authority=["merge_push_deploy"])
        proposal_mod.store_proposal(proposal, self.run_id)
        self.assertEqual(self.validated(proposal)["verdict"], "needs_approval")
        approval_id = self.events("approval.requested")[-1]["payload"]["approval_id"]
        record = approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                         decision="reject", approver=self.approver(),
                                         now=self.now)
        self.assertEqual(record["receipt"]["decision"], "reject")

        self.material_refresh()
        invalidated = [ev for ev in self.events("decision.invalidated")
                       if ev["payload"].get("proposal_id") == proposal["proposal_id"]]
        self.assertEqual(
            invalidated, [],
            "a decision a human rejected is closed; invalidating it again re-opens a "
            "question that was already answered")

    def test_a_validation_rejection_does_not_impersonate_a_human_rejection(self) -> None:
        proposal = self.proposal(required_authority=["merge_push_deploy"])
        proposal_mod.store_proposal(proposal, self.run_id)
        self.assertEqual(self.validated(proposal)["verdict"], "needs_approval")
        approval_id = self.events("approval.requested")[-1]["payload"]["approval_id"]

        self.set_key(RESEARCH_KEY)
        research = resolve_from_env()
        self.validated(proposal, caller=research)
        self.set_key(UNRESTRICTED_KEY)

        self.assertEqual(self.events("human.decided"), [],
                         "a deterministic validation rejection recorded a human decision")
        record = approve.load_approval(self.run_id, approval_id)
        self.assertIsNone(record["receipt"],
                          "a validation rejection decided a pending approval request")
        decided = approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                          decision="approve", approver=self.approver(),
                                          now=self.now)
        self.assertEqual(decided["receipt"]["decision"], "approve")


class ReplayAndExplainTests(SupersessionBase):
    def test_replay_reports_a_superseded_decision(self) -> None:
        from hearth.operator import replay

        first = self.proposal()
        proposal_mod.store_proposal(first, self.run_id)
        self.validated(first)
        successor = self.successor_of(first)
        proposal_mod.store_proposal(successor, self.run_id)

        state = replay.replay_run(self.run_id)
        self.assertIn(first["proposal_id"], state["proposals"])
        self.assertIn(successor["proposal_id"], state["proposals"])
        second = replay.replay_run(self.run_id, check=True)
        self.assertEqual(state, second)


if __name__ == "__main__":
    unittest.main()
