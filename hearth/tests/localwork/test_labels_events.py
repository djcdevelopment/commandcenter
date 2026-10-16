"""Labels on the door (ADR-0071, labels contract section 2): a `step.dispatched` carries the run's conditions, a
candidate read appends one `artifact.fetched` per caller, a rejection names its bucket, and an unattended caller
(ADR-0069) records a rejection only and never on its own candidate."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.service import ExecutionService
from hearth.localwork.service import LocalWorkError, LocalWorkService, UNATTENDED_CALLERS
from hearth.observation.identity import DispatchIdentity, dispatch_identity
from hearth.operator import history
from hearth.tests.localwork.test_service import git, join_waiters
from hearth.toolsurface import local_work as surface

CRITERIA = [{"criterion": "diff applies", "status": "passed", "evidence": "git apply --check"}]
DISPATCH_KEYS = ("task_family", "lane", "selected_lane", "serving_profile_sha256", "route_profile_sha256",
                 "procedure", "lab_configuration", "temperature")
LABEL_KEYS = ("bucket", "attended", "task_id", "validator")


class LabelsEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "a.py").write_text("one\ntwo\n", encoding="utf-8")
        git(self.repo, "add", "a.py")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        candidate = json.dumps({
            "schema": "local-work-candidate.v1", "artifact_kind": "unified_diff",
            "summary": "Change the second line.", "target_path": None,
            "citations": [{"path": "a.py", "start_line": 1, "end_line": 2}],
            "content": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
        })
        self.candidate = candidate
        self.outputs: list[str] = []   # answers to give before falling back to the valid candidate

        def generate(**kwargs):
            text = self.outputs.pop(0) if self.outputs else self.candidate
            result = {"ok": True, "text": text, "backend": "omen-arc", "model": "qwen3-30b-a3b",
                      "routed_by": "pinned:omen-arc", "tokens_in": 100, "tokens_out": 50, "duration_ms": 5}
            if kwargs.get("temperature") is not None:
                result["temperature"] = kwargs["temperature"]
            return result

        state = self.root / "execution"
        self.execution = ExecutionService(
            ledger=ExecutionLedger(state), artifacts=ArtifactStore(state / "artifacts"),
            leases=CapacityLeaseStore(state / "leases.sqlite"), generate=generate,
            workers=1, recover_pending=False)
        self.env = mock.patch.dict(os.environ, {"HEARTH_OPERATOR_HOME": str(self.root),
                                                "HEARTH_LAB_CONFIGURATION": "three-dense"})
        self.env.start()
        self.service = LocalWorkService(self.execution, root=self.root,
                                        token_counter=lambda _p, _m, _q: 100)

    def tearDown(self) -> None:
        self.execution.close()
        join_waiters()
        self.env.stop()
        self.temp.cleanup()

    def submit(self, **overrides) -> dict:
        args = dict(intent="Fix a.py", acceptance_criteria=["diff applies"], repo=str(self.repo),
                    base_commit=self.base, files=["a.py"], artifact_kind="unified_diff", target_path=None,
                    lane="fast", task_family=None, deadline_s=30, max_tokens=128, receipt_id="br-test",
                    idempotency_key=None, caller_id="caller-a")
        args.update(overrides)
        return self.service.submit(**args)

    def settle(self, work_id: str, wanted: str = "awaiting_review") -> dict:
        for _ in range(200):
            current = self.service.get(work_id)
            if current["status"] in {wanted, "failed"}:
                return current
            time.sleep(.01)
        self.fail("local work did not settle")

    def awaiting(self, **overrides) -> str:
        # Each call is its own submission: an identical submit returns the same work (idempotency), so key them apart.
        self.n = getattr(self, "n", 0) + 1
        overrides.setdefault("idempotency_key", f"key-{self.n}")
        return self.settle(self.submit(**overrides)["work_id"])["work_id"]

    def events(self, work_id: str, event_type: str) -> list[dict]:
        return [e for e in history.read_run_history(work_id) if e["event_type"] == event_type]

    def verdict_events(self, work_id: str) -> dict[str, dict]:
        """The verdict's two events. The door also writes a `verification.recorded` when it validates the candidate
        (`passed: true`, no `decision`); the verdict's row is the one that names the decision."""
        recorded = [e for e in self.events(work_id, "verification.recorded") if "decision" in e["payload"]]
        final = self.events(work_id, "outcome.final")
        self.assertEqual((len(recorded), len(final)), (1, 1))
        return {"verification.recorded": recorded[0], "outcome.final": final[0]}

    def verdict(self, work_id: str, **overrides) -> dict:
        kw = dict(decision="rejected", criteria=CRITERIA, summary="reviewed", evidence=["pytest"],
                  receipt_id=None, caller_id="frontier", bucket="setup")
        kw.update(overrides)
        return self.service.verdict(work_id, **kw)

    # -- verdict: bucket on a rejection (ADR-0071) ------------------------------------------------------------------

    def test_a_rejection_without_a_bucket_is_refused_and_writes_nothing(self) -> None:
        work_id = self.awaiting()
        before = len(history.read_run_history(work_id))
        with self.assertRaisesRegex(LocalWorkError, r"a rejection names its bucket: form, setup or substance \(ADR-0071\)"):
            self.verdict(work_id, bucket=None)
        self.assertEqual(self.service.get(work_id)["status"], "awaiting_review")
        self.assertEqual(len(history.read_run_history(work_id)), before)

    def test_a_rejection_with_a_bucket_labels_both_events(self) -> None:
        work_id = self.awaiting()
        done = self.verdict(work_id, bucket="substance", task_id="cc-test1234")
        self.assertEqual(done["status"], "rejected")
        self.assertEqual({k: done["verdict"][k] for k in ("bucket", "attended", "task_id")},
                         {"bucket": "substance", "attended": True, "task_id": "cc-test1234"})
        expected = {"bucket": "substance", "attended": True, "task_id": "cc-test1234", "validator": "frontier"}
        rows = self.verdict_events(work_id)
        for event_type, row in rows.items():
            self.assertEqual({k: row["payload"][k] for k in LABEL_KEYS}, expected, event_type)
        self.assertEqual(rows["verification.recorded"]["payload"]["decision"], "rejected")
        self.assertEqual(rows["outcome.final"]["payload"]["status"], "rejected")

    def test_an_acceptance_keeps_the_bucket_optional_and_still_labels_the_events(self) -> None:
        work_id = self.awaiting()
        done = self.verdict(work_id, decision="accepted", bucket=None)
        self.assertNotIn("bucket", done["verdict"])
        self.assertEqual(done["verdict"]["attended"], True)
        self.assertIsNone(done["verdict"]["task_id"])
        payload = self.events(work_id, "outcome.final")[0]["payload"]
        self.assertEqual({k: payload[k] for k in LABEL_KEYS},
                         {"bucket": None, "attended": True, "task_id": None, "validator": "frontier"})
        with self.assertRaisesRegex(LocalWorkError, "passed evidenced"):
            self.verdict(self.awaiting(), decision="accepted", criteria=[], bucket=None)

    # -- verdict: unattended callers (ADR-0069) ----------------------------------------------------------------------

    def test_the_unattended_class_table_names_codex(self) -> None:
        self.assertEqual(UNATTENDED_CALLERS, ("codex-cli",))

    def test_an_unattended_caller_may_not_accept(self) -> None:
        work_id = self.awaiting()
        with self.assertRaisesRegex(LocalWorkError, r"rejection only, never accepted \(ADR-0069\)"):
            self.verdict(work_id, decision="accepted", caller_id="codex-cli", bucket=None)
        with self.assertRaisesRegex(LocalWorkError, r"never superseded \(ADR-0069\)"):
            self.verdict(work_id, decision="superseded", caller_id="codex-cli")
        with self.assertRaisesRegex(LocalWorkError, "ADR-0069"):   # an attended class may declare itself unattended
            self.verdict(work_id, decision="accepted", caller_id="frontier", attended=False, bucket=None)
        self.assertEqual(self.service.get(work_id)["status"], "awaiting_review")

    def test_an_unattended_caller_never_judges_its_own_candidate(self) -> None:
        work_id = self.awaiting(caller_id="codex-cli")
        with self.assertRaisesRegex(LocalWorkError, r"never judges its own candidate \(ADR-0069\)"):
            self.verdict(work_id, caller_id="codex-cli")
        self.assertEqual(self.service.get(work_id)["status"], "awaiting_review")
        self.assertEqual(self.events(work_id, "outcome.final"), [])

    def test_an_unattended_rejection_of_another_callers_candidate_is_recorded_unattended(self) -> None:
        work_id = self.awaiting(caller_id="caller-a")
        done = self.verdict(work_id, caller_id="codex-cli", bucket="form", task_id="cx-night")
        self.assertEqual(done["status"], "rejected")
        self.assertEqual(done["verdict"]["attended"], False)
        self.assertEqual(done["caller"]["validated_by"], "codex-cli")
        for event_type, row in self.verdict_events(work_id).items():
            self.assertEqual({k: row["payload"][k] for k in LABEL_KEYS},
                             {"bucket": "form", "attended": False, "task_id": "cx-night", "validator": "codex-cli"},
                             event_type)

    def test_an_unattended_class_cannot_accept_by_declaring_itself_attended(self) -> None:
        # ADR-0071 amendment: the class decides; `attended=True` from codex-cli is recorded, never honoured.
        work_id = self.awaiting()
        with self.assertRaisesRegex(LocalWorkError, r"rejection only, never accepted \(ADR-0069\).*ADR-0071"):
            self.verdict(work_id, decision="accepted", caller_id="codex-cli", attended=True, bucket=None)
        self.assertEqual(self.service.get(work_id)["status"], "awaiting_review")   # the refusal wrote nothing

    def test_a_declared_attended_rejection_from_codex_is_recorded_unattended_with_the_attempt_visible(self) -> None:
        work_id = self.awaiting()
        done = self.verdict(work_id, caller_id="codex-cli", attended=True, bucket="form", task_id="cx-night")
        self.assertEqual((done["status"], done["verdict"]["attended"], done["verdict"]["attended_declared"]),
                         ("rejected", False, True))
        for event_type, row in self.verdict_events(work_id).items():
            self.assertEqual({k: row["payload"][k] for k in LABEL_KEYS + ("attended_declared",)},
                             {"bucket": "form", "attended": False, "task_id": "cx-night", "validator": "codex-cli",
                              "attended_declared": True}, event_type)
        # a plain unattended rejection carries no `attended_declared` at all: the key marks an attempt, not a default
        plain = self.verdict(self.awaiting(), caller_id="codex-cli", bucket="form")
        self.assertNotIn("attended_declared", plain["verdict"])
        self.assertNotIn("attended_declared", self.events(plain["work_id"], "outcome.final")[0]["payload"])

    def test_an_attended_caller_stating_attended_still_accepts(self) -> None:
        done = self.verdict(self.awaiting(), decision="accepted", caller_id="frontier", attended=True, bucket=None)
        self.assertEqual((done["status"], done["verdict"]["attended"]), ("accepted", True))
        self.assertNotIn("attended_declared", done["verdict"])
        with self.assertRaisesRegex(LocalWorkError, "attended must be true or false"):
            self.verdict(self.awaiting(), attended="yes")
        with self.assertRaisesRegex(LocalWorkError, "task_id must be a nonempty string"):
            self.verdict(self.awaiting(), task_id="")

    # -- artifact.fetched ------------------------------------------------------------------------------------------

    def test_a_fetch_appends_artifact_fetched_once_per_caller(self) -> None:
        work_id = self.awaiting()
        self.service.artifact(work_id, caller_id="reviewer-1")
        self.service.artifact(work_id, caller_id="reviewer-1")
        rows = self.events(work_id, "artifact.fetched")
        self.assertEqual([r["payload"] for r in rows], [{"by": "reviewer-1", "status": "awaiting_review"}])
        self.service.artifact(work_id, caller_id="reviewer-2")
        self.service.artifact(work_id, caller_id="reviewer-1")
        rows = self.events(work_id, "artifact.fetched")
        self.assertEqual([r["payload"]["by"] for r in rows], ["reviewer-1", "reviewer-2"])
        self.assertEqual(rows[0]["run_id"], work_id)
        self.assertEqual(rows[0]["envelope_id"], self.service.get(work_id)["envelope_id"])

    def test_a_fetch_without_a_caller_appends_nothing(self) -> None:
        work_id = self.awaiting()
        self.service.artifact(work_id)
        self.assertEqual(self.events(work_id, "artifact.fetched"), [])

    def test_a_fetch_leaves_the_manifest_bytes_identical(self) -> None:
        work_id = self.awaiting()
        path = self.service._manifest_path(work_id)
        before = path.read_bytes()
        stat = path.stat()
        self.service.artifact(work_id, caller_id="reviewer-1")
        self.service.artifact(work_id, caller_id="reviewer-2")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((path.stat().st_mtime_ns, path.stat().st_ino), (stat.st_mtime_ns, stat.st_ino))
        self.assertEqual(len(self.events(work_id, "artifact.fetched")), 2)

    def test_a_fetch_after_the_verdict_records_the_final_status(self) -> None:
        work_id = self.awaiting()
        self.verdict(work_id, bucket="setup")
        self.service.artifact(work_id, caller_id="auditor")
        self.assertEqual(self.events(work_id, "artifact.fetched")[0]["payload"], {"by": "auditor", "status": "rejected"})

    # -- step.dispatched conditions ------------------------------------------------------------------------------------

    def test_a_dispatched_run_carries_its_conditions_on_step_dispatched(self) -> None:
        work_id = self.awaiting(task_family="summarization", temperature=0.2)
        manifest = self.service.get(work_id)
        rows = self.events(work_id, "step.dispatched")
        self.assertEqual(len(rows), 1)
        payload = rows[0]["payload"]
        self.assertEqual({k: payload[k] for k in DISPATCH_KEYS}, {
            "task_family": "summarization", "lane": "fast", "selected_lane": "fast",
            "serving_profile_sha256": manifest["route"]["serving_profile_sha256"],
            "route_profile_sha256": manifest["route"]["profile_sha256"],
            # a fractional value enters an event payload as the house decimal string, never a float (canonical.py)
            "procedure": None, "lab_configuration": "three-dense", "temperature": "0.20"})
        self.assertEqual(manifest["conditions"]["temperature"], 0.2)
        self.assertRegex(payload["serving_profile_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(payload["route_profile_sha256"], r"^[0-9a-f]{64}$")
        # provider and model are what was routed at dispatch; reconciliation may later stamp the seat's reported alias
        self.assertEqual((payload["provider"], payload["attempt"]), (manifest["route"]["provider"], 1))
        self.assertIsInstance(payload["model"], str)

    def test_the_repair_dispatch_carries_the_same_conditions(self) -> None:
        self.outputs[:] = ["not json", "still not json"]
        work_id = self.submit(task_family="summarization")["work_id"]
        final = self.settle(work_id, wanted="failed")
        self.assertEqual(len(final["attempts"]), 2)
        rows = self.events(work_id, "step.dispatched")
        self.assertEqual([r["payload"]["attempt"] for r in rows], [1, 2])
        self.assertTrue(rows[1]["payload"]["structural_repair"])
        for row in rows:
            self.assertEqual({k: row["payload"][k] for k in ("task_family", "lane", "lab_configuration")},
                             {"task_family": "summarization", "lane": "fast", "lab_configuration": "three-dense"})
            self.assertEqual(row["payload"]["serving_profile_sha256"], final["route"]["serving_profile_sha256"])
            self.assertIsNone(row["payload"]["temperature"])

    # -- the tool surface passes the caller through ------------------------------------------------------------------

    def test_the_tool_surface_passes_the_caller_to_fetch_and_verdict(self) -> None:
        work_id = self.awaiting()
        reviewer = DispatchIdentity(caller_id="reviewer-x", runner_class="claude", node="test", profile="unrestricted")
        with mock.patch.object(surface, "_get_service", return_value=self.service), dispatch_identity(reviewer):
            surface.get_local_work_artifact(work_id)
            surface.get_local_work_artifact(work_id)
            self.assertEqual([r["payload"]["by"] for r in self.events(work_id, "artifact.fetched")], ["reviewer-x"])
            with self.assertRaisesRegex(LocalWorkError, "a rejection names its bucket"):
                surface.record_local_work_verdict(work_id, decision="rejected", criteria=CRITERIA, summary="r",
                                                  evidence=["e"])
            done = surface.record_local_work_verdict(work_id, decision="rejected", criteria=CRITERIA, summary="r",
                                                     evidence=["e"], bucket="form", attended=True, task_id="cc-x")
        self.assertEqual(done["verdict"]["caller_id"], "reviewer-x")
        payload = self.events(work_id, "outcome.final")[0]["payload"]
        self.assertEqual({k: payload[k] for k in LABEL_KEYS},
                         {"bucket": "form", "attended": True, "task_id": "cc-x", "validator": "reviewer-x"})


if __name__ == "__main__":
    unittest.main()
