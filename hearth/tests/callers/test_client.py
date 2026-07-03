"""HearthClient tests. Require the mcp SDK (run with the venv-omen
interpreter); skipped cleanly on system python."""

from __future__ import annotations

import inspect
import unittest
from unittest import TestCase

try:
    import mcp  # noqa: F401
except ImportError:  # pragma: no cover - system python has no mcp
    raise unittest.SkipTest("mcp SDK not installed; run with venv-omen python")

from mcp.client.streamable_http import streamablehttp_client

from hearth.callers.client import DEFAULT_ENDPOINT, HearthClient, _result_to_dict, _tool_to_dict


class HeaderContractTests(TestCase):
    def test_sdk_transport_accepts_headers(self) -> None:
        # The frozen auth contract rides the headers kwarg of the SDK client.
        self.assertIn("headers", inspect.signature(streamablehttp_client).parameters)

    def test_client_builds_hearth_key_header(self) -> None:
        client = HearthClient(key="dev-local")
        self.assertEqual(client._headers(), {"X-Hearth-Key": "dev-local"})

    def test_defaults_and_task_meta(self) -> None:
        client = HearthClient()
        self.assertEqual(client.endpoint, DEFAULT_ENDPOINT)
        self.assertIsNone(client._meta())
        tasked = HearthClient(task_id="task-7")
        self.assertEqual(tasked._meta(), {"task_id": "task-7"})


class ResultFlatteningTests(TestCase):
    def test_call_tool_result_flattens_to_dict(self) -> None:
        from mcp.types import CallToolResult, TextContent

        result = CallToolResult(
            content=[TextContent(type="text", text="hello")],
            structuredContent={"answer": 42},
            isError=False,
        )
        self.assertEqual(
            _result_to_dict(result),
            {"ok": True, "text": "hello", "structured": {"answer": 42}},
        )

    def test_error_result_is_not_ok(self) -> None:
        from mcp.types import CallToolResult, TextContent

        result = CallToolResult(content=[TextContent(type="text", text="boom")], isError=True)
        self.assertFalse(_result_to_dict(result)["ok"])

    def test_tool_flattens_to_dict(self) -> None:
        from mcp.types import Tool

        tool = Tool(name="fs_read", description="Read a file.", inputSchema={"type": "object"})
        self.assertEqual(
            _tool_to_dict(tool),
            {"name": "fs_read", "description": "Read a file.", "input_schema": {"type": "object"}},
        )
