"""Regression tests for the unenforced-contract, privacy and explanation
exploits reproduced by the WI-G2 independent verification (report items 9 and
10, appendix A sections 3.4/4.6, appendix B G2-C12).

The four contracts were enforced nowhere at runtime; `store_proposal` was an
unguarded ingestion point; `load_envelope` accepted a `task-envelope.v2`
document with an extra top-level field; the `task.received` payload carried the
full task intent; and `explain` printed two statements the record did not
support.

Each test is named for the exploit. All were red against `a2d7a91`.
"""

from __future__ import annotations

import copy
import json
import unittest

from hearth.operator import (approve, artifacts, canonical, catalog as catalog_mod,
                             envelope as envelope_mod, explain, history, paths,
                             proposal as proposal_mod, validate)
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (HUMAN_APPROVER_KEY, OperatorTestCase,
                                           ORCHESTRATOR_KEY, UNRESTRICTED_KEY)


class ContractEnforcementBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-contract-regression"
        self.envelope = self.make_envelope()
        self.proposal = self.make_proposal(self.catalog, self.snapshot,
                                           envelope_id=self.envelope["envelope_id"])
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()


class EnvelopeIngestionTests(ContractEnforcementBase):
    def write(self, document: dict):
        target = self.home / "envelope-in.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        return target

    def test_an_unknown_envelope_contract_version_is_refused_on_load(self) -> None:
        document = copy.deepcopy(self.envelope)
        document["contract_version"] = "task-envelope.v2"
        document["envelope_id"] = canonical.identity_of(document, "envelope_id")
        with self.assertRaises(envelope_mod.EnvelopeError) as ctx:
            envelope_mod.load_envelope(self.write(document))
        self.assertIn("task-envelope.v2", str(ctx.exception))

    def test_an_extra_top_level_field_is_refused_on_load(self) -> None:
        document = copy.deepcopy(self.envelope)
        document["orchestrator"] = {"client": "claude-code"}
        document["envelope_id"] = canonical.identity_of(document, "envelope_id")
        with self.assertRaises(envelope_mod.EnvelopeError) as ctx:
            envelope_mod.load_envelope(self.write(document))
        self.assertIn("orchestrator", str(ctx.exception))

    def test_the_envelope_intent_is_capped_at_four_thousand_characters(self) -> None:
        with self.assertRaises(envelope_mod.EnvelopeError) as ctx:
            self.make_envelope(intent="x" * 4001)
        self.assertIn("4000", str(ctx.exception))
        self.make_envelope(intent="x" * 4000)  # the cap itself is accepted

    def test_the_task_received_payload_carries_no_intent_prose(self) -> None:
        marker = "VERIFYPROMPT-XYZZY"
        envelope = self.make_envelope(intent=f"Summarize the capacity note ({marker})")
        envelope_mod.store_envelope(envelope, self.run_id)
        events = history.read_run_history(self.run_id)
        payload = events[0]["payload"]
        self.assertNotIn(marker, json.dumps(payload))
        self.assertNotIn("intent", payload)
        self.assertEqual(payload["envelope_id"], envelope["envelope_id"])
        self.assertIn("intent_sha256", payload)

    def test_a_retained_raw_prompt_is_a_private_content_addressed_artifact(self) -> None:
        envelope = self.make_envelope()
        raw = "the complete raw prompt, including <think>hidden</think> reasoning"
        envelope_mod.store_envelope(envelope, self.run_id, raw_prompt=raw)
        events = history.read_run_history(self.run_id)
        blob = json.dumps(events)
        self.assertNotIn("the complete raw prompt", blob)
        self.assertNotIn("hidden", blob)
        digests = [ev["payload"].get("raw_prompt_sha256") for ev in events
                   if ev["event_type"] == "task.received"]
        self.assertTrue(digests[0])


