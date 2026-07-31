from __future__ import annotations

import unittest

from hearth.execution import (
    ExecutionEventError,
    new_execution_event,
    new_invocation_id,
    new_job_id,
    new_request_id,
    validate_execution_event,
)


class ExecutionEventModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.request_id = new_request_id()
        self.job_id = new_job_id()

    def test_builds_valid_unsequenced_request_event(self) -> None:
        event = new_execution_event(
            "request.accepted",
            request_id=self.request_id,
            job_id=self.job_id,
            principal={"type": "irc_account", "id": "derek", "authenticated": True},
            source={"transport": "irc", "adapter": "BotHerder"},
            operation="llm.chat",
            desired={"arguments": {"prompt": "hello"}},
        )
        self.assertIsNone(event["sequence"])
        validate_execution_event(event, allow_unsequenced=True)

    def test_invocation_requires_invocation_id(self) -> None:
        with self.assertRaisesRegex(ExecutionEventError, "require invocation_id"):
            new_execution_event(
                "invocation.started",
                request_id=self.request_id,
                job_id=self.job_id,
            )

    def test_rejects_unknown_fields(self) -> None:
        event = new_execution_event(
            "request.accepted",
            request_id=self.request_id,
            job_id=self.job_id,
        )
        event["surprise"] = True
        with self.assertRaisesRegex(ExecutionEventError, "extra"):
            validate_execution_event(event, allow_unsequenced=True)

    def test_invocation_event_accepts_opaque_id(self) -> None:
        event = new_execution_event(
            "invocation.started",
            request_id=self.request_id,
            job_id=self.job_id,
            invocation_id=new_invocation_id(),
        )
        validate_execution_event(event, allow_unsequenced=True)


if __name__ == "__main__":
    unittest.main()
