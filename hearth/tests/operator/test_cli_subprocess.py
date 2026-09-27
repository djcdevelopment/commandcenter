"""Every G2 CLI command, exercised as a real subprocess.

The WI-G2 candidate shipped two commands that crashed on the first line of their
handler (`operator approve` and `operator route draft`) while its suite stayed
green, because no test ever invoked a G2 CLI command. These tests assert the
exit code, the stdout and stderr contract, and the durable state each command
leaves behind — from outside the process, the way a cold agent scripts it.

The approver credential is entered on stdin (hidden input; never argv, never an
environment variable) and is an ephemeral fixture string in a temporary
registry. The real `derek-approver` credential is never minted or looked for.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from hearth.operator import canonical, catalog as catalog_mod, core, history, paths
from hearth.tests.operator.support import (FakeDoor, HUMAN_APPROVER_KEY, OperatorTestCase,
                                           ORCHESTRATOR_KEY, SAMPLE_ORCHESTRATOR,
                                           UNRESTRICTED_KEY, fake_cli_runner)

RUN_ID = "run-cli-subprocess"


class CliSubprocessTests(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        # A captured planning snapshot in the temp home, taken from the fake
        # door: no test ever calls the live gateway.
        self.snapshot = core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                                     catalog=self.catalog)["snapshot"]

    # --- harness ----------------------------------------------------------
    def cli(self, *argv: str, stdin: str = "", key: str | None = None,
            env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = self.child_env(**(env_extra or {}))
        if key is not None:
            env[paths.API_KEY_ENV] = key
        return subprocess.run(
            [sys.executable, "-m", "hearth.operator", *argv],
            input=stdin, capture_output=True, text=True, encoding="utf-8",
            env=env, cwd=str(paths.REPO_ROOT), timeout=180)

    def envelope_file(self, **overrides) -> Path:
        document = {
            "intent": "Summarize the AM4 capacity note and cite the catalog gaps.",
            "acceptance_criteria": ["cites the catalog", "names the gaps"],
            "inputs": {"repo": "commandcenter", "base_commit": "a" * 40,
                       "paths": ["knowledge/"], "files": []},
            "classification": {},
            "constraints": {"deadline_s": 600, "max_attempts": 3,
                            "max_context_tokens": 32768, "budget": None},
        }
        document.update(overrides)
        target = self.home / "task.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        return target

    def submit(self, run_id: str = RUN_ID) -> dict:
        result = self.cli("task", "submit", str(self.envelope_file()),
                          "--run-id", run_id, "--json", key=ORCHESTRATOR_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def draft(self, run_id: str = RUN_ID) -> dict:
        result = self.cli("route", "draft", run_id, "--json", key=ORCHESTRATOR_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # --- G1 commands still work -------------------------------------------
    def test_catalog_check_exits_zero(self) -> None:
        result = self.cli("catalog", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current", result.stdout)

    def test_whoami_and_inspect(self) -> None:
        who = self.cli("whoami", "--json", key=UNRESTRICTED_KEY)
        self.assertEqual(who.returncode, 0, who.stderr)
        self.assertEqual(json.loads(who.stdout)["caller"]["id"], "fixture-unrestricted")
        self.assertNotIn(UNRESTRICTED_KEY, who.stdout + who.stderr)

        inspect = self.cli("inspect", "--json", key=UNRESTRICTED_KEY)
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        self.assertEqual(json.loads(inspect.stdout)["capacity_snapshot"]["snapshot_id"],
                         self.snapshot["snapshot_id"])

    # --- task submit -------------------------------------------------------
    def test_task_submit_writes_the_envelope_and_the_event(self) -> None:
        emitted = self.submit()
        self.assertEqual(emitted["run_id"], RUN_ID)
        stored = json.loads((paths.run_refs_dir(RUN_ID) / "envelope.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(stored["envelope_id"], emitted["envelope_id"])
        events = history.read_run_history(RUN_ID)
        self.assertEqual([ev["event_type"] for ev in events], ["task.received"])
        self.assertNotIn("intent", events[0]["payload"])

    def test_task_submit_refuses_a_missing_file_with_exit_1(self) -> None:
        result = self.cli("task", "submit", str(self.home / "nope.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    # --- route draft / propose --------------------------------------------
    def test_route_draft_runs_and_stores_a_proposal(self) -> None:
        self.submit()
        proposal = self.draft()
        self.assertTrue(proposal["proposal_id"])
        self.assertEqual(proposal["contract_version"], "route-proposal.v1")
        target = (paths.run_refs_dir(RUN_ID)
                  / f"proposal_{proposal['proposal_id']}.json")
        self.assertTrue(target.is_file())
        self.assertIn("route.proposed",
                      [ev["event_type"] for ev in history.read_run_history(RUN_ID)])

    def test_route_propose_refuses_a_reasoning_bearing_document(self) -> None:
        self.submit()
        proposal = self.draft()
        proposal["uncertainty"] = {"confidence": "high", "unknowns": [],
                                   "thinking": "hidden chain"}
        target = self.home / "bad-proposal.json"
        target.write_text(json.dumps(proposal), encoding="utf-8")
        result = self.cli("route", "propose", RUN_ID, str(target), key=ORCHESTRATOR_KEY)
        self.assertEqual(result.returncode, 1)
        self.assertIn("D-115", result.stderr)

    def test_route_propose_via_door_fails_explicitly_when_the_default_rung_is_unavailable(self) -> None:
        """D-107: `gcp-gemini` is the configurable default; an unavailable
        default fails explicitly, with no silent fallback."""
        self.submit()
        proposal = self.draft()
        target = self.home / "proposal.json"
        target.write_text(json.dumps(proposal), encoding="utf-8")
        result = self.cli("route", "propose", RUN_ID, str(target), "--via-door",
                          key=ORCHESTRATOR_KEY)
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcp-gemini", result.stderr)
        self.assertIn("no silent fallback", result.stderr.lower())

    def test_route_propose_via_door_records_the_resolved_rung_identity(self) -> None:
        self.submit()
        proposal = self.draft()
        target = self.home / "proposal.json"
        target.write_text(json.dumps(proposal), encoding="utf-8")
        result = self.cli("route", "propose", RUN_ID, str(target), "--via-door",
                          "--rung", "omen-arc", "--json", key=ORCHESTRATOR_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [ev for ev in history.read_run_history(RUN_ID)
                  if ev["event_type"] == "route.proposed"]
        door = events[-1]["payload"]["via_door"]
        self.assertEqual(door["rung"], "omen-arc")
        self.assertTrue(door["backend"])
        self.assertTrue(door["model"])

    # --- route validate ----------------------------------------------------
    def test_route_validate_exit_codes_distinguish_the_three_verdicts(self) -> None:
        self.submit()
        proposal = self.draft()
        validated = self.cli("route", "validate", RUN_ID, proposal["proposal_id"],
                             "--json", key=UNRESTRICTED_KEY)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["verdict"], "validated")

        gated = self.gated_proposal(proposal)
        needs = self.cli("route", "validate", RUN_ID, gated, "--json",
                         key=UNRESTRICTED_KEY)
        self.assertEqual(needs.returncode, 3, needs.stderr)
        self.assertEqual(json.loads(needs.stdout)["verdict"], "needs_approval")

        rejected = self.rejected_proposal(proposal)
        refused = self.cli("route", "validate", RUN_ID, rejected, "--json",
                           key=UNRESTRICTED_KEY)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stdout)["verdict"], "rejected")

    def test_route_validate_refuses_an_unknown_proposal_with_exit_1(self) -> None:
        self.submit()
        result = self.cli("route", "validate", RUN_ID, "f" * 64, key=UNRESTRICTED_KEY)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no proposal", result.stderr)

    # --- approve / revoke --------------------------------------------------
    def test_approve_records_the_decision_from_a_hidden_prompt(self) -> None:
        approval_id = self.pending_approval()
        result = self.cli("approve", RUN_ID, approval_id, "--decision", "approve",
                          stdin=HUMAN_APPROVER_KEY + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(HUMAN_APPROVER_KEY, result.stdout + result.stderr)
        decided = history.last_of("human.decided", paths.run_history_path(RUN_ID))
        self.assertIsNotNone(decided)
        self.assertEqual(decided["payload"]["decision"], "approve")
        self.assertTrue(decided["payload"]["receipt_id"])

    def test_approve_refuses_a_key_on_argv(self) -> None:
        approval_id = self.pending_approval()
        result = self.cli("approve", RUN_ID, approval_id, "--key", HUMAN_APPROVER_KEY)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--key", result.stderr)
        self.assertIsNone(history.last_of("human.decided", paths.run_history_path(RUN_ID)))

    def test_approve_ignores_a_key_in_the_environment(self) -> None:
        approval_id = self.pending_approval()
        result = self.cli("approve", RUN_ID, approval_id, stdin="",
                          env_extra={"DEREK_APPROVER_KEY": HUMAN_APPROVER_KEY})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(HUMAN_APPROVER_KEY, result.stdout + result.stderr)
        self.assertIsNone(history.last_of("human.decided", paths.run_history_path(RUN_ID)))

    def test_approve_refuses_an_agent_credential(self) -> None:
        approval_id = self.pending_approval()
        result = self.cli("approve", RUN_ID, approval_id,
                          stdin=UNRESTRICTED_KEY + "\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("approve", result.stderr)
        self.assertNotIn(UNRESTRICTED_KEY, result.stdout + result.stderr)
        self.assertIsNone(history.last_of("human.decided", paths.run_history_path(RUN_ID)))

    def test_revoke_appends_approval_revoked(self) -> None:
        approval_id = self.pending_approval()
        approved = self.cli("approve", RUN_ID, approval_id,
                            stdin=HUMAN_APPROVER_KEY + "\n")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        revoked = self.cli("revoke", RUN_ID, approval_id, "--reason",
                           "capacity shifted under the route",
                           stdin=HUMAN_APPROVER_KEY + "\n")
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertIn("approval.revoked",
                      [ev["event_type"] for ev in history.read_run_history(RUN_ID)])

    # --- history / replay / explain ---------------------------------------
    def test_history_replay_and_explain(self) -> None:
        self.submit()
        proposal = self.draft()
        self.cli("route", "validate", RUN_ID, proposal["proposal_id"],
                 key=UNRESTRICTED_KEY)

        listed = self.cli("history", RUN_ID, "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertTrue(json.loads(listed.stdout)["events"])

        verified = self.cli("history", RUN_ID, "--verify")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("verified", verified.stdout)

        replayed = self.cli("replay", RUN_ID, "--json")
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        state = json.loads(replayed.stdout)
        self.assertEqual(state["run_id"], RUN_ID)

        again = self.cli("replay", RUN_ID, "--check", "--json")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout), state)

        explained = self.cli("explain", RUN_ID)
        self.assertEqual(explained.returncode, 0, explained.stderr)
        self.assertIn("# Run Explanation", explained.stdout)
        self.assertIn("validated", explained.stdout)

    def test_history_verify_exits_1_on_a_damaged_file(self) -> None:
        self.submit()
        target = paths.run_history_path(RUN_ID)
        rows = target.read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[0])
        row["payload"]["envelope_id"] = "9" * 64
        rows[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        target.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="")
        result = self.cli("history", RUN_ID, "--verify")
        self.assertEqual(result.returncode, 1)
        self.assertIn("event_id", result.stderr)

    # --- helpers -----------------------------------------------------------
    def gated_proposal(self, drafted: dict) -> str:
        """Store a proposal requiring a human-gated authority and return its id."""
        from hearth.operator import proposal as proposal_mod

        document = dict(drafted)
        document["required_authority"] = ["call_door_generate", "merge_push_deploy"]
        document.pop("proposal_id", None)
        stamped = canonical.stamp_identity(document, "proposal_id")
        proposal_mod.store_proposal(stamped, RUN_ID)
        return stamped["proposal_id"]

    def rejected_proposal(self, drafted: dict) -> str:
        from hearth.operator import proposal as proposal_mod

        document = dict(drafted)
        document["catalog_version"] = "0" * 64
        document.pop("proposal_id", None)
        stamped = canonical.stamp_identity(document, "proposal_id")
        proposal_mod.store_proposal(stamped, RUN_ID)
        return stamped["proposal_id"]

    def pending_approval(self) -> str:
        self.submit()
        drafted = self.draft()
        proposal_id = self.gated_proposal(drafted)
        result = self.cli("route", "validate", RUN_ID, proposal_id, "--json",
                          key=UNRESTRICTED_KEY)
        self.assertEqual(result.returncode, 3, result.stderr)
        requested = [ev for ev in history.read_run_history(RUN_ID)
                     if ev["event_type"] == "approval.requested"]
        self.assertTrue(requested)
        return requested[-1]["payload"]["approval_id"]


if __name__ == "__main__":
    unittest.main()
