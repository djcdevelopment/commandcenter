from __future__ import annotations

import json
from unittest import TestCase, mock

from hearth.callers import local_caller
from hearth.callers.local_caller import mcp_tools_to_ollama, run_task


class FakeHearthClient:
    """Mocked gateway client: records calls, returns canned results."""

    def __init__(self, tools: list[dict] | None = None, results: dict | None = None) -> None:
        self.tools = tools if tools is not None else [
            {
                "name": "record_event",
                "description": "Append a workflow event.",
                "input_schema": {"type": "object", "properties": {"event": {"type": "object"}}},
            }
        ]
        self.results = results or {}
        self.calls: list[tuple[str, dict]] = []

    def list_tools_sync(self) -> list[dict]:
        return self.tools

    def call_sync(self, tool: str, **args: object) -> dict:
        self.calls.append((tool, args))
        return self.results.get(tool, {"ok": True, "text": "done", "structured": None})


class FakeOllama:
    """Sequence of canned /api/chat responses; records request payloads."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float = 300.0) -> dict:
        self.requests.append(payload)
        return self.responses.pop(0)


def chat_response(content: str = "", tool_calls: list[dict] | None = None, tokens_in: int = 10, tokens_out: int = 5) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message, "prompt_eval_count": tokens_in, "eval_count": tokens_out, "done": True}


class ToolSchemaConversionTests(TestCase):
    def test_converts_mcp_tools_to_ollama_format(self) -> None:
        converted = mcp_tools_to_ollama(
            [{"name": "fs_read", "description": "Read a file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
        )
        self.assertEqual(
            converted,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "fs_read",
                        "description": "Read a file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        )

    def test_missing_schema_gets_empty_object_schema(self) -> None:
        converted = mcp_tools_to_ollama([{"name": "ping", "description": "", "input_schema": None}])
        self.assertEqual(converted[0]["function"]["parameters"], {"type": "object", "properties": {}})


class RunTaskTests(TestCase):
    def test_tool_call_loop_then_final_answer(self) -> None:
        client = FakeHearthClient()
        ollama = FakeOllama(
            [
                chat_response(tool_calls=[{"function": {"name": "record_event", "arguments": {"event": {"kind": "x"}}}}]),
                chat_response(content="All recorded.", tokens_in=20, tokens_out=8),
            ]
        )

        with mock.patch.object(local_caller, "_post_json", ollama):
            result = run_task("record something", client=client)

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_text"], "All recorded.")
        self.assertEqual(result["turns"], 2)
        self.assertEqual(result["tool_calls_made"], ["record_event"])
        self.assertEqual(result["tokens_in_total"], 30)
        self.assertEqual(result["tokens_out_total"], 13)
        self.assertEqual(client.calls, [("record_event", {"event": {"kind": "x"}})])
        # tool result was fed back to the model as a role=tool message
        tool_messages = [m for m in ollama.requests[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(json.loads(tool_messages[0]["content"])["ok"], True)
        # gateway tool list was converted into the request tools
        self.assertEqual(ollama.requests[0]["tools"][0]["function"]["name"], "record_event")
        self.assertFalse(ollama.requests[0]["stream"])

    def test_malformed_tool_args_reported_to_model_and_loop_continues(self) -> None:
        client = FakeHearthClient()
        ollama = FakeOllama(
            [
                chat_response(tool_calls=[{"function": {"name": "record_event", "arguments": "{not json"}}]),
                chat_response(content="Gave up on that call."),
            ]
        )

        with mock.patch.object(local_caller, "_post_json", ollama):
            result = run_task("record something", client=client)

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_calls_made"], [])  # nothing executed
        self.assertEqual(client.calls, [])
        tool_messages = [m for m in ollama.requests[1]["messages"] if m.get("role") == "tool"]
        error_payload = json.loads(tool_messages[0]["content"])
        self.assertFalse(error_payload["ok"])
        self.assertIn("JSON", error_payload["error"])

    def test_string_json_arguments_are_accepted(self) -> None:
        client = FakeHearthClient()
        ollama = FakeOllama(
            [
                chat_response(tool_calls=[{"function": {"name": "record_event", "arguments": '{"event": {"kind": "y"}}'}}]),
                chat_response(content="done"),
            ]
        )

        with mock.patch.object(local_caller, "_post_json", ollama):
            result = run_task("go", client=client)

        self.assertEqual(client.calls, [("record_event", {"event": {"kind": "y"}})])
        self.assertTrue(result["ok"])

    def test_ollama_down_returns_ok_false(self) -> None:
        client = FakeHearthClient()

        def boom(url: str, payload: dict, timeout: float = 300.0) -> dict:
            import urllib.error

            raise urllib.error.URLError("connection refused")

        with mock.patch.object(local_caller, "_post_json", boom):
            result = run_task("go", client=client)

        self.assertFalse(result["ok"])
        self.assertIn("ollama chat failed", result["error"])

    def test_gateway_down_returns_ok_false(self) -> None:
        class DeadClient:
            def list_tools_sync(self) -> list[dict]:
                raise ConnectionError("gateway offline")

        result = run_task("go", client=DeadClient())

        self.assertFalse(result["ok"])
        self.assertIn("gateway tool list failed", result["error"])

    def test_gateway_call_failure_fed_back_as_tool_error(self) -> None:
        class FlakyClient(FakeHearthClient):
            def call_sync(self, tool: str, **args: object) -> dict:
                self.calls.append((tool, args))
                raise RuntimeError("gateway 500")

        client = FlakyClient()
        ollama = FakeOllama(
            [
                chat_response(tool_calls=[{"function": {"name": "record_event", "arguments": {}}}]),
                chat_response(content="noted the failure"),
            ]
        )

        with mock.patch.object(local_caller, "_post_json", ollama):
            result = run_task("go", client=client)

        self.assertTrue(result["ok"])
        tool_messages = [m for m in ollama.requests[1]["messages"] if m.get("role") == "tool"]
        self.assertIn("gateway call failed", json.loads(tool_messages[0]["content"])["error"])

    def test_max_turns_exhaustion(self) -> None:
        client = FakeHearthClient()
        looping = chat_response(tool_calls=[{"function": {"name": "record_event", "arguments": {}}}])
        ollama = FakeOllama([looping, dict(looping)])

        with mock.patch.object(local_caller, "_post_json", ollama):
            result = run_task("go", client=client, max_turns=2)

        self.assertFalse(result["ok"])
        self.assertIn("max_turns", result["error"])
        self.assertEqual(result["turns"], 2)
