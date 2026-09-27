"""Condition 3 (WI-G2b): D-113 companions are RESOLVED, not shape-checked.

Verifier B's two gaps against the WI-G2a candidate:

* a well-formed 40-hex `base_commit` that is not an object in any repository
  validated, and an isolated path that does not exist validated as long as it
  lay outside master. "Forty hex characters" is a shape; D-113 as amended says
  "paths, refs, work-item identity, test-target membership, authority subsets,
  and isolation must be resolved and verified".
* there was no machine-readable per-work-item prohibited-authority register, so
  "high-impact authorities ... may be prohibited by the work item" was carried
  only in the prose of a brief.

Derek's Gate 2 decision adds a versioned, structured work-item policy whose
digest is bound into the validation result, and requires real resolution:
commits and trees resolved in the declared repository, ambiguous or abbreviated
ids refused, paths checked for existence and for containment outside master and
outside any other work item's worktree, targets checked against the compiled
catalog, and requested authorities checked against the work item's prohibitions.

The object ids below are real and immutable in this repository: the frozen
WI-G2a candidate commit and tree, and the failed WI-G2 candidate commit. The
paths are the two frozen worktrees the program keeps.
"""

from __future__ import annotations

import json
import re
import unittest

from hearth.operator import (canonical, catalog as catalog_mod, envelope as envelope_mod,
                             history, paths, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import OperatorTestCase, UNRESTRICTED_KEY

G2A_COMMIT = "7ff409e229ab10e7626045733ef11794b00e3e08"
G2A_TREE = "2f7ecbe999e2b67a3479148d5af6f49f97ed3903"
G2_COMMIT = "a2d7a9197a1103fcf7af06d384f67617d5b86068"

G2A_WORKTREE = r"/home/derek/work/worktrees/commandcenter/g2a-envelope-hardening"
G2A_EVIDENCE = r"/home/derek/work/worktrees/commandcenter/g2a-envelope-hardening-evidence"
G2B_WORKTREE = r"/home/derek/work/worktrees/commandcenter/g2b-contract-closure"


def reason_codes(result: dict) -> list[str]:
    return [row.get("reason_code") for row in result["checks"]]


def reasons(result: dict) -> str:
    return " | ".join(row["reason"] for row in result["checks"] if row["result"] != "passed")


class WorkItemResolutionBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-work-item-resolution"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    def mode_fields(self, **overrides) -> dict:
        fields = {
            "work_item": "WI-G2a",
            "isolated_inputs": [G2A_WORKTREE],
            "isolated_outputs": [G2A_EVIDENCE],
            "base_commit": G2A_COMMIT,
            "base_tree": G2A_TREE,
            "bounded_authorities": ["call_door_generate"],
            "rollback_ref": G2_COMMIT,
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
                  "required_approvals": ["human-operator"],
                  "envelope_id": self.envelope["envelope_id"]}
        fields.update(overrides)
        return self.make_proposal(self.catalog, self.snapshot, **fields)

    def validate(self, proposal):
        return validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                          current_snapshot=self.snapshot, now=self.now,
                                          run_id=self.run_id)


