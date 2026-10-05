"""Qualified heterogeneous lane admission, independent of deployed hosts or inference."""
from __future__ import annotations

import json
import threading
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

    def test_ambiguous_empty_duplicate_and_wrong_type_routes_refuse(self):
        bad = ('backend="deep-b"\nbackends=["deep-c"]', 'backends=[]',
               'backends=["deep-b", "deep-b"]', 'backends="deep-b"', 'backend=12')
        for declaration in bad:
            (self.root / "routes.toml").write_text('[lane.deep]\n' + declaration + '\n')
            with self.assertRaises(LocalWorkError, msg=declaration):
                self.submit()


if __name__ == "__main__":
    unittest.main()
