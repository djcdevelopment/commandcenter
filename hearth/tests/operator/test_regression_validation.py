"""Regression tests for the capacity-blind validation exploits reproduced by the
WI-G2 independent verification (report section 5, appendix B section 5).

`current_snapshot` was a dead parameter: a route onto an unreachable door and a
rung recorded `ready: null` validated clean, required fields past `fresh_until`
validated as fresh, budgets were never compared, an unknown contract version
validated, and a malformed proposal raised KeyError out of the validator.

Each test is named for the exploit. All were red against `a2d7a91`.
"""

from __future__ import annotations

import copy
import unittest
from datetime import timedelta

from hearth.operator import (canonical, catalog as catalog_mod, core, envelope as envelope_mod,
                             history, inspection, paths, proposal as proposal_mod, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (FakeDoor, OperatorTestCase, UNRESTRICTED_KEY,
                                           fake_cli_runner)


def reason_codes(result: dict) -> list[str]:
    return [check.get("reason_code") for check in result["checks"]]


class ValidationRegressionBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-validation-regression"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()
        self.proposal = self.make_proposal(self.catalog, self.snapshot,
                                           envelope_id=self.envelope["envelope_id"])

    def validate(self, proposal=None, *, snapshot=None, now=None, **kwargs):
        return validate.validate_proposal(
            proposal if proposal is not None else self.proposal,
            self.caller,
            catalog=self.catalog,
            current_snapshot=snapshot if snapshot is not None else self.snapshot,
            now=now or self.now,
            run_id=self.run_id,
            **kwargs)


class CapacityBlindnessTests(ValidationRegressionBase):
    def test_a_route_onto_an_unreachable_door_is_rejected(self) -> None:
        down = self.capture_snapshot(
            self.catalog, now=self.now,
            door=FakeDoor(all_down="connection refused (door offline)"))
        proposal = self.make_proposal(self.catalog, down,
                                      envelope_id=self.envelope["envelope_id"])
        result = self.validate(proposal, snapshot=down)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("capacity_not_ready", reason_codes(result))
        failed = [c for c in result["checks"] if c["reason_code"] == "capacity_not_ready"]
        self.assertTrue(any("door" in c["reason"] for c in failed))
        self.assertTrue(all(c.get("remedy") for c in failed), "every rejection states a remedy")

    def test_a_route_onto_a_not_ready_rung_is_rejected(self) -> None:
        serve_truth = {"omen-arc": {"observed_at": "2026-09-17T12:00:00Z", "ready": False,
                                    "loaded_models": [],
                                    "reason": "llama-server is not listening on 8082"}}
        door = FakeDoor()
        door.responses = dict(door.responses, capture_resource_snapshot=serve_truth)
        snapshot = self.capture_snapshot(self.catalog, now=self.now, door=door)
        proposal = self.make_proposal(self.catalog, snapshot,
                                      envelope_id=self.envelope["envelope_id"])
        result = self.validate(proposal, snapshot=snapshot)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("capacity_not_ready", reason_codes(result))
        self.assertTrue(any("omen-arc" in c["reason"] for c in result["checks"]
                            if c["reason_code"] == "capacity_not_ready"))

    def test_current_snapshot_is_read_not_merely_accepted(self) -> None:
        """The validator must consult the snapshot it is handed: the same
        proposal validates against a healthy snapshot and is refused against a
        snapshot in which the rung it needs is down."""
        self.assertEqual(self.validate()["verdict"], "validated")
        door = FakeDoor()
        door.responses = dict(door.responses, capture_resource_snapshot={
            "omen-arc": {"observed_at": "2026-09-17T12:00:00Z", "ready": None,
                         "reason": "connection refused (door offline)"}})
        blind = self.capture_snapshot(self.catalog, now=self.now, door=door)
        result = self.validate(snapshot=blind)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("capacity_not_ready", reason_codes(result))

    def wall_clock_run(self):
        """A run whose snapshot was observed on the wall clock.

        The rung readiness fields are stamped with the observing process's own
        clock rather than an injected one (a G1 surface the WI-G2 verification
        recorded), so the two-horizon case — planning window open at 300 s,
        readiness stale at 120 s — is constructed against the clock the snapshot
        actually used.
        """
        base = canonical.utc_now()
        snapshot = self.capture_snapshot(self.catalog, now=base)
        run_id = "run-two-horizon"
        # Submitted when the snapshot was observed: since WI-G2b the deadline is
        # compared against ELAPSED time, so an envelope frozen at a fixed
        # fixture hour would be hours over its own deadline on the wall clock.
        envelope_mod.store_envelope(
            self.make_envelope(submitted_at=canonical.rfc3339(base)), run_id)
        envelope = envelope_mod.load_envelope(paths.run_refs_dir(run_id) / "envelope.json")
        proposal = self.make_proposal(self.catalog, snapshot,
                                      envelope_id=envelope["envelope_id"])
        return run_id, snapshot, proposal, base + timedelta(seconds=200)

    def test_capacity_that_moved_under_the_proposal_is_reported(self) -> None:
        """A material change between the planning snapshot the orchestrator chose
        against and the snapshot validation read is a rejection of its own."""
        inspection.write_snapshot(self.snapshot)
        door = FakeDoor()
        door.responses = dict(door.responses, capture_resource_snapshot={
            "omen-arc": {"observed_at": "2026-09-17T12:00:00Z", "ready": True,
                         "loaded_models": ["qwen3-30b-a3b"], "parallel_slots": 8,
                         "reason": "ready"}})
        moved = self.capture_snapshot(self.catalog, now=self.now, door=door)
        self.assertNotEqual(moved["snapshot_id"], self.snapshot["snapshot_id"])
        result = self.validate(snapshot=moved)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("capacity_changed", reason_codes(result))

    def test_a_required_field_past_fresh_until_is_not_treated_as_fresh(self) -> None:
        """D-106: readiness has a 120 s TTL inside a 300 s planning window."""
        run_id, snapshot, proposal, later = self.wall_clock_run()
        result = validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=snapshot, now=later,
                                            run_id=run_id)
        self.assertNotIn("snapshot_expired", reason_codes(result),
                         "the planning window must still be open for this case")
        self.assertNotEqual(result["verdict"], "validated")
        self.assertIn("capacity_field_stale", reason_codes(result))
        stale = [c for c in result["checks"] if c["reason_code"] == "capacity_field_stale"]
        self.assertTrue(any("rungs.omen-arc.ready" in c["reason"] for c in stale))
        self.assertTrue(all(c.get("remedy") for c in stale))

    def test_refreshing_a_stale_field_binds_a_validation_snapshot(self) -> None:
        """The other half of D-106: re-observe, bind, and never rewrite the
        proposal's planning snapshot."""
        run_id, snapshot, proposal, later = self.wall_clock_run()
        result = validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=snapshot, now=later,
                                            run_id=run_id, refresh_door=FakeDoor(),
                                            refresh_cli_runner=fake_cli_runner())
        self.assertEqual(result["verdict"], "validated", result["reasons"])
        self.assertTrue(result["validation_snapshot_id"])
        self.assertNotEqual(result["validation_snapshot_id"], result["snapshot_id"])
        self.assertEqual(result["snapshot_id"], proposal["snapshot_id"])
        self.assertEqual(snapshot["snapshot_id"], proposal["snapshot_id"],
                         "the planning snapshot is never rewritten")


