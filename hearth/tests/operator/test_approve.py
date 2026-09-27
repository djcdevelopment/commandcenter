"""Tests for the D-112 approval boundary and lifecycle (G2-C6, G2-C7, G2-C11).

The criteria are unchanged from WI-G2; the fixtures now carry the full binding
an approval identity is computed over, because in WI-G2a the identity is
recomputed on every load.
"""

import json
import unittest

from hearth.operator import (approve, canonical, catalog as catalog_mod, history, paths)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (HUMAN_APPROVER_KEY, HUMAN_REQUESTER_KEY,
                                           OperatorTestCase, ORCHESTRATOR_KEY,
                                           RESEARCH_KEY, UNRESTRICTED_KEY)


class ApprovalBoundaryTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-approval-test"
        self.proposal_id = "a" * 64
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")

    def request(self, **overrides) -> dict:
        fields = {
            "run_id": self.run_id,
            "proposal_id": self.proposal_id,
            "validation_id": "b" * 64,
            "node_ids": ["node-1"],
            "targets": ["direct_hearth"],
            "authorities": ["merge_push_deploy"],
            "catalog_version": str(self.catalog["catalog_version"]),
            "snapshot_id": "c" * 64,
            "requester": self.requester_context(ORCHESTRATOR_KEY),
            "now": self.now,
        }
        fields.update(overrides)
        return approve.request_approval(**fields)

    def test_g2_c6_approval_lifecycle_and_human_decided_invariant(self) -> None:
        """G2-C6: request_approval → approval.requested → decide_approval →
        human.decided. No `human.decided` without an actual human decision."""
        record = self.request()
        approval_id = record["approval_id"]
        self.assertEqual(approval_id, canonical.approval_identity(record))
        self.assertTrue(record["expires_at"] > record["requested_at"])

        events = history.read_run_history(self.run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "approval.requested")
        self.assertEqual(events[0]["payload"]["approval_id"], approval_id)
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))

        self.set_key(HUMAN_APPROVER_KEY)
        decided = approve.decide_approval(
            run_id=self.run_id, approval_id=approval_id, decision="approve",
            approver=resolve_from_env(), scope="one_use", now=self.now)
        self.assertEqual(decided["receipt"]["decision"], "approve")
        self.assertEqual(decided["receipt"]["approving_principal"]["id"], "fixture-approver")
        self.assertEqual(decided["receipt"]["receipt_id"],
                         canonical.receipt_identity(decided["receipt"]))

        decided_event = history.last_of("human.decided", paths.run_history_path(self.run_id))
        self.assertIsNotNone(decided_event)
        self.assertEqual(decided_event["payload"]["decision"], "approve")
        self.assertEqual(decided_event["payload"]["approving_principal"], "fixture-approver")
        self.assertEqual(decided_event["payload"]["receipt_id"],
                         decided["receipt"]["receipt_id"])

    def test_g2_c11_agent_self_approval_rejection(self) -> None:
        """G2-C11: an agent caller — even `unrestricted` — lacks `approve`."""
        record = self.request()
        for key in (UNRESTRICTED_KEY, ORCHESTRATOR_KEY, RESEARCH_KEY):
            with self.subTest(caller=key):
                self.set_key(key)
                with self.assertRaises(approve.ApprovalError) as ctx:
                    approve.decide_approval(run_id=self.run_id,
                                            approval_id=record["approval_id"],
                                            decision="approve", approver=resolve_from_env(),
                                            now=self.now)
                self.assertIn("approve", str(ctx.exception))
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))

    def test_g2_c11_self_approval_prevention(self) -> None:
        """G2-C11: the requesting principal cannot approve its own request, and a
        disinterested human can."""
        record = self.request(requester=self.requester_context(HUMAN_REQUESTER_KEY))

        self.set_key(HUMAN_REQUESTER_KEY)
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                    decision="approve", approver=resolve_from_env(),
                                    now=self.now)
        self.assertIn("self-approval", str(ctx.exception))

        self.set_key(HUMAN_APPROVER_KEY)
        decided = approve.decide_approval(run_id=self.run_id,
                                          approval_id=record["approval_id"],
                                          decision="approve", approver=resolve_from_env(),
                                          now=self.now)
        self.assertEqual(decided["receipt"]["decision"], "approve")

    def test_invalid_decision_values_rejected(self) -> None:
        """Only `approve` or `reject`, and nothing is written before the check."""
        record = self.request()
        self.set_key(HUMAN_APPROVER_KEY)
        approver = resolve_from_env()
        for decision in ("maybe", "APPROVE", "approve_with_conditions", "", None):
            with self.subTest(decision=decision):
                with self.assertRaises(approve.ApprovalError):
                    approve.decide_approval(run_id=self.run_id,
                                            approval_id=record["approval_id"],
                                            decision=decision, approver=approver,
                                            now=self.now)
        self.assertIsNone(history.last_of("human.decided",
                                          paths.run_history_path(self.run_id)))

    def test_a_decided_approval_cannot_be_decided_twice(self) -> None:
        record = self.request()
        self.set_key(HUMAN_APPROVER_KEY)
        approver = resolve_from_env()
        approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                decision="approve", approver=approver, now=self.now)
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                    decision="approve", approver=approver, now=self.now)
        self.assertIn("already been decided", str(ctx.exception))

    def test_an_unknown_approval_is_refused(self) -> None:
        self.set_key(HUMAN_APPROVER_KEY)
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.decide_approval(run_id=self.run_id, approval_id="d" * 64,
                                    decision="approve", approver=resolve_from_env(),
                                    now=self.now)
        self.assertIn("no approval request found", str(ctx.exception))

    def test_the_stored_record_verifies_with_verify_ids(self) -> None:
        """`verify-ids` recomputes an approval's binding and its receipt."""
        record = self.request()
        self.set_key(HUMAN_APPROVER_KEY)
        approve.decide_approval(run_id=self.run_id, approval_id=record["approval_id"],
                                decision="approve", approver=resolve_from_env(),
                                now=self.now)
        stored = json.loads(approve.approval_path(self.run_id, record["approval_id"])
                            .read_text(encoding="utf-8"))
        rows = canonical.verify_document(stored)
        self.assertEqual(sorted(row["field"] for row in rows),
                         ["approval_id", "receipt_id"])
        self.assertTrue(all(row["ok"] for row in rows))


if __name__ == "__main__":
    unittest.main()
