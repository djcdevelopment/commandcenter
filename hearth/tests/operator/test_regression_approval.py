"""Regression tests for the approval-boundary exploits reproduced by the WI-G2
independent verification (report sections 6 and 8, appendix A section 6).

Each test is named for the exploit it reproduces. Every one of them was run red
against the failed candidate `a2d7a91` before the fix that makes it green. The
WI-G2a red run is `regressions-red.txt` in that candidate's evidence drop; the
WI-G2b run, regenerated against these FROZEN files with the per-test table
condition 6 requires, is `regressions-red-g2.txt`.

No test mints, enters, or looks for the real `derek-approver` credential: the
registry is a temporary file holding ephemeral fixture strings only.
"""

from __future__ import annotations

import inspect
import json
import unittest
from datetime import timedelta

from hearth.operator import (approve, canonical, catalog as catalog_mod, envelope as envelope_mod,
                             history, paths, replay, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (HUMAN_APPROVER_KEY, HUMAN_REQUESTER_KEY,
                                           OperatorTestCase, ORCHESTRATOR_KEY,
                                           RESEARCH_KEY, UNRESTRICTED_KEY)


class ApprovalRegressionBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-approval-regression"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.proposal = self.make_proposal(
            self.catalog, self.snapshot,
            envelope_id=self.envelope["envelope_id"],
            required_authority=["call_door_generate", "merge_push_deploy"],
            selected_graph={"nodes": [{"id": "n1", "route_kind": "direct_inference",
                                       "target": "direct_hearth",
                                       "inputs": {"envelope_id": self.envelope["envelope_id"]},
                                       "expected": {"attempts": 1}}],
                            "edges": []},
        )
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    def request(self, **overrides):
        """A pending approval request bound to this run's proposal."""
        fields = {
            "run_id": self.run_id,
            "proposal_id": self.proposal["proposal_id"],
            "validation_id": "b" * 64,
            "node_ids": ["n1"],
            "targets": ["direct_hearth"],
            "authorities": ["merge_push_deploy"],
            "catalog_version": str(self.catalog["catalog_version"]),
            "snapshot_id": str(self.snapshot["snapshot_id"]),
            "now": self.now,
        }
        fields.update(self.approval_requester_fields(ORCHESTRATOR_KEY))
        fields.update(overrides)
        return approve.request_approval(**fields)

    def approver(self):
        self.set_key(HUMAN_APPROVER_KEY)
        return resolve_from_env()

    def record_path(self, approval_id: str):
        return paths.run_refs_dir(self.run_id) / f"approval_{approval_id}.json"

    def rewrite_record(self, approval_id: str, mutate) -> dict:
        target = self.record_path(approval_id)
        record = json.loads(target.read_text(encoding="utf-8"))
        mutate(record)
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        return record


class AlteredApprovalRequestTests(ApprovalRegressionBase):
    """Exploit 1: a pending approval request can be widened before the human
    signs it, because `decide_approval` never recomputes `approval_id`."""

    def test_widened_approval_request_is_refused(self) -> None:
        record = self.request()
        approval_id = record["approval_id"]
        self.rewrite_record(approval_id, lambda doc: doc.update({
            "authorities": ["merge_push_deploy", "change_machine_or_network"],
            "node_ids": ["n1", "n2", "n3"],
        }))
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                    decision="approve", approver=self.approver(),
                                    now=self.now)
        message = str(ctx.exception)
        self.assertIn("approval_id", message)
        self.assertIn(approval_id[:16], message)
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))

    def test_every_bound_field_mutation_invalidates_the_approval(self) -> None:
        """D-112 item 3: run id, proposal id, validation id, authority set,
        selected nodes and targets, catalog version, snapshot id, requester,
        expiry, contract and policy versions."""
        mutations = {
            "run_id": "run-somewhere-else",
            "proposal_id": "9" * 64,
            "validation_id": "8" * 64,
            "authorities": ["merge_push_deploy", "change_machine_or_network"],
            "node_ids": ["n1", "n2"],
            "targets": ["direct_hearth", "mechnet_build"],
            "catalog_version": "7" * 64,
            "snapshot_id": "6" * 64,
            "expires_at": "2027-09-17T12:00:00Z",
            "policy_version": "5" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(bound_field=field):
                record = self.request()
                approval_id = record["approval_id"]
                self.rewrite_record(approval_id, lambda doc, f=field, v=value: doc.update({f: v}))
                with self.assertRaises(approve.ApprovalError):
                    approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                            decision="approve", approver=self.approver(),
                                            now=self.now)

    def test_requester_identity_is_bound(self) -> None:
        record = self.request()
        approval_id = record["approval_id"]
        self.rewrite_record(approval_id, lambda doc: doc.update({
            "requested_by": {"id": "someone-else", "profile": "orchestrator",
                             "runner_class": "local"}}))
        with self.assertRaises(approve.ApprovalError):
            approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                    decision="approve", approver=self.approver(),
                                    now=self.now)