class BudgetTests(ValidationRegressionBase):
    def test_context_over_the_envelope_budget_is_rejected(self) -> None:
        proposal = self.make_proposal(
            self.catalog, self.snapshot, envelope_id=self.envelope["envelope_id"],
            expected={"time_s": 120, "attempts": 1, "context_tokens": 100_000_000,
                      "resources": ["omen-arc"]})
        result = self.validate(proposal)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("over_budget_context", reason_codes(result))

    def test_attempts_over_the_envelope_budget_is_rejected(self) -> None:
        proposal = self.make_proposal(
            self.catalog, self.snapshot, envelope_id=self.envelope["envelope_id"],
            expected={"time_s": 120, "attempts": 9999, "context_tokens": 8192,
                      "resources": ["omen-arc"]})
        result = self.validate(proposal)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("over_budget_attempts", reason_codes(result))

    def test_context_over_the_rungs_declared_budget_is_rejected(self) -> None:
        envelope = self.make_envelope(constraints={"deadline_s": 600, "max_attempts": 3,
                                                   "max_context_tokens": 1_000_000,
                                                   "budget": None},
                                      intent="A very large read against a small rung.")
        run_id = "run-rung-budget"
        envelope_mod.store_envelope(envelope, run_id)
        proposal = self.make_proposal(
            self.catalog, self.snapshot, envelope_id=envelope["envelope_id"],
            expected={"time_s": 120, "attempts": 1, "context_tokens": 900_000,
                      "resources": ["omen-arc"]})
        result = validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=run_id)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("over_budget_context", reason_codes(result))
        self.assertTrue(any("omen-arc" in c["reason"] for c in result["checks"]
                            if c["reason_code"] == "over_budget_context"))


