from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.service import ExecutionService
from hearth.localwork.service import LocalWorkError, LocalWorkService


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


class LocalWorkServiceTests(unittest.TestCase):
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
        self.outputs: list[str] = [json.dumps({
            "schema": "local-work-candidate.v1",
            "artifact_kind": "unified_diff",
            "summary": "Change the second line.",
            "target_path": None,
            "citations": [{"path": "a.py", "start_line": 1, "end_line": 2}],
            "content": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
        })]

        def generate(**_kwargs):
            text = self.outputs.pop(0)
            return {"ok": True, "text": text, "backend": "omen-arc",
                    "model": "qwen3-30b-a3b", "routed_by": "pinned:omen-arc",
                    "tokens_in": 100, "tokens_out": 50, "duration_ms": 5}

        state = self.root / "execution"
        self.execution = ExecutionService(
            ledger=ExecutionLedger(state), artifacts=ArtifactStore(state / "artifacts"),
            leases=CapacityLeaseStore(state / "leases.sqlite"), generate=generate,
            workers=1, recover_pending=False)
        self.env = mock.patch.dict(os.environ, {"HEARTH_OPERATOR_HOME": str(self.root)})
        self.env.start()
        self.service = LocalWorkService(self.execution, root=self.root,
                                        token_counter=lambda _p, _m, _q: 100)

    def tearDown(self) -> None:
        self.execution.close()
        self.env.stop()
        self.temp.cleanup()

    def submit(self, **overrides):
        args = dict(intent="Fix a.py", acceptance_criteria=["diff applies"],
                    repo=str(self.repo), base_commit=self.base, files=["a.py"],
                    artifact_kind="unified_diff", target_path=None, lane="fast",
                    task_family=None, deadline_s=30, max_tokens=128,
                    receipt_id="br-test", idempotency_key=None, caller_id="caller-a")
        args.update(overrides)
        return self.service.submit(**args)

    def settle(self, work_id: str, wanted: str = "awaiting_review") -> dict:
        for _ in range(100):
            current = self.service.get(work_id)
            if current["status"] == wanted or current["status"] == "failed":
                return current
            time.sleep(.01)
        self.fail("local work did not settle")

    def test_candidate_waits_for_explicit_evidenced_verdict(self) -> None:
        manifest = self.submit()
        final = self.settle(manifest["work_id"])
        self.assertEqual(final["status"], "awaiting_review")
        fetched = self.service.artifact(final["work_id"])
        self.assertEqual(fetched["artifact"]["sha256"], final["artifact"]["sha256"])
        with self.assertRaisesRegex(LocalWorkError, "passed evidenced"):
            self.service.verdict(final["work_id"], decision="accepted", criteria=[],
                                 summary="reviewed", evidence=["pytest"], receipt_id=None,
                                 caller_id="frontier")
        accepted = self.service.verdict(
            final["work_id"], decision="accepted",
            criteria=[{"criterion": "diff applies", "status": "passed", "evidence": "git apply --check"}],
            summary="Independently checked", evidence=["focused tests passed"],
            receipt_id=None, caller_id="frontier")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["caller"]["validated_by"], "frontier")

    def test_only_one_structural_repair_is_attempted(self) -> None:
        self.outputs[:] = ["not json", "still not json"]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertEqual(final["status"], "failed")
        self.assertEqual(len(final["attempts"]), 2)
        self.assertTrue(final["attempts"][1]["repair"])

    def test_semantically_invalid_candidate_fails_without_retry(self) -> None:
        bad = json.loads(self.outputs[0])
        bad["citations"][0]["end_line"] = 99
        self.outputs[:] = [json.dumps(bad)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertEqual(len(final["attempts"]), 1)
        self.assertIn("citation exceeds", final["failure"])

    def test_pinned_commit_ignores_mutable_worktree(self) -> None:
        (self.repo / "a.py").write_text("mutated\n", encoding="utf-8")
        manifest = self.submit()
        self.assertEqual(manifest["base_commit"], self.base)
        self.assertEqual(manifest["source"]["files"][0]["lines"], 2)

    def test_context_and_vision_refuse_before_job(self) -> None:
        refusing = LocalWorkService(self.execution, root=self.root,
                                     token_counter=lambda _p, _m, _q: 1_000_000)
        with self.assertRaisesRegex(LocalWorkError, "exact context refusal"):
            refusing.submit(intent="x", acceptance_criteria=["x"], repo=str(self.repo),
                             base_commit=self.base, files=["a.py"], artifact_kind="markdown",
                             target_path=None, lane="fast", task_family=None, deadline_s=30,
                             max_tokens=128, receipt_id=None, idempotency_key=None, caller_id="c")
        with self.assertRaisesRegex(LocalWorkError, "vision"):
            self.submit(task_family="vision")

    def test_diff_may_not_touch_undeclared_path(self) -> None:
        bad = json.loads(self.outputs[0])
        bad["content"] = bad["content"].replace("a.py", "secret.py")
        self.outputs[:] = [json.dumps(bad)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertIn("undeclared", final["failure"])

    def test_idempotency_returns_same_work_and_rejects_reuse(self) -> None:
        first = self.submit(idempotency_key="same")
        second = self.submit(idempotency_key="same")
        self.assertEqual(first["work_id"], second["work_id"])
        with self.assertRaisesRegex(LocalWorkError, "different local work"):
            self.submit(idempotency_key="same", intent="different")


if __name__ == "__main__":
    unittest.main()
