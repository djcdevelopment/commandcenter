"""Tests for route validation logic (G2-C4, G2-C5, G2-C7, G2-C8, G2-C9).

The criteria are unchanged from WI-G2. What changed in WI-G2a is that the
fixtures are documents that pass their contract and a validation that actually
reads capacity, so each criterion is exercised against the real check rather
than against a validator that ignored half its inputs.
"""

import copy
import unittest
from datetime import timedelta

from hearth.operator import (canonical, catalog as catalog_mod, envelope as envelope_mod,
                             history, paths, proposal, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (OperatorTestCase, RESEARCH_KEY, UNRESTRICTED_KEY)


class RouteValidationTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.catalog_version = self.catalog["catalog_version"]
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

        self.fixed_now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.fixed_now)
        self.snapshot_id = self.snapshot["snapshot_id"]
        self.snapshot_expires_at = self.snapshot["planning_valid_until"]

        self.run_id = "run-validate-tests"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.valid_prop = self.make_proposal(self.catalog, self.snapshot,
                                             envelope_id=self.envelope["envelope_id"])

    def validate(self, prop, *, now=None, caller=None):
        return validate.validate_proposal(prop, caller or self.caller,
                                          catalog=self.catalog,
                                          current_snapshot=self.snapshot,
                                          now=now or self.fixed_now,
                                          run_id=self.run_id)

    def test_g2_c8_validation_purity(self) -> None:
        """G2-C8: Pure check. Never modifies the proposal, never substitutes a
        route, and answers only validated / rejected / needs_approval."""
        prop_copy = copy.deepcopy(self.valid_prop)
        prop_bytes_before = canonical.canonical_json(self.valid_prop)

        result = self.validate(self.valid_prop)

        prop_bytes_after = canonical.canonical_json(self.valid_prop)
        self.assertEqual(prop_bytes_before, prop_bytes_after)
        self.assertEqual(self.valid_prop, prop_copy)

        self.assertIn(result["verdict"], ("validated", "rejected", "needs_approval"))
        self.assertEqual(result["verdict"], "validated", result["reasons"])
        self.assertEqual(result["proposal_copy"]["proposal_id"], self.valid_prop["proposal_id"])
        self.assertEqual(canonical.canonical_json(result["proposal_copy"]), prop_bytes_before)

        # A deep copy, not an alias: mutating the embedded copy cannot reach back
        # into the caller's proposal.
        result["proposal_copy"]["selected_graph"]["nodes"][0]["target"] = "hijacked"
        self.assertEqual(self.valid_prop["selected_graph"]["nodes"][0]["target"],
                         "direct_hearth")

    def test_g2_c4_catalog_mismatch_rejection(self) -> None:
        """G2-C4: A proposal citing a mismatched catalog version is rejected."""
        bad_prop = copy.deepcopy(self.valid_prop)
        bad_prop["catalog_version"] = "0" * 64
        bad_prop["proposal_id"] = canonical.identity_of(bad_prop, "proposal_id")

        result = self.validate(bad_prop)
        self.assertEqual(result["verdict"], "rejected")
        failed = [c for c in result["checks"] if c["check_id"] == "catalog_freshness"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["result"], "failed")
        self.assertEqual(failed[0]["reason_code"], "catalog_mismatch")
        self.assertTrue(failed[0]["remedy"])

    def test_g2_c4_snapshot_expired_rejection(self) -> None:
        """G2-C4: An expired planning window is rejected with snapshot_expired."""
        after_expiry = self.fixed_now + timedelta(minutes=10)
        result = self.validate(self.valid_prop, now=after_expiry)
        self.assertEqual(result["verdict"], "rejected")
        failed = [c for c in result["checks"] if c["check_id"] == "snapshot_freshness"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["result"], "failed")
        self.assertEqual(failed[0]["reason_code"], "snapshot_expired")

    def test_g2_c4_snapshot_invalidated_rejection(self) -> None:
        """G2-C4: A proposal citing an invalidated snapshot is rejected."""
        history.append(
            "snapshot.invalidated",
            {"snapshot_id": self.snapshot_id, "successor_snapshot_id": "f" * 64,
             "changed_fields": ["readiness"]},
        )
        result = self.validate(self.valid_prop)
        self.assertEqual(result["verdict"], "rejected")
        failed = [c for c in result["checks"] if c["check_id"] == "snapshot_validity"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["reason_code"], "snapshot_invalidated")

    def test_g2_c5_built_not_deployed_rejection_in_normal_mode(self) -> None:
        """G2-C5: A target whose catalog status is BUILT NOT DEPLOYED is rejected
        in normal mode."""
        prop = self.make_proposal(
            self.catalog, self.snapshot,
            envelope_id=self.envelope["envelope_id"],
            selected_graph={"nodes": [{"id": "n1", "route_kind": "planning",
                                       "target": "propose_schedule",
                                       "inputs": {}, "expected": {}}], "edges": []},
            rationale="Attempting a target that is built but not deployed.")
        result = self.validate(prop)
        self.assertEqual(result["verdict"], "rejected")
        failed = [c for c in result["checks"]
                  if c["check_id"] == "implementation_status_propose_schedule"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["reason_code"], "implementation_not_live")

    def test_g2_c5_test_mode_policy_enforcement(self) -> None:
        """G2-C5 / D-113: test mode needs every companion, resolved and verified,
        and still requires human approval."""
        graph = {"nodes": [{"id": "n1", "route_kind": "planning",
                            "target": "propose_schedule",
                            "inputs": {}, "expected": {}}], "edges": []}
        complete = {
            "work_item": "WI-G2a",
            "isolated_inputs": [r"/home/derek/work/worktrees/commandcenter/g2a-envelope-hardening"],
            "isolated_outputs": [r"/home/derek/work/worktrees/commandcenter/g2a-envelope-hardening-evidence"],
            # Real, immutable objects in this repository: the frozen WI-G2a
            # candidate and the failed WI-G2 candidate. Since WI-G2b these are
            # RESOLVED with git cat-file, so a placeholder no longer passes.
            "base_commit": "7ff409e229ab10e7626045733ef11794b00e3e08",
            "base_tree": "2f7ecbe999e2b67a3479148d5af6f49f97ed3903",
            "bounded_authorities": ["call_door_generate"],
            "rollback_ref": "a2d7a9197a1103fcf7af06d384f67617d5b86068",
            "recovery_instructions": "git -C <worktree> reset --hard <rollback_ref>",
            "test_targets": ["propose_schedule"],
        }

        good = self.make_proposal(self.catalog, self.snapshot, mode="test",
                                  envelope_id=self.envelope["envelope_id"],
                                  selected_graph=graph, test_mode_fields=complete,
                                  required_approvals=["human-operator"],
                                  rationale="Test-mode exercise of a built-not-deployed target.")
        result_good = self.validate(good)
        self.assertEqual(result_good["verdict"], "needs_approval", result_good["reasons"])
        self.assertTrue(result_good["test_mode"])

        # An unreleased work item is refused: presence is not resolution.
        unresolved = dict(complete, work_item="WI-NOT-RELEASED")
        bad = self.make_proposal(self.catalog, self.snapshot, mode="test",
                                 envelope_id=self.envelope["envelope_id"],
                                 selected_graph=graph, test_mode_fields=unresolved,
                                 required_approvals=["human-operator"],
                                 rationale="Test-mode proposal naming no released work item.")
        result_bad = self.validate(bad)
        self.assertEqual(result_bad["verdict"], "rejected")

    def test_g2_c7_non_overridable_denial(self) -> None:
        """G2-C7: An authority evaluated as denied stays denied."""
        self.set_key(RESEARCH_KEY)
        caller = resolve_from_env()

        prop = self.make_proposal(self.catalog, self.snapshot,
                                  envelope_id=self.envelope["envelope_id"],
                                  required_authority=["write_worktree"],
                                  rationale="Attempting an unauthorized write.")
        result = self.validate(prop, caller=caller)
        self.assertEqual(result["verdict"], "rejected")
        auth = [c for c in result["checks"] if c["check_id"] == "authority_write_worktree"]
        self.assertEqual(len(auth), 1)
        self.assertEqual(auth[0]["result"], "failed")
        self.assertEqual(auth[0]["reason_code"], "authority_missing")

    def test_g2_c9_decision_invalidation(self) -> None:
        """G2-C9: `decision.invalidated` names the proposal it invalidates."""
        run_id = "run-val-invalidation"
        event = validate.invalidate_decision(run_id, self.valid_prop["proposal_id"],
                                             "am4 went offline")
        self.assertEqual(event["event_type"], "decision.invalidated")
        self.assertEqual(event["payload"]["proposal_id"], self.valid_prop["proposal_id"])
        self.assertEqual(event["payload"]["reason"], "am4 went offline")
        events = history.read_run_history(run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "decision.invalidated")

    def test_every_refusal_states_a_reason_code_and_a_remedy(self) -> None:
        """A refusal a caller cannot act on is not a refusal."""
        bad = copy.deepcopy(self.valid_prop)
        bad["catalog_version"] = "0" * 64
        bad["expected"] = {"time_s": 1, "attempts": 9999, "context_tokens": 100_000_000,
                           "resources": ["omen-arc"]}
        bad["proposal_id"] = canonical.identity_of(bad, "proposal_id")
        result = self.validate(bad)
        self.assertEqual(result["verdict"], "rejected")
        refusals = [c for c in result["checks"] if c["result"] != "passed"]
        self.assertTrue(refusals)
        for row in refusals:
            with self.subTest(check=row["check_id"]):
                self.assertIn(row["reason_code"], validate.VALIDATION_REASON_CODES)
                self.assertTrue(row["reason"])
                self.assertTrue(row["remedy"])


if __name__ == "__main__":
    unittest.main()
