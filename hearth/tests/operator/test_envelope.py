"""Tests for TaskEnvelope canonicalization and neutrality (G2-C1, G2-C2)."""

import json
import unittest
from pathlib import Path

from hearth.operator import canonical, envelope
from hearth.tests.operator.support import OperatorTestCase


class TaskEnvelopeTests(OperatorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sample_inputs = {
            "repo": "/home/derek/work/commandcenter-linux-flash",
            "base_commit": "1900765883c8c7c6885d05d4fc0fa0b0e31bd1a1",
            "paths": ["hearth/operator/envelope.py"],
            "files": [],
        }
        self.sample_classification = {
            "task_type": "feature",
            "repo_size": "medium",
            "language": "python",
            "read_vs_reasoning": "balanced",
            "context_continuity": "session",
            "mutation_level": "worktree",
            "risk_level": "low",
        }
        self.sample_constraints = {
            "deadline_s": 1800,
            "max_attempts": 3,
            "max_context_tokens": 16384,
            "budget": "USD 0.50",
        }

    def test_g2_c2_envelope_neutrality_across_distinct_submitters(self) -> None:
        """G2-C2: Identical task content submitted by two different submitters
        produces the exact same envelope_id."""
        env1 = envelope.build_envelope(
            intent="Implement TaskEnvelope subsystem",
            acceptance_criteria=["Neutrality holds", "Identity stable"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
            submitted_by="claude-code-agent-1",
            submission_source="claude-cli",
            submitted_at="2026-09-17T10:00:00Z",
        )

        env2 = envelope.build_envelope(
            intent="Implement TaskEnvelope subsystem",
            acceptance_criteria=["Neutrality holds", "Identity stable"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
            submitted_by="codex-cli-agent-2",
            submission_source="codex-runner",
            submitted_at="2026-09-17T11:30:00Z",
        )

        # Different submission metadata
        self.assertNotEqual(env1["submitted_by"], env2["submitted_by"])
        self.assertNotEqual(env1["submission_source"], env2["submission_source"])
        self.assertNotEqual(env1["submitted_at"], env2["submitted_at"])

        # Exact same envelope_id!
        self.assertEqual(env1["envelope_id"], env2["envelope_id"])
        self.assertNotIn("orchestrator", env1)
        self.assertNotIn("orchestrator", env2)

    def test_g2_c1_identity_canonicalization_and_tampering(self) -> None:
        """G2-C1: Identity canonicalization rules:
        - same input -> same id
        - altered field -> different id
        - whitespace and key ordering do not change id
        - tampering detected by verify_document
        """
        env1 = envelope.build_envelope(
            intent="Build G2",
            acceptance_criteria=["Criterion 1"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
        )

        # Same input
        env2 = envelope.build_envelope(
            intent="Build G2",
            acceptance_criteria=["Criterion 1"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
        )
        self.assertEqual(env1["envelope_id"], env2["envelope_id"])

        # Altered material field changes id
        env3 = envelope.build_envelope(
            intent="Build G2 - altered",
            acceptance_criteria=["Criterion 1"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
        )
        self.assertNotEqual(env1["envelope_id"], env3["envelope_id"])

        # Verification passes on untampered doc
        ver = canonical.verify_document(env1)
        self.assertTrue(all(v["ok"] for v in ver))

        # Post-assignment tampering is detected
        tampered = dict(env1)
        tampered["intent"] = "Tampered intent"
        ver_tampered = canonical.verify_document(tampered)
        self.assertFalse(all(v["ok"] for v in ver_tampered))

    def test_reasoning_fields_are_rejected(self) -> None:
        """D-115: Thinking / reasoning / chain_of_thought fields must be rejected."""
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build_envelope(
                intent="Test task",
                acceptance_criteria=["C1"],
                inputs={"repo": "repo", "base_commit": "abc", "paths": [], "files": [], "thinking": "secret thought"},
                classification=self.sample_classification,
                constraints=self.sample_constraints,
            )

    def test_store_and_load_envelope(self) -> None:
        env = envelope.build_envelope(
            intent="Persist task",
            acceptance_criteria=["Persisted cleanly"],
            inputs=self.sample_inputs,
            classification=self.sample_classification,
            constraints=self.sample_constraints,
        )
        stored_path = envelope.store_envelope(env, "run-test-1")
        self.assertTrue(stored_path.is_file())
        loaded = envelope.load_envelope(stored_path)
        self.assertEqual(loaded["envelope_id"], env["envelope_id"])


if __name__ == "__main__":
    unittest.main()
