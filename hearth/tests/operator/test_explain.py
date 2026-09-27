"""Tests for the run explanation generator against the six-part rubric (G2-C12)."""

import unittest

from hearth.operator import (canonical, catalog as catalog_mod, envelope as envelope_mod,
                             explain, history, proposal as proposal_mod, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import OperatorTestCase, UNRESTRICTED_KEY


class RunExplainTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-explain-rubric"
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    def test_g2_c12_explanation_rubric(self) -> None:
        """G2-C12: the six parts, rendered from what the record holds — what was
        available, believed, considered, chosen and decided."""
        env = self.make_envelope(intent="Test explanation generation",
                                 acceptance_criteria=["Criterion A", "Criterion B"],
                                 submitted_by="claude-runner")
        envelope_mod.store_envelope(env, self.run_id)

        prop = self.make_proposal(
            self.catalog, self.snapshot, envelope_id=env["envelope_id"],
            rejected_routes=[{"route_id": "propose_schedule", "route_kind": "planning",
                              "target": "propose_schedule",
                              "reason_code": "implementation_not_live",
                              "reason_detail": "status is BUILT NOT DEPLOYED"}],
            assumptions=["omen-arc is ready and zero-marginal-cost"],
            uncertainty={"confidence": "high", "unknowns": ["decode rate at depth"]},
            rationale="Omen-arc is ready and zero-marginal-cost.")
        proposal_mod.store_proposal(prop, self.run_id)

        result = validate.validate_proposal(prop, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.store_validation_as(result, self.run_id, self.caller)

        narrative = explain.explain_run(self.run_id)

        # 1. intent and submission context, from the FROZEN envelope
        self.assertIn("## 1. Task Intent & Submission Context", narrative)
        self.assertIn("Test explanation generation", narrative)
        self.assertIn(env["envelope_id"], narrative)
        self.assertIn("claude-runner", narrative)

        # 2. what was AVAILABLE: identifiers AND capacity facts
        self.assertIn("## 2. Capacity Snapshot & Environmental Baseline", narrative)
        self.assertIn(str(self.catalog["catalog_version"]), narrative)
        self.assertIn(self.snapshot["snapshot_id"], narrative)
        self.assertIn("door reachable", narrative)
        self.assertIn("omen-arc", narrative)

        # 3. what was CONSIDERED, eligible and rejected
        self.assertIn("## 3. Considered & Rejected Routes", narrative)
        self.assertIn("implementation_not_live", narrative)
        self.assertIn("BUILT NOT DEPLOYED", narrative)
        self.assertIn("eligible", narrative)

        # 4. what was CHOSEN, and what was BELIEVED while choosing
        self.assertIn("## 4. Selected Graph & Decision Rationale", narrative)
        self.assertIn("direct_hearth", narrative)
        self.assertIn("Omen-arc is ready and zero-marginal-cost.", narrative)
        self.assertIn("What the orchestrator believed", narrative)
        self.assertIn("decode rate at depth", narrative)

        # 5. what was DECIDED: the validation verdict, its reasons, the approvals
        self.assertIn("## 5. Authority & Approval Trail", narrative)
        self.assertIn(result["verdict"], narrative)
        self.assertIn(result["validation_id"][:16], narrative)
        self.assertNotIn("No human approval was required", narrative)

        # 6. outcome and durability, derived from artifact records
        self.assertIn("## 6. Execution Outcome & Replay Durability", narrative)
        self.assertIn("Final Run Status:", narrative)
        self.assertIn("Durability Classification:", narrative)
        self.assertNotIn("hashes verified", narrative)

        # D-115: no chain-of-thought anywhere in the projection.
        for forbidden in ("<think>", "chain_of_thought", "thinking"):
            self.assertNotIn(forbidden, narrative)


if __name__ == "__main__":
    unittest.main()