class ProposalIngestionTests(ContractEnforcementBase):
    def test_store_proposal_refuses_a_reasoning_bearing_proposal(self) -> None:
        document = copy.deepcopy(self.proposal)
        document["uncertainty"] = {"confidence": "high", "unknowns": [],
                                   "thinking": "step one, step two"}
        document["proposal_id"] = canonical.identity_of(document, "proposal_id")
        with self.assertRaises(proposal_mod.ProposalError):
            proposal_mod.store_proposal(document, self.run_id)
        self.assertEqual(history.read_run_history(self.run_id), [])

    def test_store_proposal_refuses_an_invalid_reason_code(self) -> None:
        document = copy.deepcopy(self.proposal)
        document["rejected_routes"] = [{"route_id": "r9", "route_kind": "planning",
                                        "target": "propose_schedule",
                                        "reason_code": "because_i_said_so",
                                        "reason_detail": "no"}]
        document["proposal_id"] = canonical.identity_of(document, "proposal_id")
        with self.assertRaises(proposal_mod.ProposalError):
            proposal_mod.store_proposal(document, self.run_id)

    def test_store_proposal_refuses_a_tampered_identity(self) -> None:
        document = copy.deepcopy(self.proposal)
        document["rationale"] = "a different rationale than the one that was hashed"
        with self.assertRaises(proposal_mod.ProposalError):
            proposal_mod.store_proposal(document, self.run_id)

    def test_store_proposal_refuses_an_unknown_contract_version(self) -> None:
        document = copy.deepcopy(self.proposal)
        document["contract_version"] = "route-proposal.v9"
        document["proposal_id"] = canonical.identity_of(document, "proposal_id")
        with self.assertRaises(proposal_mod.ProposalError):
            proposal_mod.store_proposal(document, self.run_id)

    def test_store_proposal_refuses_a_schema_violation(self) -> None:
        document = copy.deepcopy(self.proposal)
        document["expected"] = {"time_s": 1}
        document["proposal_id"] = canonical.identity_of(document, "proposal_id")
        with self.assertRaises(proposal_mod.ProposalError):
            proposal_mod.store_proposal(document, self.run_id)


class ContractsEnforcedEverywhereTests(ContractEnforcementBase):
    def test_every_stored_document_validates_against_its_contract(self) -> None:
        envelope_mod.store_envelope(self.envelope, self.run_id)
        proposal_mod.store_proposal(self.proposal, self.run_id)
        result = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.store_validation_as(result, self.run_id, self.caller)

        refs = paths.run_refs_dir(self.run_id)
        canonical.validate_contract(
            json.loads((refs / "envelope.json").read_text(encoding="utf-8")),
            "task-envelope.v1")
        canonical.validate_contract(
            json.loads((refs / f"proposal_{self.proposal['proposal_id']}.json")
                       .read_text(encoding="utf-8")), "route-proposal.v1")
        canonical.validate_contract(
            json.loads((refs / f"validation_{result['validation_id']}.json")
                       .read_text(encoding="utf-8")), "validation-result.v1")
        for event in history.read_run_history(self.run_id):
            canonical.validate_contract(event, "operator-history-event.v1")

    def test_the_approval_record_has_its_own_contract(self) -> None:
        record = approve.request_approval(
            run_id=self.run_id, proposal_id=self.proposal["proposal_id"],
            validation_id="b" * 64, node_ids=["n1"], targets=["direct_hearth"],
            authorities=["merge_push_deploy"],
            catalog_version=str(self.catalog["catalog_version"]),
            snapshot_id=str(self.snapshot["snapshot_id"]),
            **self.approval_requester_fields(ORCHESTRATOR_KEY),
            now=self.now)
        canonical.validate_contract(record, "approval.v1")
        self.assertTrue((paths.CONTRACTS / "approval.v1.schema.json").is_file())

    def test_history_refuses_an_event_that_violates_its_contract(self) -> None:
        with self.assertRaises(history.HistoryError):
            history.append("task.received", {"ok": True}, run_id=self.run_id,
                           envelope_id="not-a-digest")


