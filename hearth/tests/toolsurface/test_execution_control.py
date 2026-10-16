from __future__ import annotations

import unittest
import tempfile
from types import SimpleNamespace

from hearth.execution import ExecutionLedger, new_execution_event, new_job_id, new_request_id
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
                    "inference.deliberate", prompt_bytes=1000, task_family="quote_retrieval",
                    thinking=False, procedure="one_call")
        self.assertEqual("quote_retrieval", service.plans[0]["task_family"])
        self.assertIs(service.plans[0]["thinking"], False)
        self.assertEqual("one_call", service.plans[0]["procedure"])

    def test_plan_execution_without_a_family_passes_none(self) -> None:
        service = _FakeService()
        identity = DispatchIdentity("claude", "frontier", "omen", profile="research")
        with patch.object(execution_control, "_get_service", return_value=service):
            with dispatch_identity(identity):
                execution_control.plan_execution("llm.chat", prompt_bytes=1000)
        self.assertIsNone(service.plans[0]["task_family"])

    def test_list_my_executions_filters_before_paging_and_stays_owned(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ledger = ExecutionLedger(root)
            jobs = []
            for caller, task, principal_type, adapter in (
                ("alice", "old", "hearth_caller", "alice"),
                ("alice", "wanted", "hearth_caller", "alice"),
                ("alice", "wanted", "hearth_caller", "alice"),
                ("bob", "wanted", "hearth_caller", "bob"),
                ("alice", "wanted", "service_account", "alice"),
            ):
                job_id = new_job_id()
                ledger.append(new_execution_event(
                    "request.accepted", request_id=new_request_id(), job_id=job_id,
                    principal={"type": principal_type, "id": caller, "authenticated": True},
                    source={"transport": "mcp", "adapter": adapter}, operation="llm.chat",
                    desired={"operation": "llm.chat", "arguments": {"task_id": task}},
                ))
                jobs.append(job_id)
            service = SimpleNamespace(ledger=ledger)
            # Any use of the old global scan is the observed newest-jobs defect.
            with patch.object(execution_control, "_get_service", return_value=service), patch.object(
                    ledger, "list_jobs", side_effect=AssertionError("global scan")):
                for profile in ("research", "unrestricted"):
                    with dispatch_identity(DispatchIdentity("alice", "agent", "omen", profile=profile)):
                        first = execution_control.list_my_executions(task_id="wanted", status="accepted", limit=1)
                        second = execution_control.list_my_executions(task_id="wanted", status="accepted", limit=1,
                            before_sequence=first["next_before_sequence"])
                        self.assertEqual([jobs[2]], [j["job_id"] for j in first["executions"]])
                        self.assertEqual([jobs[1]], [j["job_id"] for j in second["executions"]])
                        self.assertIsNone(second["next_before_sequence"])
                        self.assertIsNotNone(first["executions"][0]["submitted_at"])
                        with self.assertRaises(ValueError):
                            execution_control.list_my_executions(limit=101)
                with dispatch_identity(DispatchIdentity("alice", "service", "omen", profile="irc-adapter")):
                    owned = execution_control.list_owned_executions("service_account", "alice")
                    self.assertEqual([jobs[4]], [j["job_id"] for j in owned])
                with dispatch_identity(DispatchIdentity("bob", "service", "omen", profile="irc-adapter")):
                    self.assertEqual([], execution_control.list_owned_executions("service_account", "alice"))
            with self.assertRaises(PermissionError):
                execution_control.list_my_executions()

    def test_no_gateway_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(PermissionError, "gateway caller identity"):
            execution_control.submit_execution("llm.chat", {"prompt": "hello"})


if __name__ == "__main__":
    unittest.main()