class ResolutionTests(WorkItemResolutionBase):
    def test_a_fully_resolvable_test_mode_proposal_is_gated_not_rejected(self) -> None:
        """The control: every companion resolves, so the only thing left is the
        human decision D-113 requires."""
        result = self.validate(self.mode_proposal())
        self.assertEqual(result["verdict"], "needs_approval", reasons(result))
        self.assertTrue(result["test_mode"])

    def test_a_well_formed_but_nonexistent_base_commit_is_refused(self) -> None:
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(base_commit="d" * 40)))
        self.assertEqual(result["verdict"], "rejected",
                         "a 40-hex string that is not an object in the repository is not "
                         "an immutable base commit")
        self.assertIn("test_mode_invalid", reason_codes(result))
        self.assertIn("d" * 40, reasons(result))

    def test_a_base_tree_that_is_not_a_tree_is_refused(self) -> None:
        """A commit id peels to a tree; that does not make it one."""
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(base_tree=G2A_COMMIT)))
        self.assertEqual(result["verdict"], "rejected", reasons(result))
        self.assertIn("test_mode_invalid", reason_codes(result))

    def test_an_unresolvable_rollback_commit_is_refused(self) -> None:
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(rollback_ref="e" * 40)))
        self.assertEqual(result["verdict"], "rejected", reasons(result))
        self.assertIn("test_mode_invalid", reason_codes(result))

    def test_an_abbreviated_or_ambiguous_object_id_is_refused(self) -> None:
        for value in ("7ff409e2", "7ff409e229ab10e7626045733ef11794b00e3e0", "HEAD", ""):
            with self.subTest(base_commit=value):
                fields = self.mode_fields(base_commit=value)
                result = self.validate(self.mode_proposal(test_mode_fields=fields))
                self.assertEqual(result["verdict"], "rejected", reasons(result))
                self.assertIn("test_mode_invalid", reason_codes(result))

    def test_a_nonexistent_isolated_path_is_refused(self) -> None:
        missing = G2A_WORKTREE + r"\this-path-does-not-exist"
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(isolated_inputs=[missing])))
        self.assertEqual(result["verdict"], "rejected",
                         "an isolated input that does not exist was accepted because it "
                         "lay outside master")
        self.assertIn("test_mode_invalid", reason_codes(result))
        self.assertIn("this-path-does-not-exist", reasons(result))

    def test_an_isolated_path_inside_master_is_refused(self) -> None:
        master = str(paths.operator_config()["catalog"]["worktree_policy"]
                     ["control_plane_repository"])
        for override in ({"isolated_inputs": [master + r"\hearth"]},
                         {"isolated_outputs": ["knowledge/"]}):
            with self.subTest(override=tuple(override)):
                result = self.validate(self.mode_proposal(
                    test_mode_fields=self.mode_fields(**override)))
                self.assertEqual(result["verdict"], "rejected", reasons(result))
                self.assertIn("test_mode_invalid", reason_codes(result))

    def test_an_isolated_path_inside_another_work_items_worktree_is_refused(self) -> None:
        """Isolation is not "outside master": it is inside THIS work item's roots."""
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(isolated_outputs=[G2B_WORKTREE])))
        self.assertEqual(result["verdict"], "rejected",
                         "WI-G2a declared an output inside the WI-G2b worktree and it was "
                         "accepted: one writer per mutable surface is not enforced")
        self.assertIn("test_mode_invalid", reason_codes(result))

    def test_an_authority_prohibited_by_the_work_item_is_refused_with_policy_block(self) -> None:
        proposal = self.mode_proposal(
            required_authority=["call_door_generate", "merge_push_deploy"],
            test_mode_fields=self.mode_fields(
                bounded_authorities=["call_door_generate", "merge_push_deploy"]))
        result = self.validate(proposal)
        self.assertEqual(result["verdict"], "rejected",
                         "a work item that prohibits merge_push_deploy still validated a "
                         "proposal requiring it")
        self.assertIn("policy_block", reason_codes(result))
        self.assertIn("merge_push_deploy", reasons(result))

    def test_a_work_item_outside_the_released_policy_is_refused(self) -> None:
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(work_item="WI-NOT-RELEASED")))
        self.assertEqual(result["verdict"], "rejected", reasons(result))
        self.assertIn("test_mode_invalid", reason_codes(result))

    def test_a_test_target_outside_the_catalog_is_refused(self) -> None:
        result = self.validate(self.mode_proposal(
            test_mode_fields=self.mode_fields(test_targets=["no_such_implementation"])))
        self.assertEqual(result["verdict"], "rejected", reasons(result))
        self.assertIn("test_mode_invalid", reason_codes(result))


