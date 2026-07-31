from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.execution import (
    ExecutionLedger,
    ExecutionLedgerError,
    new_artifact_id,
    new_execution_event,
    new_invocation_id,
    new_job_id,
    new_request_id,
)


class ExecutionLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = ExecutionLedger(self.root)
        self.request_id = new_request_id()
        self.job_id = new_job_id()
        self.principal = {"type": "irc_account", "id": "derek", "authenticated": True}
        self.source = {"transport": "irc", "adapter": "BotHerder"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def append(self, event_type: str, **kwargs: object) -> dict[str, object]:
        return self.ledger.append(
            new_execution_event(
                event_type,
                request_id=self.request_id,
                job_id=self.job_id,
                **kwargs,
            )
        )

    def accept(self, *, idempotency_key: str = "irc:msg-1") -> None:
        self.append(
            "request.accepted",
            principal=self.principal,
            source=self.source,
            operation="llm.chat",
            desired={
                "idempotency_key": idempotency_key,
                "arguments": {"prompt": "hello"},
            },
        )

    def test_projects_request_invocation_artifact_and_completion(self) -> None:
        self.accept()
        self.append("job.queued")
        invocation_id = new_invocation_id()
        self.append(
            "invocation.started",
            invocation_id=invocation_id,
            observed={"provider": "am4-moe", "model": "gpt-oss-120b"},
        )
        self.append(
            "invocation.succeeded",
            invocation_id=invocation_id,
            observed={"usage": {"output_tokens": 12}},
        )
        artifact_id = new_artifact_id()
        metadata = {
            "artifact_id": artifact_id,
            "media_type": "text/plain",
            "size": 5,
            "sha256": "a" * 64,
            "created_at": "2026-07-30T00:00:00Z",
        }
        self.append(
            "artifact.recorded",
            invocation_id=invocation_id,
            artifacts=[metadata],
        )
        self.append("job.succeeded", observed={"summary": "done"})

        state = self.ledger.get_job(self.job_id)
        assert state is not None
        self.assertEqual("succeeded", state["status"])
        self.assertEqual("succeeded", state["invocations"][0]["status"])
        self.assertEqual("am4-moe", state["invocations"][0]["provider"])
        self.assertEqual(artifact_id, state["artifacts"][0]["artifact_id"])
        self.assertEqual(metadata, self.ledger.get_artifact(artifact_id))
        self.assertEqual(self.job_id, self.ledger.find_by_idempotency("irc:msg-1")["job_id"])
        self.assertEqual(list(range(1, 7)), [e["sequence"] for e in self.ledger.iter_events()])

    def test_projection_rebuild_is_lossless(self) -> None:
        self.accept()
        self.append("job.queued")
        before = self.ledger.get_job(self.job_id)
        self.ledger.projection_path.unlink()
        rebuilt = ExecutionLedger(self.root)
        self.assertEqual(2, rebuilt.rebuild())
        self.assertEqual(before, rebuilt.get_job(self.job_id))

    def test_rejects_duplicate_idempotency_key_before_canonical_append(self) -> None:
        self.accept()
        other_request = new_request_id()
        other_job = new_job_id()
        with self.assertRaisesRegex(ExecutionLedgerError, "idempotency key already exists"):
            self.ledger.append(
                new_execution_event(
                    "request.accepted",
                    request_id=other_request,
                    job_id=other_job,
                    desired={"idempotency_key": "irc:msg-1"},
                )
            )
        events = list(self.ledger.iter_events())
        self.assertEqual(1, len(events))
        self.assertIsNone(self.ledger.get_job(other_job))

    def test_rejects_transition_after_terminal_state(self) -> None:
        self.accept()
        self.append("job.cancelled")
        with self.assertRaisesRegex(ExecutionLedgerError, "terminal job"):
            self.append("job.running")

    def test_rejects_invocation_completion_without_start(self) -> None:
        self.accept()
        with self.assertRaisesRegex(ExecutionLedgerError, "no matching invocation"):
            self.append(
                "invocation.failed",
                invocation_id=new_invocation_id(),
                reason="provider unavailable",
            )


if __name__ == "__main__":
    unittest.main()
