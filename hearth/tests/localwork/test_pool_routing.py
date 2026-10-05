"""Qualified heterogeneous lane admission, independent of deployed hosts or inference."""
from __future__ import annotations

import json
import threading
import sqlite3
from unittest import mock
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hearth.localwork.service import LocalWorkError, LocalWorkService
from hearth.tests.localwork import test_service as fixtures
from hearth.toolsurface.backends import load_pool


class PoolRoutingTests(unittest.TestCase):
    setUpBase = fixtures.CarryProcedureTests.setUp
    execution_service = fixtures.CarryProcedureTests.execution_service
    tearDown = fixtures.CarryProcedureTests.tearDown
    submit = fixtures.CarryProcedureTests.submit
    settle = fixtures.CarryProcedureTests.settle
    table = fixtures.DoorChoiceTests.table
    CARRY = fixtures.DoorChoiceTests.CARRY
    DRAFT, ATTACH = fixtures.CarryProcedureTests.DRAFT, fixtures.CarryProcedureTests.ATTACH

    def setUp(self):
        self.setUpBase()
        path = self.root / "backends.toml"
        text = path.read_text()
        deep = text[text.index('[[backend]]\nname = "deep-b"'):]
        # Independent recipes; no evidence can move across their names or hashes.
        text = text.replace("[backend.settings]", '[backend.settings]\nengine = "vllm"')
        deep = deep.replace("[backend.settings]", '[backend.settings]\nengine = "vllm"')
        path.write_text(text + deep.replace('deep-b', 'deep-c').replace('65536', '49152').replace('9001', '9002'))
        (self.root / "routes.toml").write_text('[lane.deep]\nbackends = ["deep-b", "deep-c"]\n')
        self.qualify()
        self.hold = threading.Event()

    def qualify(self, names=("deep-b", "deep-c")):
        backends = {}
        for name in names:
            _, sha = LocalWorkService._serving_profile(load_pool().by_name(name).settings)
            backends[name] = {"profiles": {sha: {"all": {"carry": self.CARRY}, "families": {}}}}
        return self.table(backends)

    def test_deep_only_auto_uses_each_candidates_actual_chat_tokenizer(self):
        seen = []
        def count(provider, model, prompt):
            seen.append((provider.name, prompt))
            return 50000 if provider.name == "deep-b" else 100
        self.service.token_counter = count
        manifest = self.submit(lane="auto", procedure=None)
        self.assertEqual(manifest["route"]["provider"], "deep-c")
        self.assertEqual([n for n, _ in seen], ["deep-b", "deep-c"])
        self.assertEqual(seen[0][1][0]["role"], "system")
        self.assertIn("exact context refusal", manifest["route"]["backend_choice"]["excluded"]["deep-b"])
        self.assertEqual(manifest["route"]["procedure"], "carry")

    def test_simultaneous_submissions_distribute_before_execution_starts(self):
        other = LocalWorkService(self.execution, root=self.root, token_counter=lambda *_: 100)
        original = self.service
        # Different coordinators share only the admission database, not Python locks.
        args = dict(intent="Report on a.py", acceptance_criteria=["renders"], repo=str(self.repo),
                    base_commit=self.base, files=["a.py"], artifact_kind="markdown", target_path=None,
                    lane="deep", task_family=None, deadline_s=30, max_tokens=None, receipt_id=None,
                    idempotency_key=None, caller_id="caller-a", procedure="carry")
        fixture = Path(__file__).resolve().parents[1] / 'delivery/fixtures/a59bad05.brief.json'
        args["brief"] = json.loads(fixture.read_text())
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(service.submit, **args) for service in (original, other)]
            manifests = [future.result() for future in futures]
        self.assertEqual({m["route"]["provider"] for m in manifests}, {"deep-b", "deep-c"})
        with self.assertRaisesRegex(LocalWorkError, "capacity exhausted"):
            self.submit()

    def test_old_recipe_or_other_backend_evidence_never_qualifies(self):
        self.table({"deep-b": {"all": {"carry": self.CARRY}, "profiles": {"old": {"all": {"carry": self.CARRY}}}}})
        with self.assertRaisesRegex(LocalWorkError, "no qualified delivery procedure"):
            self.submit()
        self.qualify(("deep-c",))
        manifest = self.submit()
        self.assertEqual(manifest["route"]["provider"], "deep-c")

    def test_retry_keeps_backend_procedure_and_table_digest_after_route_changes(self):
        first = self.submit(procedure=None, idempotency_key="stable")
        self.table({})
        (self.root / "routes.toml").write_text('[lane.deep]\nbackends = ["deep-c", "deep-b"]\n')
        retry = self.submit(procedure=None, idempotency_key="stable")
        self.assertEqual(first["route"], retry["route"])
        self.assertEqual(first["job_id"], retry["job_id"])
        with self.assertRaisesRegex(LocalWorkError, "recorded procedure"):
            self.submit(procedure="one_call", idempotency_key="stable")

    def test_retry_refuses_removed_provider_and_changed_recipe(self):
        self.submit(idempotency_key="stable")
        path = self.root / "backends.toml"
        original = path.read_text()
        path.write_text(original.replace('name = "deep-b"', 'name = "removed"'))
        with self.assertRaisesRegex(LocalWorkError, "unavailable: deep-b"):
            self.submit(idempotency_key="stable")
        path.write_text(original.replace('context_tokens = 65536', 'context_tokens = 60000'))
        with self.assertRaisesRegex(LocalWorkError, "serving profile changed"):
            self.submit(idempotency_key="stable")

    def test_execution_leases_break_an_assignment_tie(self):
        # A provider may have multiple slots; an occupied slot must still lose the tie.
        path = self.root / "backends.toml"
        path.write_text(path.read_text().replace('parallel_slots = 1', 'parallel_slots = 2'))
        self.qualify()
        lease = self.execution.leases.acquire(scope="provider:deep-b", job_id="raw-job", invocation_id="raw", limit=2, ttl_seconds=60)
        try:
            manifest = self.submit()
            self.assertEqual(manifest["route"]["provider"], "deep-c")
        finally:
            self.execution.leases.release(lease)

    def test_qualified_carry_cannot_silently_become_one_call(self):
        path = self.root / "backends.toml"
        path.write_text(path.read_text().replace('deliberate_max_tokens = 24576', 'deliberate_max_tokens = 4096'))
        self.qualify()
        with self.assertRaisesRegex(LocalWorkError, "qualified procedure is inadmissible"):
            self.submit(procedure=None)

    def test_unreachable_candidate_is_reported_before_next_qualified_candidate(self):
        def count(provider, *_):
            if provider.name == "deep-b":
                raise LocalWorkError("exact tokenizer endpoint refused: offline")
            return 100
        self.service.token_counter = count
        manifest = self.submit()
        self.assertEqual(manifest["route"]["provider"], "deep-c")
        self.assertIn("offline", manifest["route"]["backend_choice"]["excluded"]["deep-b"])

    def test_explicit_fast_and_items_refuse_deep_only_configuration(self):
        with self.assertRaisesRegex(LocalWorkError, "not in this host"):
            self.submit(lane="fast", procedure="one_call")
        with self.assertRaisesRegex(LocalWorkError, "needs a 'fast' lane"):
            self.service._items_roster({"deep": "deep-b"})

    def test_legacy_single_backend_allows_qualification_lap(self):
        (self.root / "routes.toml").write_text('[lane.deep]\nbackend = "deep-c"\n')
        self.table({})
        self.assertEqual(self.submit()["route"]["provider"], "deep-c")

    def test_non_delivery_code_is_not_qualified_by_report_verdicts(self):
        with self.assertRaisesRegex(LocalWorkError, "no code-qualification source"):
            self.submit(brief=None, procedure=None, artifact_kind="unified_diff", task_family="code_fix")

    def test_pool_requires_minimum_declared_serving_facts(self):
        path = self.root / "backends.toml"
        path.write_text(path.read_text().replace('engine = "vllm"\n', ''))
        self.qualify()
        with self.assertRaisesRegex(LocalWorkError, "minimum declared serving profile"):
            self.submit()

    def test_qualified_one_call_fallback_is_recorded(self):
        path = self.root / "backends.toml"
        path.write_text(path.read_text().replace('deliberate_max_tokens = 24576', 'deliberate_max_tokens = 4096'))
        backends = {}
        for name in ("deep-b", "deep-c"):
            profile, sha = LocalWorkService._serving_profile(load_pool().by_name(name).settings)
            self.assertEqual(profile["serving_profile_schema"], "serving-profile.v2")
            backends[name] = {"profiles": {sha: {"all": {"carry": self.CARRY,
                "one_call": {"accepted": 2, "rejected": 2, "briefs": 2, "accepted_briefs": 2}}}}}
        self.table(backends)
        manifest = self.submit(procedure=None)
        self.assertEqual(manifest["route"]["procedure"], "one_call")
        self.assertIn("deliberate_max_tokens", manifest["route"]["procedure_choice"]["fallback"])

    def test_tokenizer_wait_does_not_hold_reconciliation_lock(self):
        first = self.submit()
        entered, release = threading.Event(), threading.Event()
        def count(*_):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test admission was not released")
            return 100
        self.service.token_counter = count
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.submit)
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.service._lock.acquire(timeout=.2), "admission blocked reconciliation")
                try:
                    self.assertEqual(self.service.get(first["work_id"])["work_id"], first["work_id"])
                finally:
                    self.service._lock.release()
            finally:
                release.set()
            pending.result()

    def test_sqlite_lock_timeout_is_a_local_work_refusal(self):
        connection = mock.Mock()
        connection.execute.side_effect = sqlite3.OperationalError("database is locked")
        with mock.patch("hearth.localwork.service.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(LocalWorkError, "admission lock unavailable"):
                self.submit()
        connection.close.assert_called_once()

    def test_finished_legacy_retry_needs_no_current_route_or_tokenizer(self):
        first = self.submit(idempotency_key="finished")
        self.hold.set()
        completed = self.settle(first["work_id"])
        self.assertEqual(completed["status"], "awaiting_review")
        # Simulate a historical, unversioned manifest with a server-reported model alias.
        completed["route"].pop("selected_model")
        completed["route"].pop("serving_profile_schema")
        completed["route"]["model"] = "historical-server-alias"
        completed["route"]["serving_profile_sha256"] = "old-unversioned-hash"
        self.service._write(completed)
        (self.root / "backends.toml").unlink()
        (self.root / "routes.toml").unlink()
        self.service.token_counter = mock.Mock(side_effect=AssertionError("terminal retry tokenized"))
        self.assertEqual(self.submit(idempotency_key="finished"), completed)
        with self.assertRaisesRegex(LocalWorkError, "different local work"):
            self.submit(idempotency_key="finished", intent="changed intent")

    def test_ambiguous_empty_duplicate_and_wrong_type_routes_refuse(self):
        bad = ('backend="deep-b"\nbackends=["deep-c"]', 'backends=[]',
               'backends=["deep-b", "deep-b"]', 'backends="deep-b"', 'backend=12')
        for declaration in bad:
            (self.root / "routes.toml").write_text('[lane.deep]\n' + declaration + '\n')
            with self.assertRaises(LocalWorkError, msg=declaration):
                self.submit()


if __name__ == "__main__":
    unittest.main()
