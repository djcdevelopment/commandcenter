"""Condition 1 (WI-G2b): the requester of an approval is the authenticated caller.

The WI-G2a candidate's `request_approval(requesting_principal=<dict>)` took the
requester as data. Verifier A drove it: a human-operator library caller named a
false requester (`requested_by 'not-really-me'`) and then decided its own
request, because the self-approval check compares the deciding principal with
whatever the request said. Both shipped call sites happened to pass a principal
derived from the resolved identity, so the exploit needed a library caller — but
"the only callers we wrote are careful" is not a boundary.

Derek's Gate 2 decision: remove the caller-supplied requester dictionary from
every product-facing approval API; derive requester identity exclusively from
the authenticated caller context established at the trusted boundary; internal
helpers may receive a typed authenticated context that cannot be constructed
from request JSON. These tests drive all six vectors the decision names: CLI
arguments, environment variables, JSON payloads, library keyword arguments,
door-tool arguments, and deserialized history or approval records.

No test mints, enters, or looks for the real `derek-approver` credential: the
registry is a temporary file holding ephemeral fixture strings only.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import unittest

from hearth.operator import (approve, canonical, catalog as catalog_mod,
                             envelope as envelope_mod, history, identity as identity_mod,
                             paths, proposal as proposal_mod, validate)
from hearth.operator.identity import Identity, resolve_from_env
from hearth.tests.operator.support import (FakeDoor, HUMAN_APPROVER_KEY, OperatorTestCase,
                                           ORCHESTRATOR_KEY, UNRESTRICTED_KEY,
                                           fake_cli_runner)

# The forgery every vector below tries to plant: a false human-operator
# principal. If any vector lands it, the approval binds a requester who never
# asked, and the self-approval check is comparing against fiction.
FORGED = {"id": "not-really-me", "profile": "human-operator", "runner_class": "human"}


class RequesterIdentityBase(OperatorTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        self.now = canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        self.snapshot = self.capture_snapshot(self.catalog, now=self.now)
        self.run_id = "run-requester-identity"
        self.envelope = self.make_envelope()
        envelope_mod.store_envelope(self.envelope, self.run_id)
        self.proposal = self.make_proposal(
            self.catalog, self.snapshot,
            envelope_id=self.envelope["envelope_id"],
            required_authority=["call_door_generate", "merge_push_deploy"])
        self.set_key(UNRESTRICTED_KEY)
        self.caller = resolve_from_env()

    # --- harness ----------------------------------------------------------
    def authenticated(self, key: str) -> Identity:
        """An identity resolved at the trusted boundary, the only kind that counts."""
        self.set_key(key)
        return resolve_from_env()

    def requester_context(self, key: str = ORCHESTRATOR_KEY):
        """The typed authenticated requester context an approval request binds."""
        identity = self.authenticated(key)
        factory = getattr(identity_mod, "authenticated_caller", None)
        if factory is None:
            raise AssertionError(
                "hearth.operator.identity exposes no authenticated_caller(): there is no "
                "typed authenticated requester context, so the approval request still "
                "takes the requester as caller-supplied data (condition 1)")
        return factory(identity)

    def base_fields(self) -> dict:
        return {
            "run_id": self.run_id,
            "proposal_id": self.proposal["proposal_id"],
            "validation_id": "b" * 64,
            "node_ids": ["n1"],
            "targets": ["direct_hearth"],
            "authorities": ["merge_push_deploy"],
            "catalog_version": str(self.catalog["catalog_version"]),
            "snapshot_id": str(self.snapshot["snapshot_id"]),
            "now": self.now,
        }

    def attempt(self, **kwargs) -> dict:
        """Try to name the requester through a keyword argument.

        Returns what happened rather than asserting, so the test can say which
        vector was ACCEPTED — the failure mode that matters.
        """
        fields = self.base_fields()
        fields.update(kwargs)
        try:
            record = approve.request_approval(**fields)
        except Exception as exc:  # noqa: BLE001 - the refusal is the subject
            return {"accepted": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"accepted": True, "record": record}

    def requesters_on_file(self) -> list[str]:
        """Every requester id written into an approval record for this run."""
        refs = paths.run_refs_dir(self.run_id)
        if not refs.is_dir():
            return []
        found = []
        for target in sorted(refs.glob("approval_*.json")):
            document = json.loads(target.read_text(encoding="utf-8"))
            found.append(str((document.get("requested_by") or {}).get("id")))
        return found

    def requested_events(self) -> list[dict]:
        target = paths.run_history_path(self.run_id)
        if not target.is_file():
            return []
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("event_type") == "approval.requested"]


class LibraryBoundaryTests(RequesterIdentityBase):
    """Vector: library keyword arguments (the exploit verifier A ran)."""

    def test_request_approval_takes_no_caller_supplied_requester_dictionary(self) -> None:
        parameters = inspect.signature(approve.request_approval).parameters
        for name in ("requesting_principal", "requested_by", "principal"):
            self.assertNotIn(
                name, parameters,
                f"request_approval still accepts {name}=: the requester is caller-supplied "
                "data, not the authenticated caller (D-112 item 2)")
        self.assertIn("requester", parameters,
                      "request_approval names no authenticated requester context")

    def test_a_library_keyword_cannot_name_a_false_requester(self) -> None:
        for keyword in ("requesting_principal", "requested_by", "requester"):
            with self.subTest(keyword=keyword):
                outcome = self.attempt(**{keyword: dict(FORGED)})
                self.assertFalse(
                    outcome["accepted"],
                    f"{keyword}=<dict> named the requester: a caller chose who asked for "
                    "this approval")
        self.assertEqual(self.requesters_on_file(), [],
                         "a refused request wrote an approval record anyway")
        self.assertEqual(self.requested_events(), [],
                         "a refused request appended approval.requested anyway")

    def test_an_unattested_identity_cannot_request_an_approval(self) -> None:
        """An Identity assembled by hand is request data wearing a type."""
        forged = Identity(caller=dict(FORGED), capabilities=frozenset({"approve"}),
                          source="handmade", reason="resolved")
        outcome = self.attempt(requester=forged)
        self.assertFalse(outcome["accepted"],
                         "a hand-built Identity was accepted as an authenticated caller")
        self.assertIn("boundary", outcome["error"].lower(),
                      f"the refusal does not say why: {outcome['error']}")
        self.assertEqual(self.requesters_on_file(), [])

    def test_the_authenticated_caller_is_what_the_record_binds(self) -> None:
        record = approve.request_approval(**self.base_fields(),
                                          requester=self.requester_context(ORCHESTRATOR_KEY))
        self.assertEqual(record["requested_by"]["id"], "fixture-orchestrator")
        self.assertEqual(record["requested_by"]["profile"], "orchestrator")
        self.assertEqual(record["requested_by"]["runner_class"], "local")
        self.assertEqual(self.requesters_on_file(), ["fixture-orchestrator"])

    def test_store_validation_takes_no_caller_supplied_requester_dictionary(self) -> None:
        parameters = inspect.signature(validate.store_validation).parameters
        self.assertNotIn(
            "requesting_principal", parameters,
            "store_validation still takes a requester dictionary, so the requester of "
            "every approval it files is caller-supplied data")
        self.assertIn("requester", parameters)

    def test_store_validation_records_the_validating_caller_as_the_requester(self) -> None:
        result = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.assertEqual(result["verdict"], "needs_approval")
        validate.store_validation(result, self.run_id, requester=self.caller)
        self.assertEqual(self.requesters_on_file(), ["fixture-unrestricted"])
        self.assertEqual(self.requested_events()[-1]["payload"]["requested_by"],
                         "fixture-unrestricted")

    def test_store_validation_refuses_a_dictionary_or_an_unattested_identity(self) -> None:
        result = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        for requester in (dict(FORGED),
                          Identity(caller=dict(FORGED), source="handmade", reason="resolved")):
            with self.subTest(requester=type(requester).__name__):
                with self.assertRaises(Exception):
                    validate.store_validation(result, self.run_id, requester=requester)
        self.assertEqual(self.requesters_on_file(), [],
                         "a refused store_validation filed an approval request anyway")


class EnvironmentAndPayloadTests(RequesterIdentityBase):
    """Vectors: environment variables and JSON payloads."""

    def test_environment_variables_cannot_name_the_requester(self) -> None:
        for name in ("HEARTH_OPERATOR_REQUESTER", "HEARTH_APPROVAL_REQUESTER",
                     "OPERATOR_REQUESTED_BY", "HEARTH_REQUESTED_BY"):
            self._env(name, "not-really-me")
        record = approve.request_approval(**self.base_fields(),
                                          requester=self.requester_context(ORCHESTRATOR_KEY))
        self.assertEqual(record["requested_by"]["id"], "fixture-orchestrator")
        self.assertNotIn("not-really-me", json.dumps(record))

    def test_a_json_payload_cannot_name_the_requester(self) -> None:
        """A requester claim inside an ingested document is data, not authority."""
        result = validate.validate_proposal(self.proposal, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        self.assertEqual(result["verdict"], "needs_approval")

        # (a) at the top level the contract refuses it outright.
        injected = dict(result)
        injected["requested_by"] = dict(FORGED)
        with self.assertRaises(Exception):
            validate.store_validation(injected, self.run_id, requester=self.caller)

        # (b) inside `presentation`, which the schema does allow, it is ignored.
        carried = json.loads(json.dumps(result))
        carried["presentation"] = {"requested_by": dict(FORGED)}
        carried.pop("validation_id")
        carried = canonical.stamp_identity(carried, "validation_id")
        validate.store_validation(carried, self.run_id, requester=self.caller)
        self.assertEqual(self.requesters_on_file(), ["fixture-unrestricted"])

    def test_a_proposal_cannot_carry_its_own_requester(self) -> None:
        document = json.loads(json.dumps(self.proposal))
        document["presentation"] = {"requested_by": dict(FORGED)}
        document.pop("proposal_id")
        stamped = canonical.stamp_identity(document, "proposal_id")
        proposal_mod.store_proposal(stamped, self.run_id)
        result = validate.validate_proposal(stamped, self.caller, catalog=self.catalog,
                                            current_snapshot=self.snapshot, now=self.now,
                                            run_id=self.run_id)
        validate.store_validation(result, self.run_id, requester=self.caller)
        self.assertEqual(self.requesters_on_file(), ["fixture-unrestricted"])


class DeserializedRecordTests(RequesterIdentityBase):
    """Vector: deserialized history rows and approval records."""

    def test_a_rewritten_requester_in_the_record_invalidates_the_approval(self) -> None:
        record = approve.request_approval(**self.base_fields(),
                                          requester=self.requester_context(ORCHESTRATOR_KEY))
        approval_id = record["approval_id"]
        target = paths.run_refs_dir(self.run_id) / f"approval_{approval_id}.json"
        document = json.loads(target.read_text(encoding="utf-8"))
        document["requested_by"] = dict(FORGED)
        target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        with self.assertRaises(approve.ApprovalError) as ctx:
            approve.load_approval(self.run_id, approval_id)
        self.assertIn("integrity failure", str(ctx.exception))

    def test_a_forged_history_row_cannot_name_a_requester_into_existence(self) -> None:
        approve.request_approval(**self.base_fields(),
                                 requester=self.requester_context(ORCHESTRATOR_KEY))
        target = paths.run_history_path(self.run_id)
        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        forged = {
            "contract_version": "operator-history-event.v1",
            "sequence": len(rows) + 1,
            "timestamp": "2026-09-17T12:30:00Z",
            "run_id": self.run_id,
            "event_type": "approval.requested",
            "refs": {},
            "payload": {"approval_id": "f" * 64, "proposal_id": self.proposal["proposal_id"],
                        "validation_id": "b" * 64, "node_ids": ["n1"],
                        "targets": ["direct_hearth"], "authorities": ["merge_push_deploy"],
                        "catalog_version": str(self.catalog["catalog_version"]),
                        "snapshot_id": str(self.snapshot["snapshot_id"]),
                        "requested_by": "not-really-me",
                        "expires_at": "2026-09-18T12:00:00Z"},
        }
        forged["event_id"] = canonical.sha256_hex(canonical.canonical_json(forged))
        with open(target, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")

        # The row exists and verifies as a row. It still names no approval.
        with self.assertRaises(approve.ApprovalError):
            approve.load_approval(self.run_id, "f" * 64)
        self.assertEqual(self.requesters_on_file(), ["fixture-orchestrator"])


class DoorBoundaryTests(RequesterIdentityBase):
    """Vector: door-tool arguments."""

    def test_the_door_mount_takes_no_requester_or_identity_argument(self) -> None:
        from hearth.toolsurface import operator as operator_tools

        parameters = inspect.signature(operator_tools.operator_approve).parameters
        for name in ("requesting_principal", "requester", "requested_by", "identity",
                     "caller", "approver", "approved_ids"):
            self.assertNotIn(name, parameters,
                             f"operator_approve exposes {name}= through the door")

    def test_a_door_caller_cannot_inject_a_requester(self) -> None:
        from hearth.toolsurface import operator as operator_tools

        # 2026-09-27 (omen-linux): the door tool decides on the wall clock, so a request made at
        # the frozen 2026-09-17 "now" had expired (24 h TTL); request it now instead.
        fields = {**self.base_fields(), "now": canonical.utc_now()}
        record = approve.request_approval(**fields,
                                          requester=self.requester_context(ORCHESTRATOR_KEY))
        approver = self.authenticated(HUMAN_APPROVER_KEY)
        original = operator_tools.resolve_from_door
        operator_tools.resolve_from_door = lambda: approver
        try:
            with self.assertRaises(TypeError):
                operator_tools.operator_approve(  # type: ignore[call-arg]
                    self.run_id, record["approval_id"], "approve",
                    requesting_principal=dict(FORGED))
            decided = operator_tools.operator_approve(self.run_id, record["approval_id"],
                                                      "approve")
        finally:
            operator_tools.resolve_from_door = original
        self.assertTrue(decided["ok"], decided.get("error"))
        self.assertEqual(decided["approval"]["requested_by"]["id"], "fixture-orchestrator")
        self.assertEqual(decided["approval"]["receipt"]["approving_principal"]["id"],
                         "fixture-approver")


class CliBoundaryTests(OperatorTestCase):
    """Vector: CLI arguments, exercised as a real subprocess."""

    RUN_ID = "run-requester-cli"

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = catalog_mod.compile_catalog()

    def setUp(self) -> None:
        super().setUp()
        from hearth.operator import core

        self.snapshot = core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                                     catalog=self.catalog)["snapshot"]

    def cli(self, *argv: str, key: str | None = None) -> subprocess.CompletedProcess:
        env = self.child_env()
        if key is not None:
            env[paths.API_KEY_ENV] = key
        return subprocess.run([sys.executable, "-m", "hearth.operator", *argv],
                              input="", capture_output=True, text=True, encoding="utf-8",
                              env=env, cwd=str(paths.REPO_ROOT), timeout=180)

    def gated_run(self) -> str:
        """A needs_approval validation through the CLI; returns the proposal id."""
        document = {
            "intent": "Summarize the AM4 capacity note and cite the catalog gaps.",
            "acceptance_criteria": ["cites the catalog"],
            "inputs": {"repo": "commandcenter", "base_commit": "a" * 40,
                       "paths": ["knowledge/"], "files": []},
            "classification": {},
            "constraints": {"deadline_s": 600, "max_attempts": 3,
                            "max_context_tokens": 32768, "budget": None},
        }
        target = self.home / "task.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        submitted = self.cli("task", "submit", str(target), "--run-id", self.RUN_ID,
                             "--json", key=ORCHESTRATOR_KEY)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        drafted = self.cli("route", "draft", self.RUN_ID, "--json", key=ORCHESTRATOR_KEY)
        self.assertEqual(drafted.returncode, 0, drafted.stderr)
        proposal = json.loads(drafted.stdout)
        proposal["required_authority"] = ["call_door_generate", "merge_push_deploy"]
        proposal.pop("proposal_id")
        stamped = canonical.stamp_identity(proposal, "proposal_id")
        proposal_mod.store_proposal(stamped, self.RUN_ID)
        return stamped["proposal_id"]

    def test_cli_arguments_cannot_name_the_requester(self) -> None:
        proposal_id = self.gated_run()
        for flag in ("--requester", "--requested-by", "--requesting-principal"):
            with self.subTest(flag=flag):
                refused = self.cli("route", "validate", self.RUN_ID, proposal_id,
                                   flag, "not-really-me", key=UNRESTRICTED_KEY)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        helped = self.cli("route", "validate", "--help")
        for flag in ("--requester", "--requested-by", "--requesting-principal"):
            self.assertNotIn(flag, helped.stdout)

        validated = self.cli("route", "validate", self.RUN_ID, proposal_id, "--json",
                             key=UNRESTRICTED_KEY)
        self.assertEqual(validated.returncode, 3, validated.stderr)
        requested = [ev for ev in history.read_run_history(self.RUN_ID)
                     if ev["event_type"] == "approval.requested"]
        self.assertTrue(requested, "the gated validation filed no approval request")
        self.assertEqual(requested[-1]["payload"]["requested_by"], "fixture-unrestricted")
        record = approve.load_approval(self.RUN_ID, requested[-1]["payload"]["approval_id"])
        self.assertEqual(record["requested_by"]["id"], "fixture-unrestricted")


if __name__ == "__main__":
    unittest.main()