class ApprovalExpiryAndRevocationTests(ApprovalRegressionBase):
    """Exploit: no expiry and no revocation path exist (D-112 item 4/5)."""

    def test_approval_request_carries_an_expiry_capped_at_24_hours(self) -> None:
        record = self.request(ttl_s=72 * 3600)
        expires = canonical.parse_rfc3339(record["expires_at"])
        self.assertLessEqual(expires - self.now, timedelta(hours=24))
        self.assertGreater(expires, self.now)

    def test_expired_approval_cannot_be_decided(self) -> None:
        record = self.request()
        later = canonical.parse_rfc3339(record["expires_at"]) + timedelta(seconds=1)
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                    decision="approve", approver=self.approver(), now=later)
        self.assertIn("expired", str(ctx.exception))

    def test_expired_approval_does_not_authorize_validation(self) -> None:
        record = self.decided_approval()
        later = canonical.parse_rfc3339(record["expires_at"]) + timedelta(seconds=1)
        found = approve.effective_approval(
            self.run_id, self.context(record), now=later)
        self.assertIsNone(found["record"])
        self.assertIn("expired", found["reason"])

    def test_revoked_approval_is_refused_and_never_restored(self) -> None:
        record = self.decided_approval()
        self.set_key(HUMAN_APPROVER_KEY)
        approve.revoke_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                revoker=resolve_from_env(),
                                reason="capacity shifted under the route", now=self.now)
        events = [ev["event_type"] for ev in history.read_run_history(self.run_id)]
        self.assertIn("approval.revoked", events)

        found = approve.effective_approval(self.run_id, self.context(record), now=self.now)
        self.assertIsNone(found["record"])
        self.assertIn("revoked", found["reason"])

        # A second decision cannot resurrect it.
        with self.assertRaises(approve.ApprovalError):
            approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                    decision="approve", approver=self.approver(), now=self.now)
        found_again = approve.effective_approval(self.run_id, self.context(record), now=self.now)
        self.assertIsNone(found_again["record"])

    def test_approval_revoked_is_in_the_closed_event_set(self) -> None:
        self.assertIn("approval.revoked", history.EVENT_TYPES)

    # helpers -------------------------------------------------------------
    def context(self, record: dict) -> dict:
        return {
            "proposal_id": record["proposal_id"],
            "validation_id": record["validation_id"],
            "authorities": list(record["authorities"]),
            "node_ids": list(record["node_ids"]),
            "targets": list(record["targets"]),
            "catalog_version": record["catalog_version"],
            "snapshot_id": record["snapshot_id"],
        }

    def decided_approval(self) -> dict:
        record = self.request()
        return approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                       decision="approve", approver=self.approver(),
                                       now=self.now)