class ContractGateTests(ValidationRegressionBase):
    def test_an_unknown_proposal_contract_version_is_refused(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["contract_version"] = "route-proposal.v9"
        proposal["proposal_id"] = canonical.identity_of(proposal, "proposal_id")
        result = self.validate(proposal)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("unknown_contract_version", reason_codes(result))

    def test_a_malformed_proposal_returns_a_rejected_verdict_not_a_keyerror(self) -> None:
        for payload in ({}, {"proposal_id": "x" * 64},
                        {k: v for k, v in self.proposal.items() if k != "catalog_version"}):
            with self.subTest(payload=sorted(payload)[:3]):
                result = self.validate(payload)
                self.assertEqual(result["verdict"], "rejected")
                self.assertTrue(set(reason_codes(result))
                                & {"missing_required_field", "contract_invalid",
                                   "unknown_contract_version"})

    def test_validation_deep_copies_the_proposal_it_checked(self) -> None:
        result = self.validate()
        result["proposal_copy"]["selected_graph"]["nodes"][0]["target"] = "hijacked"
        self.assertEqual(self.proposal["selected_graph"]["nodes"][0]["target"],
                         "direct_hearth")


class TestModePolicyTests(ValidationRegressionBase):
    """D-113 as amended: companions resolved and verified, relaxation only of
    catalog lifecycle eligibility for the exact `test_targets`."""

    def mode_fields(self, **overrides) -> dict:
        fields = {
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
        fields.update(overrides)
        return fields

    def mode_proposal(self, **overrides):
        graph = {"nodes": [{"id": "n1", "route_kind": "planning",
                            "target": "propose_schedule",
                            "inputs": {"envelope_id": self.envelope["envelope_id"]},
                            "expected": {"attempts": 1}}],
                 "edges": []}
        fields = {"mode": "test", "selected_graph": graph,
                  "test_mode_fields": self.mode_fields(),
                  "envelope_id": self.envelope["envelope_id"]}
        fields.update(overrides)
        return self.make_proposal(self.catalog, self.snapshot, **fields)

    def test_a_complete_test_mode_proposal_still_requires_human_approval(self) -> None:
        result = self.validate(self.mode_proposal())
        self.assertEqual(result["verdict"], "needs_approval")
        self.assertTrue(result["test_mode"])

    def test_presence_only_companions_are_not_enough(self) -> None:
        master = str(paths.operator_config()["catalog"]["worktree_policy"]
                     ["control_plane_repository"])
        cases = {
            "unreleased_work_item": {"work_item": "WI-NOT-RELEASED"},
            "inputs_inside_master": {"isolated_inputs": [master + r"\hearth"]},
            "outputs_inside_master": {"isolated_outputs": ["knowledge/"]},
            "unresolvable_rollback": {"rollback_ref": "not-a-commit"},
            "missing_recovery": {"recovery_instructions": ""},
            "target_outside_test_targets": {"test_targets": ["direct_hearth"]},
            "authority_outside_bounds": {"bounded_authorities": ["read_repo"]},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                proposal = self.mode_proposal(
                    test_mode_fields=self.mode_fields(**override))
                result = self.validate(proposal)
                self.assertEqual(result["verdict"], "rejected")

    def test_test_mode_relaxes_only_catalog_lifecycle_for_named_targets(self) -> None:
        """Never authority, human approval, capacity, freshness, budgets,
        reachability, or contract validation."""
        never_relaxed = {
            "capacity": dict(expected={"time_s": 1, "attempts": 1, "context_tokens": 8192,
                                       "resources": ["am4-ollama"]}),
            "budget": dict(expected={"time_s": 1, "attempts": 9999,
                                     "context_tokens": 8192, "resources": ["omen-arc"]}),
            "catalog_mismatch": dict(catalog_version="0" * 64),
        }
        for name, override in never_relaxed.items():
            with self.subTest(case=name):
                result = self.validate(self.mode_proposal(**override))
                self.assertEqual(result["verdict"], "rejected")

    def test_test_mode_events_are_flagged(self) -> None:
        result = self.validate(self.mode_proposal())
        self.store_validation_as(result, self.run_id, self.caller)
        events = history.read_run_history(self.run_id)
        flagged = [ev for ev in events
                   if ev["event_type"] in ("route.validated", "route.rejected",
                                           "approval.requested")]
        self.assertTrue(flagged)
        self.assertTrue(all(ev.get("test_mode") is True for ev in flagged))


class InvalidationTests(ValidationRegressionBase):
    def test_material_capacity_change_appends_decision_invalidated(self) -> None:
        """G2-C9 literally: `core.refresh` invalidates the open validated
        decisions that cited the superseded snapshot."""
        result = self.validate()
        self.assertEqual(result["verdict"], "validated")
        self.store_validation_as(result, self.run_id, self.caller)
        inspection.write_snapshot(self.snapshot)
        inspection.write_current(self.catalog, self.snapshot)

        door = FakeDoor()
        door.responses = dict(door.responses, capture_resource_snapshot={
            "omen-arc": {"observed_at": "2026-09-17T12:05:00Z", "ready": False,
                         "loaded_models": [], "reason": "arc-maintenance.stop"}})
        core.refresh(door=door, cli_runner=fake_cli_runner(), catalog=self.catalog,
                     now=self.now + timedelta(seconds=300))

        events = [ev for ev in history.read_run_history(self.run_id)
                  if ev["event_type"] == "decision.invalidated"]
        self.assertTrue(events, "a superseded snapshot must invalidate the decision")
        self.assertEqual(events[-1]["payload"]["proposal_id"], self.proposal["proposal_id"])

    def test_decision_superseded_has_an_emitter(self) -> None:
        first = self.validate()
        self.store_validation_as(first, self.run_id, self.caller)
        successor = self.make_proposal(self.catalog, self.snapshot,
                                       envelope_id=self.envelope["envelope_id"],
                                       rationale="A second proposal supersedes the first.")
        validate.supersede_decision(self.run_id, superseded_proposal_id=self.proposal["proposal_id"],
                                    successor_proposal_id=successor["proposal_id"],
                                    reason="a superseding proposal was accepted")
        events = [ev for ev in history.read_run_history(self.run_id)
                  if ev["event_type"] == "decision.superseded"]
        self.assertTrue(events)


if __name__ == "__main__":
    unittest.main()
