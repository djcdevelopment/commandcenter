"""Condition 4 (WI-G2b): the budgets that were declared but never compared.

Verifier B: "Declared reason codes with no emitter: `policy_block`,
`lane_absent`, `cost_over_budget`, `approval_unverified`. Consequence:
`constraints.budget` (money) and `deadline_s` are never compared." A code with
no emitter is a promise the record cannot keep, and an uncompared budget is not
a budget.

Derek's Gate 2 decision: enforce expected monetary cost against the envelope
monetary budget; elapsed plus expected duration against the deadline; an unknown
cost with a finite budget as a refusal or an explicit approval-required state,
never as zero; policy denial with `policy_block`; a missing required lane or
target with `lane_absent`; canonical units, deterministic comparisons, and
boundary tests below, equal to, and above each limit.

Canonical units under test:
  * money — the wire form is the string "USD <amount>", amount with at most six
    decimal places; comparison is integer micro-USD (1 USD = 1,000,000). `null`
    means "not declared", which is never read as zero.
  * time — integer seconds, compared as elapsed (now minus the envelope's
    submitted_at, floored at zero) plus the proposal's expected.time_s.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from hearth.operator import (approve, canonical, catalog as catalog_mod,
                             envelope as envelope_mod, paths, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import OperatorTestCase, HUMAN_APPROVER_KEY, UNRESTRICTED_KEY


def reason_codes(result: dict) -> list[str]:
    return [row.get("reason_code") for row in result["checks"]]


def reasons(result: dict) -> str:
    return " | ".join(row["reason"] for row in result["checks"] if row["result"] != "passed")


class BudgetBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-budget-deadline"
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    def envelope_for(self, **constraints) -> dict:
        fields = {"deadline_s": 600, "max_attempts": 3, "max_context_tokens": 32768,
                  "budget": None}
        fields.update(constraints)
        submitted_at = fields.pop("submitted_at", "2026-09-17T12:00:00Z")
        document = self.make_envelope(constraints=fields, submitted_at=submitted_at)
        envelope_mod.store_envelope(document, self.run_id)
        return document

    def proposal_for(self, envelope: dict, *, cost="USD 0.00", time_s=120, **overrides):
        routes = [{"route_id": "direct_hearth", "route_kind": "direct_inference",
                   "target": "direct_hearth", "estimated_cost": cost}]
        fields = {"envelope_id": envelope["envelope_id"],
                  "eligible_routes": routes,
                  "expected": {"time_s": time_s, "attempts": 1, "context_tokens": 8192,
                               "resources": ["omen-arc"]}}
        fields.update(overrides)
        return self.make_proposal(self.catalog, self.snapshot, **fields)

    def validate(self, proposal):
        return validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                          current_snapshot=self.snapshot, now=self.now,
                                          run_id=self.run_id)


class MonetaryBudgetTests(BudgetBase):
    def test_expected_cost_over_the_envelope_budget_is_rejected(self) -> None:
        envelope = self.envelope_for(budget="USD 1.00")
        result = self.validate(self.proposal_for(envelope, cost="USD 2.00"))
        self.assertEqual(result["verdict"], "rejected",
                         "a route costing twice the envelope's budget validated")
        self.assertIn("cost_over_budget", reason_codes(result))
        self.assertIn("USD", reasons(result))

    def test_expected_cost_equal_to_the_budget_is_within_it(self) -> None:
        envelope = self.envelope_for(budget="USD 1.00")
        result = self.validate(self.proposal_for(envelope, cost="USD 1.00"))
        self.assertEqual(result["verdict"], "validated", reasons(result))
        self.assertNotIn("cost_over_budget", reason_codes(result))

    def test_expected_cost_one_microdollar_below_and_above_the_budget(self) -> None:
        envelope = self.envelope_for(budget="USD 1.000000")
        below = self.validate(self.proposal_for(envelope, cost="USD 0.999999"))
        self.assertEqual(below["verdict"], "validated", reasons(below))
        above = self.validate(self.proposal_for(envelope, cost="USD 1.000001"))
        self.assertEqual(above["verdict"], "rejected", reasons(above))
        self.assertIn("cost_over_budget", reason_codes(above))

    def test_an_unknown_cost_with_a_finite_budget_is_refused_never_zero(self) -> None:
        envelope = self.envelope_for(budget="USD 1.00")
        result = self.validate(self.proposal_for(envelope, cost=None))
        self.assertNotEqual(result["verdict"], "validated",
                            "an undeclared cost was treated as a zero cost")
        self.assertIn("cost_unknown", reason_codes(result))
        self.assertIn("not declared", reasons(result))

    def test_no_declared_budget_means_no_comparison(self) -> None:
        envelope = self.envelope_for(budget=None)
        result = self.validate(self.proposal_for(envelope, cost=None))
        self.assertEqual(result["verdict"], "validated", reasons(result))
        rows = [row for row in result["checks"] if row["check_id"] == "budget_cost"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "passed")
        self.assertIn("no monetary budget", rows[0]["reason"])

    def test_the_monetary_wire_form_is_enforced_by_the_envelope_contract(self) -> None:
        for bad in ("0.50", "half a dollar", "USD", "EUR 1.00", "USD 1.0000001", "USD -1.00"):
            with self.subTest(budget=bad):
                with self.assertRaises(Exception):
                    self.make_envelope(constraints={"deadline_s": 600, "max_attempts": 3,
                                                    "max_context_tokens": 32768,
                                                    "budget": bad})
        good = self.make_envelope(constraints={"deadline_s": 600, "max_attempts": 3,
                                               "max_context_tokens": 32768,
                                               "budget": "USD 12.345678"})
        self.assertEqual(good["constraints"]["budget"], "USD 12.345678")

    def test_an_uncomparable_budget_is_refused_rather_than_ignored(self) -> None:
        """Belt and braces: the contract keeps this out, so the branch is only
        reachable by handing the validator an envelope directly."""
        envelope = json.loads(json.dumps(self.envelope_for(budget="USD 1.00")))
        envelope["constraints"]["budget"] = "one dollar"
        proposal = self.proposal_for(envelope, cost="USD 0.10")
        result = validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id, envelope=envelope)
        self.assertEqual(result["verdict"], "rejected", reasons(result))
        self.assertIn("one dollar", reasons(result))


class DeadlineTests(BudgetBase):
    def test_elapsed_plus_expected_duration_over_the_deadline_is_rejected(self) -> None:
        envelope = self.envelope_for(deadline_s=600, submitted_at="2026-09-17T11:50:00Z")
        result = self.validate(self.proposal_for(envelope, time_s=120))
        self.assertEqual(result["verdict"], "rejected",
                         "600s elapsed plus 120s expected fits inside a 600s deadline only "
                         "if elapsed time is never counted")
        self.assertIn("deadline_exceeded", reason_codes(result))

    def test_elapsed_plus_expected_duration_equal_to_the_deadline_is_allowed(self) -> None:
        envelope = self.envelope_for(deadline_s=600, submitted_at="2026-09-17T11:52:00Z")
        result = self.validate(self.proposal_for(envelope, time_s=120))
        self.assertEqual(result["verdict"], "validated", reasons(result))
        self.assertNotIn("deadline_exceeded", reason_codes(result))

    def test_one_second_below_and_one_second_above_the_deadline(self) -> None:
        envelope = self.envelope_for(deadline_s=600, submitted_at="2026-09-17T11:52:01Z")
        below = self.validate(self.proposal_for(envelope, time_s=120))
        self.assertEqual(below["verdict"], "validated", reasons(below))

        envelope = self.envelope_for(deadline_s=600, submitted_at="2026-09-17T11:51:59Z")
        above = self.validate(self.proposal_for(envelope, time_s=120))
        self.assertEqual(above["verdict"], "rejected", reasons(above))
        self.assertIn("deadline_exceeded", reason_codes(above))

    def test_the_deadline_check_names_its_units(self) -> None:
        envelope = self.envelope_for(deadline_s=600, submitted_at="2026-09-17T11:59:00Z")
        result = self.validate(self.proposal_for(envelope, time_s=120))
        rows = [row for row in result["checks"] if row["check_id"] == "budget_deadline"]
        self.assertEqual(len(rows), 1)
        self.assertIn("s", rows[0]["reason"])
        self.assertIn("60", rows[0]["reason"])


class LaneAndPolicyEmitterTests(BudgetBase):
    def test_a_resource_the_catalog_does_not_declare_is_lane_absent(self) -> None:
        envelope = self.envelope_for()
        proposal = self.proposal_for(
            envelope,
            expected={"time_s": 120, "attempts": 1, "context_tokens": 8192,
                      "resources": ["omen-arc", "no-such-rung"]})
        result = self.validate(proposal)
        self.assertEqual(result["verdict"], "rejected",
                         "a resource no catalog lane declares was silently dropped, so its "
                         "capacity was never checked")
        self.assertIn("lane_absent", reason_codes(result))
        self.assertIn("no-such-rung", reasons(result))

    def test_a_graph_target_in_no_registry_is_lane_absent(self) -> None:
        envelope = self.envelope_for()
        graph = {"nodes": [{"id": "n1", "route_kind": "direct_inference",
                            "target": "no_such_target",
                            "inputs": {"envelope_id": envelope["envelope_id"]},
                            "expected": {"attempts": 1}}], "edges": []}
        result = self.validate(self.proposal_for(envelope, selected_graph=graph))
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("lane_absent", reason_codes(result))

    def test_an_unverifiable_approval_is_reported_with_approval_unverified(self) -> None:
        envelope = self.envelope_for()
        proposal = self.proposal_for(envelope,
                                     required_authority=["merge_push_deploy"])
        first = self.validate(proposal)
        self.assertEqual(first["verdict"], "needs_approval")
        self.store_validation_as(first, self.run_id, self.caller)
        from hearth.operator import history

        approval_id = [ev for ev in history.read_run_history(self.run_id)
                       if ev["event_type"] == "approval.requested"][-1]["payload"]["approval_id"]
        self.set_key(HUMAN_APPROVER_KEY)
        approve.decide_approval(run_id=self.run_id, approval_id=approval_id,
                                decision="approve", approver=resolve_from_env(), now=self.now)
        self.set_key(UNRESTRICTED_KEY)

        target = paths.run_refs_dir(self.run_id) / f"approval_{approval_id}.json"
        record = json.loads(target.read_text(encoding="utf-8"))
        record["authorities"] = ["merge_push_deploy", "change_machine_or_network"]
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")

        again = self.validate(proposal)
        self.assertEqual(again["verdict"], "needs_approval")
        self.assertIn("approval_unverified", reason_codes(again),
                      "an approval record that no longer re-hashes was reported only as a "
                      "missing human decision")

    def test_every_reason_code_the_validator_declares_has_an_emitter(self) -> None:
        """A declared code with no emitter is a promise the record cannot keep."""
        source = (Path(paths.REPO_ROOT) / "hearth" / "operator" / "validate.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        emitted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in ("failed", "gated") or len(node.args) < 2:
                continue
            code = node.args[1]
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                emitted.add(code.value)
        declared = set(validate.VALIDATOR_REASON_CODES)
        self.assertEqual(sorted(declared - emitted), [],
                         "these reason codes are declared by the validator and emitted "
                         "nowhere in it")
        self.assertTrue(emitted <= set(validate.VALIDATION_REASON_CODES))

    def test_the_proposal_only_codes_are_documented_as_such(self) -> None:
        from hearth.operator import proposal as proposal_mod

        self.assertEqual(set(validate.PROPOSAL_REASON_CODES), set(proposal_mod.REASON_CODES))
        self.assertEqual(set(validate.VALIDATION_REASON_CODES),
                         set(validate.PROPOSAL_REASON_CODES)
                         | set(validate.VALIDATOR_REASON_CODES))


if __name__ == "__main__":
    unittest.main()