class ApprovedIdsBypassTests(ApprovalRegressionBase):
    """Exploit 2: `validate_proposal(..., approved_ids={"all"})` turned every
    human_required authority into validated with no human decision anywhere."""

    def test_validate_proposal_no_longer_accepts_approved_ids(self) -> None:
        """Repaired for WI-G2b condition 6.

        The WI-G2a version called `validate_proposal(..., run_id=...,
        approved_ids=...)`, and the old signature had no `run_id` either — so it
        raised TypeError against the failed candidate for the WRONG reason and
        proved nothing about `approved_ids`. Every assertion below names
        `approved_ids` and nothing else: the parameter is absent from the
        signature; binding a call that passes ONLY it is a TypeError that says
        so; and the same call at runtime is refused before any work is done.
        """
        signature = inspect.signature(validate.validate_proposal)
        self.assertNotIn(
            "approved_ids", signature.parameters,
            "validate_proposal still takes a caller-supplied approval set "
            f"(signature: {signature})")

        with self.assertRaises(TypeError) as bound:
            signature.bind(self.proposal, self.caller, approved_ids={"all"})
        self.assertIn("approved_ids", str(bound.exception))

        with self.assertRaises(TypeError) as called:
            validate.validate_proposal(self.proposal, self.caller,
                                       approved_ids={"all"})  # type: ignore[call-arg]
        self.assertIn("approved_ids", str(called.exception))
        self.assertIsNone(history.last_of("route.validated",
                                          paths.run_history_path(self.run_id)))

    def test_human_required_authority_without_a_decision_is_needs_approval(self) -> None:
        result = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.assertEqual(result["verdict"], "needs_approval")
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))

    def test_validation_passes_only_with_a_verified_decision_receipt(self) -> None:
        """The authoritative decision path end to end: validate -> request ->
        human decision -> re-validate."""
        first = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                           current_snapshot=self.snapshot, now=self.now,
                                           run_id=self.run_id)
        self.assertEqual(first["verdict"], "needs_approval")
        self.store_validation_as(first, self.run_id, self.caller)

        requested = [ev for ev in history.read_run_history(self.run_id)
                     if ev["event_type"] == "approval.requested"]
        self.assertTrue(requested, "needs_approval must record an approval request")
        approval_id = requested[-1]["payload"]["approval_id"]
        self.assertTrue(approval_id)

        decided = approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                          decision="approve", approver=self.approver(),
                                          proposal=self.proposal, now=self.now)
        self.assertEqual(decided["receipt"]["decision"], "approve")

        self.set_key(UNRESTRICTED_KEY)
        second = validate.validate_proposal(self.proposal, resolve_from_env(),
                                            catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.assertEqual(second["verdict"], "validated")
        self.assertEqual([row["approval_id"] for row in second["approvals"]], [approval_id])

    def test_a_denied_authority_is_never_approved_into_a_grant(self) -> None:
        """G2-C7 through the authoritative path: approval adds no capability."""
        self.set_key(RESEARCH_KEY)
        caller = resolve_from_env()
        proposal = self.make_proposal(
            self.catalog, self.snapshot,
            envelope_id=self.envelope["envelope_id"],
            required_authority=["write_worktree"],
        )
        record = self.request(proposal_id=proposal["proposal_id"],
                              authorities=["write_worktree"])
        approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                decision="approve", approver=self.approver(), now=self.now)
        result = validate.validate_proposal(proposal, caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.assertEqual(result["verdict"], "rejected")


class ForgedHistoryTests(ApprovalRegressionBase):
    """Exploit 3: a hand-written `human.decided` row survived replay as a valid
    approval (D-104: a history row alone never grants authority)."""

    def forge(self, event: dict) -> None:
        target = paths.run_history_path(self.run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def next_sequence(self) -> int:
        target = paths.run_history_path(self.run_id)
        if not target.is_file():
            return 1
        return len([line for line in target.read_text(encoding="utf-8").splitlines()
                    if line.strip()]) + 1

    def forged_row(self, *, valid_identity: bool) -> dict:
        event = {
            "contract_version": "operator-history-event.v1",
            "sequence": self.next_sequence(),
            "timestamp": "2026-09-17T12:30:00Z",
            "run_id": self.run_id,
            "event_type": "human.decided",
            "refs": {},
            "payload": {"approval_id": "f" * 64, "receipt_id": "e" * 64,
                        "proposal_id": self.proposal["proposal_id"],
                        "decision": "approve", "approving_principal": "fixture-approver",
                        "scope": "one_use"},
        }
        event["event_id"] = (canonical.sha256_hex(canonical.canonical_json(event))
                             if valid_identity else "0" * 64)
        return event

    def test_forged_human_decided_with_a_bogus_event_id_is_refused(self) -> None:
        history.append("task.received", {"envelope_id": self.envelope["envelope_id"]},
                       run_id=self.run_id)
        self.forge(self.forged_row(valid_identity=False))
        with self.assertRaises(Exception) as ctx:
            replay.replay_run(self.run_id)
        self.assertIn("event_id", str(ctx.exception))

    def test_forged_human_decided_with_a_recomputed_event_id_grants_no_authority(self) -> None:
        """Even a well-formed row is uncorroborated: no approval record, no
        receipt, so replay must not reconstruct an approved run."""
        history.append("task.received", {"envelope_id": self.envelope["envelope_id"]},
                       run_id=self.run_id)
        self.forge(self.forged_row(valid_identity=True))
        state = replay.replay_run(self.run_id)
        self.assertNotEqual(state["status"], "approved")
        self.assertEqual(state["approvals"], [])
        self.assertTrue(state["unverified_decisions"],
                        "an uncorroborated human.decided must be reported, not honoured")

    def test_replay_verifies_the_approval_relationship(self) -> None:
        """The genuine path replays to approved; tampering with the stored
        approval record afterwards takes the authority away again."""
        record = self.request()
        approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                decision="approve", approver=self.approver(), now=self.now)
        state = replay.replay_run(self.run_id)
        self.assertEqual(state["status"], "approved")
        self.assertEqual(state["unverified_decisions"], [])

        self.rewrite_record(record["approval_id"], lambda doc: doc.update(
            {"authorities": ["merge_push_deploy", "change_machine_or_network"]}))
        tampered = replay.replay_run(self.run_id)
        self.assertNotEqual(tampered["status"], "approved")
        self.assertTrue(tampered["unverified_decisions"])


class IdentityBoundaryTests(ApprovalRegressionBase):
    """Self-approval and mismatched identities refused at every API boundary."""

    def test_library_refuses_agents_and_self_approval(self) -> None:
        record = self.request()
        for key in (UNRESTRICTED_KEY, ORCHESTRATOR_KEY, RESEARCH_KEY):
            with self.subTest(caller=key):
                self.set_key(key)
                with self.assertRaises(approve.ApprovalError):
                    approve.decide_approval(run_id=self.run_id,
                                            approval_id=record["approval_id"],
                                            decision="approve", approver=resolve_from_env(),
                                            now=self.now)

        human_request = self.request(
            **self.approval_requester_fields(HUMAN_REQUESTER_KEY))
        self.set_key(HUMAN_REQUESTER_KEY)
        with self.assertRaises(approve.ApprovalError):
            approve.decide_approval(run_id=self.run_id,
                                    approval_id=human_request["approval_id"],
                                    decision="approve", approver=resolve_from_env(),
                                    now=self.now)

    def test_the_proposals_orchestrator_cannot_approve_its_own_route(self) -> None:
        """The approver is matched against the stored proposal even when the
        caller does not hand one in."""
        proposal = self.make_proposal(
            self.catalog, self.snapshot,
            envelope_id=self.envelope["envelope_id"],
            orchestrator={**{"provider": "anthropic", "model": "claude-opus",
                             "endpoint_or_version": "2026-09", "harness": "cli"},
                          "client": "fixture-approver", "session": "fixture-approver"},
        )
        from hearth.operator import proposal as proposal_mod
        proposal_mod.store_proposal(proposal, self.run_id)
        record = self.request(proposal_id=proposal["proposal_id"])
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                    decision="approve", approver=self.approver(), now=self.now)
        self.assertIn("self-approval", str(ctx.exception))

    def test_door_mount_refuses_an_agent(self) -> None:
        from hearth.toolsurface import operator as operator_tools

        record = self.request()
        self.set_key(UNRESTRICTED_KEY)
        agent = resolve_from_env()
        original = operator_tools.resolve_from_door
        operator_tools.resolve_from_door = lambda: agent
        try:
            result = operator_tools.operator_approve(self.run_id, record["approval_id"],
                                                     "approve")
        finally:
            operator_tools.resolve_from_door = original
        self.assertFalse(result["ok"])
        self.assertIn("approve", result["error"])
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))


if __name__ == "__main__":
    unittest.main()
