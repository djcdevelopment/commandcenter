from __future__ import annotations

import unittest
from unittest.mock import patch

from hearth.observation.identity import DispatchIdentity, dispatch_identity
from hearth.toolsurface import execution_control


class _FakeService:
    def __init__(self) -> None:
        self.submissions = []
        self.plans = []
        self.operations = type("Registry", (), {"operations": ()})()

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return {"job_id": "job_test", "principal": kwargs["principal"], "source": kwargs["source"]}

    def plan(self, **kwargs):
        self.plans.append(kwargs)
        return {"operation": kwargs["operation_name"], "dispatch": False}


class ExecutionControlToolsTest(unittest.TestCase):
    def test_direct_submission_uses_authenticated_gateway_caller(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity("claude", "frontier", "omen", profile="research")
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                result = execution_control.submit_execution(
                    "llm.chat", {"prompt": "hello"}
                )
        self.assertEqual(
            {"type": "hearth_caller", "id": "claude", "authenticated": True},
            result["principal"],
        )
        self.assertEqual(
            {"transport": "mcp", "adapter": "claude"}, result["source"]
        )

    def test_irc_adapter_can_delegate_account_but_not_adapter_name(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity(
            "botherder-am4", "service", "am4", profile="irc-adapter"
        )
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                result = execution_control.submit_delegated_execution(
                    "llm.chat",
                    {"prompt": "hello"},
                    "irc_account",
                    "derek",
                    "irc",
                )
        self.assertEqual(
            {"type": "irc_account", "id": "derek", "authenticated": True},
            result["principal"],
        )
        self.assertEqual(
            {"transport": "irc", "adapter": "botherder-am4"}, result["source"]
        )

    def test_non_adapter_profile_cannot_delegate(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity("researcher", "agent", "omen", profile="research")
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                with self.assertRaisesRegex(PermissionError, "may not delegate"):
                    execution_control.submit_delegated_execution(
                        "llm.chat",
                        {"prompt": "hello"},
                        "irc_account",
                        "derek",
                        "irc",
                    )
        self.assertEqual([], service.submissions)

    def test_plan_execution_forwards_task_family_to_the_service(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity("claude", "frontier", "omen", profile="research")
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                execution_control.plan_execution(
                    "llm.chat", prompt_bytes=1000, task_family="quote_retrieval")
        self.assertEqual("quote_retrieval", service.plans[0]["task_family"])

    def test_plan_execution_without_a_family_passes_none(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity("claude", "frontier", "omen", profile="research")
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                execution_control.plan_execution("llm.chat", prompt_bytes=1000)
        self.assertIsNone(service.plans[0]["task_family"])

    def test_no_gateway_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(PermissionError, "gateway caller identity"):
            execution_control.submit_execution("llm.chat", {"prompt": "hello"})


if __name__ == "__main__":
    unittest.main()