class PolicyBindingTests(WorkItemResolutionBase):
    def test_the_work_item_policy_digest_is_bound_into_the_validation_result(self) -> None:
        result = self.validate(self.mode_proposal())
        self.assertIn("work_item_policy", result,
                      "the validation result binds no work-item policy digest, so nothing "
                      "records WHICH register the run was judged against")
        bound = result["work_item_policy"]
        self.assertIsNotNone(bound)
        self.assertEqual(bound["work_item"], "WI-G2a")
        self.assertEqual(bound["contract_version"], "work-items.v1")
        expected = canonical.source_sha256(paths.ETC / "work-items.toml")
        self.assertEqual(bound["digest"], expected,
                         "the bound digest is not the newline-normalized digest of "
                         "hearth/etc/work-items.toml")
        self.assertTrue(bound["entry_digest"])
        self.assertEqual(bound["path"], "hearth/etc/work-items.toml")

    def test_a_normal_mode_validation_binds_no_work_item_policy(self) -> None:
        proposal = self.make_proposal(self.catalog, self.snapshot,
                                      envelope_id=self.envelope["envelope_id"])
        result = self.validate(proposal)
        self.assertIn("work_item_policy", result)
        self.assertIsNone(result["work_item_policy"])

    def test_the_policy_file_is_versioned_structured_and_self_verifying(self) -> None:
        from hearth.operator import work_items

        policy = work_items.load_policy()
        self.assertEqual(policy["contract_version"], "work-items.v1")
        self.assertTrue(policy["policy_version"])
        self.assertTrue(policy["digest"])
        released = {entry["id"] for entry in policy["work_items"]
                    if entry["status"] == "released"}
        self.assertEqual(released, {"WI-G1", "WI-G2", "WI-G2a", "WI-G2b"},
                         "the released work items are the gates Derek has opened")
        self.assertNotIn(
            "released_work_items", paths.operator_config().get("test_mode", {}),
            "operator.toml still carries a competing register; two registers that can "
            "disagree are not a register")
        for entry in policy["work_items"]:
            self.assertEqual(entry["digest"], work_items.entry_digest(entry),
                             f"{entry['id']} carries a digest that does not match its own "
                             "declaration")
            self.assertTrue(entry["permitted_isolated_roots"])
            self.assertIsInstance(entry["prohibited_authorities"], list)

    def test_an_edited_policy_entry_whose_digest_no_longer_matches_is_refused(self) -> None:
        from hearth.operator import work_items

        source = (paths.ETC / "work-items.toml").read_text(encoding="utf-8")
        edited, count = re.subn(r"prohibited_authorities = \[[^\]]*\]",
                                "prohibited_authorities = []", source, count=1)
        self.assertEqual(count, 1, "the policy declares no prohibited_authorities to edit")
        tampered = self.home / "work-items-tampered.toml"
        tampered.write_text(edited, encoding="utf-8")
        with self.assertRaises(work_items.WorkItemPolicyError) as ctx:
            work_items.load_policy(tampered)
        self.assertIn("digest", str(ctx.exception))


class TestModeEventPolicyTests(WorkItemResolutionBase):
    """`history.TEST_MODE_EVENT_TYPES` was dead policy; it is now enforced."""

    def test_the_test_mode_flag_is_refused_on_an_event_outside_the_declared_set(self) -> None:
        with self.assertRaises(history.HistoryError) as ctx:
            history.append("task.received", {"envelope_id": self.envelope["envelope_id"]},
                           run_id=self.run_id, test_mode=True)
        self.assertIn("test_mode", str(ctx.exception))
        target = paths.run_history_path(self.run_id)
        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
                if line.strip()] if target.is_file() else []
        self.assertEqual([row for row in rows if row.get("test_mode")], [],
                         "a refused append wrote the event anyway")

    def test_the_declared_test_mode_events_still_carry_the_flag(self) -> None:
        result = self.validate(self.mode_proposal())
        self.store_validation_as(result, self.run_id, self.caller)
        flagged = [ev for ev in history.read_run_history(self.run_id)
                   if ev["event_type"] in history.TEST_MODE_EVENT_TYPES]
        self.assertTrue(flagged)
        self.assertTrue(all(ev.get("test_mode") is True for ev in flagged))


if __name__ == "__main__":
    unittest.main()
