"""Tests for physical execution of direct_hearth routes (WI-G3A)."""

import io
import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from hearth.operator import (
    canonical,
    catalog as catalog_mod,
    envelope as envelope_mod,
    execute,
    history,
    paths,
    proposal as proposal_mod,
    replay,
    validate,
)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import OperatorTestCase, UNRESTRICTED_KEY


class ExecuteDirectHearthTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.run_id = "run-execute-g3a-test"
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)

        # Create an isolated test fixture file inside self.home
        self.fixture_dir = self.home / "sample_inventory"
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        (self.fixture_dir / "README.md").write_text("# Test Readme", encoding="utf-8")
        (self.fixture_dir / "app.py").write_text("print('hello')", encoding="utf-8")
        (self.fixture_dir / "index.js").write_text("console.log('hi');", encoding="utf-8")

        # Set up envelope
        self.env = self.make_envelope(
            intent="Inventory repository files",
            inputs={
                "repo": str(self.home),
                "base_commit": "a" * 40,
                "paths": [
                    "sample_inventory/README.md",
                    "sample_inventory/app.py",
                    "sample_inventory/index.js",
                ],
                "files": [],
            },
            acceptance_criteria=["Output valid markdown", "List all 3 files"],
            constraints={
                "deadline_s": 120,
                "max_attempts": 1,
                "max_context_tokens": 8192,
                "budget": "USD 0.50",
            },
        )
        envelope_mod.store_envelope(self.env, self.run_id)

        # Set up proposal
        self.prop = self.make_proposal(
            self.catalog,
            self.snapshot,
            envelope_id=self.env["envelope_id"],
        )
        proposal_mod.store_proposal(self.prop, self.run_id)

        # Validate proposal
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()
        self.val = validate.validate_proposal(
            self.prop,
            self.caller,
            catalog=self.catalog,
            current_snapshot=self.snapshot,
            now=self.now,
            run_id=self.run_id,
        )
        validate.store_validation(self.val, self.run_id, requester=self.caller)

    def test_execute_happy_path_mock(self) -> None:
        """WI-G3A happy path: executes direct_hearth, produces AttemptReceipt and artifact."""
        fake_response_content = (
            "# Repository Inventory\n\n"
            "- README.md: Markdown documentation, entry point for documentation.\n"
            "- app.py: Python application script, entry point for backend.\n"
            "- index.js: JavaScript script, frontend or Node entry point.\n"
        )
        fake_json = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": fake_response_content,
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 65,
                "total_tokens": 185,
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_json).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = execute.execute_run(self.run_id)

        self.assertEqual(res["run_id"], self.run_id)
        receipt = res["receipt"]
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(receipt["route_kind"], "direct_inference")
        self.assertEqual(receipt["target"], "direct_hearth")
        self.assertEqual(receipt["usage"]["total_tokens"], 185)

        # Verify receipt is on disk
        refs_dir = paths.run_refs_dir(self.run_id)
        rcpt_file = refs_dir / f"attempt_{receipt['attempt_id']}.json"
        self.assertTrue(rcpt_file.is_file())

        # Verify verification checks passed
        verification = res["verification"]
        self.assertEqual(verification["verdict"], "passed")
        for check in verification["checks"]:
            self.assertTrue(check["passed"], f"check failed: {check}")

        # Verify replay reconstruction
        state = res["state"]
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["reconstructable"])

        # Verify history events sequence
        evs = history.read_run_history(self.run_id)
        event_types = [e["event_type"] for e in evs]
        self.assertIn("step.dispatched", event_types)
        self.assertIn("artifact.produced", event_types)
        self.assertIn("attempt.recorded", event_types)
        self.assertIn("verification.recorded", event_types)
        self.assertIn("outcome.final", event_types)

        # Verify explain output formats attempt and verification
        from hearth.operator import explain
        narrative = explain.explain_run(self.run_id)
        self.assertIn("Attempt `", narrative)
        self.assertIn("Deterministic Verification:", narrative)
        self.assertIn("check `file_exists`: passed", narrative)
        self.assertIn("check `contains_all_filenames`: passed", narrative)

    def test_execute_endpoint_unavailable(self) -> None:
        """WI-G3A: endpoint failure records failed attempt and raises ExecutionError."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(execute.ExecutionError):
                execute.execute_run(self.run_id)

        refs_dir = paths.run_refs_dir(self.run_id)
        attempts = list(refs_dir.glob("attempt_*.json"))
        self.assertEqual(len(attempts), 1)
        receipt = json.loads(attempts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failure")
        self.assertIn("Connection refused", receipt["error"])

        # History and replay
        evs = history.read_run_history(self.run_id)
        event_types = [e["event_type"] for e in evs]
        self.assertIn("attempt.recorded", event_types)
        self.assertIn("outcome.final", event_types)

        state = replay.replay_run(self.run_id)
        self.assertEqual(state["status"], "failed")

    def test_execute_unvalidated_proposal_rejected(self) -> None:
        """Executing a proposal that was rejected fails immediately."""
        unvalidated_run = "run-unvalidated-test"
        envelope_mod.store_envelope(self.env, unvalidated_run)
        proposal_mod.store_proposal(self.prop, unvalidated_run)

        # Store rejected validation with updated identity
        rejected_val = dict(self.val)
        rejected_val["verdict"] = "rejected"
        rejected_val = canonical.stamp_identity(rejected_val, "validation_id")
        validate.store_validation(rejected_val, unvalidated_run, requester=self.caller)

        with self.assertRaises(execute.ExecutionError) as ctx:
            execute.execute_run(unvalidated_run)
        self.assertIn("expected 'validated'", str(ctx.exception))


    def test_execute_deepagents_hearth_mock(self) -> None:
        """WI-G3B: executes deepagents_hearth with tool calls and generates AttemptReceipt."""
        deepagents_run_id = "run-deepagents-mock-test"
        envelope_mod.store_envelope(self.env, deepagents_run_id)

        # Build proposal selecting deepagents_hearth
        prop_deepagents = self.make_proposal(
            self.catalog,
            self.snapshot,
            envelope_id=self.env["envelope_id"],
            eligible_routes=[{"route_id": "deepagents_hearth", "route_kind": "deepagents_hearth", "target": "deepagents_hearth", "estimated_cost": "USD 0.00"}],
            selected_graph={"nodes": [{"id": "n1", "route_kind": "deepagents_hearth", "target": "deepagents_hearth", "inputs": {"envelope_id": self.env["envelope_id"]}, "expected": {"attempts": 1}}], "edges": []}
        )
        proposal_mod.store_proposal(prop_deepagents, deepagents_run_id)

        val_deepagents = validate.validate_proposal(
            prop_deepagents,
            self.caller,
            catalog=self.catalog,
            current_snapshot=self.snapshot,
            now=self.now,
            run_id=deepagents_run_id,
        )
        validate.store_validation(val_deepagents, deepagents_run_id, requester=self.caller)

        fake_markdown = (
            "# Repository Inventory\n\n"
            "- README.md: Markdown documentation, entry point for documentation.\n"
            "- app.py: Python application script, entry point for backend.\n"
            "- index.js: JavaScript script, frontend or Node entry point.\n"
        )
        def fake_run_deepagents(repo_root, input_paths, endpoint, model_name, on_tool_call=None):
            if on_tool_call:
                on_tool_call({"name": "read_file", "args": {"path": "sample_inventory/README.md"}})
                on_tool_call({"name": "read_file", "args": {"path": "sample_inventory/app.py"}})
                on_tool_call({"name": "read_file", "args": {"path": "sample_inventory/index.js"}})
            return fake_markdown, {"tool_calls": 3, "prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450}

        with patch("hearth.operator.execute._run_deepagents", side_effect=fake_run_deepagents):
            res = execute.execute_run(deepagents_run_id)

        self.assertEqual(res["receipt"]["route_kind"], "deepagents_hearth")
        self.assertEqual(res["receipt"]["target"], "deepagents_hearth")
        self.assertEqual(res["receipt"]["usage"]["tool_calls"], 3)
        self.assertEqual(res["verification"]["verdict"], "passed")
        self.assertTrue(res["state"]["reconstructable"])

        evs = history.read_run_history(deepagents_run_id)
        tool_events = [e for e in evs if e["event_type"] == "tool.called"]
        self.assertEqual(len(tool_events), 3)


    def test_execute_mechnet_build_mock(self) -> None:
        """WI-G3C: executes mechnet_build route with lifecycle tools and generates AttemptReceipt."""
        mechnet_run_id = "run-mechnet-mock-test"
        envelope_mod.store_envelope(self.env, mechnet_run_id)

        # Build proposal selecting mechnet_build
        prop_mechnet = self.make_proposal(
            self.catalog,
            self.snapshot,
            envelope_id=self.env["envelope_id"],
            eligible_routes=[{"route_id": "mechnet_build", "route_kind": "build", "target": "mechnet_build", "estimated_cost": "USD 0.00"}],
            selected_graph={"nodes": [{"id": "n1", "route_kind": "build", "target": "mechnet_build", "inputs": {"envelope_id": self.env["envelope_id"]}, "expected": {"attempts": 1}}], "edges": []}
        )
        proposal_mod.store_proposal(prop_mechnet, mechnet_run_id)

        val_mechnet = validate.validate_proposal(
            prop_mechnet,
            self.caller,
            catalog=self.catalog,
            current_snapshot=self.snapshot,
            now=self.now,
            run_id=mechnet_run_id,
        )
        validate.store_validation(val_mechnet, mechnet_run_id, requester=self.caller)

        fake_markdown = (
            "# Repository Inventory\n\n"
            "- README.md: Markdown documentation, entry point for documentation.\n"
            "- app.py: Python application script, entry point for backend.\n"
            "- index.js: JavaScript script, frontend or Node entry point.\n"
        )
        def fake_run_mechnet(repo_root, input_paths, endpoint, model_name, envelope, run_id, timeout_s=60.0, on_tool_call=None):
            if on_tool_call:
                on_tool_call({"name": "create_build_request", "args": {"title": "Test Build"}})
                on_tool_call({"name": "execute_build_request", "args": {"receipt_id": "br-test"}})
                on_tool_call({"name": "update_build_request", "args": {"receipt_id": "br-test"}})
                on_tool_call({"name": "close_build_request", "args": {"receipt_id": "br-test"}})
            return fake_markdown, {"tool_calls": 4, "prompt_tokens": 250, "completion_tokens": 120, "total_tokens": 370}, "br-test-123"

        with patch("hearth.operator.execute._run_mechnet_build", side_effect=fake_run_mechnet):
            res = execute.execute_run(mechnet_run_id)

        self.assertEqual(res["receipt"]["route_kind"], "build")
        self.assertEqual(res["receipt"]["target"], "mechnet_build")
        self.assertEqual(res["receipt"]["usage"]["tool_calls"], 4)
        self.assertEqual(res["receipt"]["mechnet_receipt_id"], "br-test-123")
        self.assertEqual(res["verification"]["verdict"], "passed")
        self.assertTrue(res["state"]["reconstructable"])

        evs = history.read_run_history(mechnet_run_id)
        tool_events = [e for e in evs if e["event_type"] == "tool.called"]
        self.assertEqual(len(tool_events), 4)


if __name__ == "__main__":
    unittest.main()
