"""Tests for RouteProposal structuring and reasoning rejection (G2-C3)."""

import json
import unittest
from pathlib import Path

from hearth.operator import canonical, proposal
from hearth.tests.operator.support import OperatorTestCase


class RouteProposalTests(OperatorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sample_orch = {
            "provider": "anthropic",
            "model": "claude-3-opus",
            "endpoint_or_version": "2026-09",
            "client": "claude-code",
            "harness": "cli",
            "session": "session-123",
        }
        self.envelope_id = "a" * 64
        self.catalog_version = "b" * 64
        self.snapshot_id = "c" * 64
        self.snapshot_expires_at = "2026-09-17T12:00:00Z"
        self.policy_version = "d" * 64

    def test_g2_c3_reasoning_fields_rejection(self) -> None:
        """G2-C3: Any proposal containing thinking, reasoning, or chain_of_thought
        fields is rejected per D-115."""
        # 1. Direct thinking key
        with self.assertRaises(proposal.ProposalError):
            proposal.build_proposal(
                envelope_id=self.envelope_id,
                catalog_version=self.catalog_version,
                snapshot_id=self.snapshot_id,
                snapshot_expires_at=self.snapshot_expires_at,
                orchestrator=self.sample_orch,
                policy_version=self.policy_version,
                eligible_routes=[],
                rejected_routes=[],
                selected_graph={"nodes": [], "edges": [], "thinking": "hidden chain"},
                assumptions=[],
                uncertainty={"confidence": "high", "unknowns": []},
                expected={"time_s": 10, "attempts": 1, "context_tokens": 100, "resources": []},
                required_authority=[],
                required_approvals=[],
                rationale="Valid rationale",
            )

        # 2. Nested chain_of_thought key
        with self.assertRaises(proposal.ProposalError):
            proposal.build_proposal(
                envelope_id=self.envelope_id,
                catalog_version=self.catalog_version,
                snapshot_id=self.snapshot_id,
                snapshot_expires_at=self.snapshot_expires_at,
                orchestrator=self.sample_orch,
                policy_version=self.policy_version,
                eligible_routes=[],
                rejected_routes=[],
                selected_graph={"nodes": [{"id": "n1", "chain_of_thought": "steps"}], "edges": []},
                assumptions=[],
                uncertainty={"confidence": "high", "unknowns": []},
                expected={"time_s": 10, "attempts": 1, "context_tokens": 100, "resources": []},
                required_authority=[],
                required_approvals=[],
                rationale="Valid rationale",
            )

    def test_rationale_length_enforcement(self) -> None:
        long_rationale = "x" * 2001
        with self.assertRaises(proposal.ProposalError):
            proposal.build_proposal(
                envelope_id=self.envelope_id,
                catalog_version=self.catalog_version,
                snapshot_id=self.snapshot_id,
                snapshot_expires_at=self.snapshot_expires_at,
                orchestrator=self.sample_orch,
                policy_version=self.policy_version,
                eligible_routes=[],
                rejected_routes=[],
                selected_graph={"nodes": [], "edges": []},
                assumptions=[],
                uncertainty={"confidence": "high", "unknowns": []},
                expected={"time_s": 10, "attempts": 1, "context_tokens": 100, "resources": []},
                required_authority=[],
                required_approvals=[],
                rationale=long_rationale,
            )

    def test_rejected_route_codes_validation(self) -> None:
        with self.assertRaises(proposal.ProposalError):
            proposal.build_proposal(
                envelope_id=self.envelope_id,
                catalog_version=self.catalog_version,
                snapshot_id=self.snapshot_id,
                snapshot_expires_at=self.snapshot_expires_at,
                orchestrator=self.sample_orch,
                policy_version=self.policy_version,
                eligible_routes=[],
                rejected_routes=[{
                    "route_id": "r1",
                    "route_kind": "direct",
                    "target": "target",
                    "reason_code": "unknown_reason_code",
                    "reason_detail": "detail"
                }],
                selected_graph={"nodes": [], "edges": []},
                assumptions=[],
                uncertainty={"confidence": "high", "unknowns": []},
                expected={"time_s": 10, "attempts": 1, "context_tokens": 100, "resources": []},
                required_authority=[],
                required_approvals=[],
                rationale="Valid",
            )

    def test_valid_proposal_and_store(self) -> None:
        prop = proposal.build_proposal(
            envelope_id=self.envelope_id,
            catalog_version=self.catalog_version,
            snapshot_id=self.snapshot_id,
            snapshot_expires_at=self.snapshot_expires_at,
            orchestrator=self.sample_orch,
            policy_version=self.policy_version,
            eligible_routes=[{"route_id": "direct", "route_kind": "inference", "target": "omen-arc", "estimated_cost": "USD 0.00"}],
            rejected_routes=[{
                "route_id": "moe",
                "route_kind": "inference",
                "target": "am4",
                "reason_code": "capacity_not_ready",
                "reason_detail": "offline"
            }],
            selected_graph={"nodes": [{"id": "s1", "route_kind": "inference", "target": "omen-arc", "inputs": {}, "expected": {}}], "edges": []},
            assumptions=["Running locally"],
            uncertainty={"confidence": "high", "unknowns": []},
            expected={"time_s": 10, "attempts": 1, "context_tokens": 100, "resources": ["omen-arc"]},
            required_authority=["call_door_generate"],
            required_approvals=[],
            rationale="Best local route",
        )
        self.assertIn("proposal_id", prop)
        stored_path = proposal.store_proposal(prop, "run-prop-test")
        self.assertTrue(stored_path.is_file())


if __name__ == "__main__":
    unittest.main()