class ReasoningRedactionTests(ContractEnforcementBase):
    def test_hidden_reasoning_never_reaches_the_private_artifact_store(self) -> None:
        secret = "SECRET-CHAIN-OF-THOUGHT"
        record = artifacts.ingest_artifact(
            self.run_id, f"answer <think>{secret}</think> tail".encode("utf-8"),
            "text/plain")
        blob = artifacts.read_artifact_bytes(record["sha256"]) or b""
        self.assertNotIn(secret.encode("utf-8"), blob)

    def test_removal_metadata_carries_only_the_four_declared_keys(self) -> None:
        record = artifacts.ingest_artifact(
            self.run_id, {"answer": "42", "reasoning": "long hidden chain",
                          "text": "visible <think>hidden</think>"},
            "application/json")
        self.assertEqual(sorted(record["ingestion"]),
                         ["policy_version", "reasoning_removed", "removed_bytes",
                          "removed_names"])
        self.assertTrue(record["ingestion"]["reasoning_removed"])
        self.assertGreater(record["ingestion"]["removed_bytes"], 0)
        self.assertNotIn("long hidden chain", json.dumps(record))

    def test_the_redaction_policy_is_versioned_in_operator_toml(self) -> None:
        config = paths.operator_config()["ingestion"]
        self.assertTrue(config["policy_version"])
        self.assertTrue(config["redact_blocks"])
        record = artifacts.ingest_artifact(self.run_id, "plain answer", "text/plain")
        self.assertEqual(record["ingestion"]["policy_version"], config["policy_version"])


class ExplainTests(ContractEnforcementBase):
    def build_run(self) -> dict:
        envelope_mod.store_envelope(self.envelope, self.run_id)
        proposal = self.make_proposal(
            self.catalog, self.snapshot, envelope_id=self.envelope["envelope_id"],
            required_authority=["call_door_generate", "merge_push_deploy"],
            assumptions=["omen-arc stays resident for the planning window"],
            uncertainty={"confidence": "medium", "unknowns": ["decode rate at depth"]},
            rejected_routes=[{"route_id": "propose_schedule", "route_kind": "planning",
                              "target": "propose_schedule",
                              "reason_code": "implementation_not_live",
                              "reason_detail": "status is BUILT NOT DEPLOYED"}])
        proposal_mod.store_proposal(proposal, self.run_id)
        result = validate.validate_proposal(proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.store_validation_as(result, self.run_id, self.caller)
        return result

    def test_explain_renders_the_validation_verdict_reasons_and_capacity_facts(self) -> None:
        result = self.build_run()
        narrative = explain.explain_run(self.run_id)
        self.assertIn(result["verdict"], narrative)
        self.assertIn("human_required", narrative)
        self.assertIn("omen-arc", narrative)
        self.assertIn("door", narrative.lower())
        self.assertIn("omen-arc stays resident", narrative)
        self.assertIn("decode rate at depth", narrative)
        self.assertIn("medium", narrative)

    def test_explain_never_claims_no_approval_was_required(self) -> None:
        self.build_run()
        narrative = explain.explain_run(self.run_id)
        self.assertNotIn("No human approval was required", narrative)
        self.assertIn("approval", narrative.lower())

    def test_explain_states_no_approval_record_found_rather_than_none_required(self) -> None:
        envelope_mod.store_envelope(self.envelope, self.run_id)
        history.append("route.proposed", {"proposal_id": self.proposal["proposal_id"]},
                       run_id=self.run_id)
        narrative = explain.explain_run(self.run_id)
        self.assertNotIn("No human approval was required", narrative)

    def test_explain_refuses_to_render_a_document_that_fails_its_contract(self) -> None:
        """A projection is a boundary too: content from a document that does not
        pass its contract is named as refused, never rendered as if it had."""
        self.build_run()
        target = paths.run_refs_dir(self.run_id) / "envelope.json"
        document = json.loads(target.read_text(encoding="utf-8"))
        document["orchestrator"] = {"client": "smuggled-in"}
        target.write_text(json.dumps(document), encoding="utf-8")

        narrative = explain.explain_run(self.run_id)
        self.assertIn("refused to render", narrative)
        self.assertNotIn("smuggled-in", narrative)

    def test_explain_derives_durability_from_artifact_records(self) -> None:
        envelope_mod.store_envelope(self.envelope, self.run_id)
        narrative = explain.explain_run(self.run_id)
        self.assertNotIn("hashes verified", narrative)

        artifacts.ingest_artifact(self.run_id, {"answer": "42"}, "application/json",
                                  retention_class="required")
        narrative = explain.explain_run(self.run_id)
        self.assertIn("auditable_only", narrative)
        self.assertIn("digest", narrative.lower())


if __name__ == "__main__":
    unittest.main()
