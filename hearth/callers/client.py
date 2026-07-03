"""HearthClient: thin MCP client for the HEARTH gateway.

Wraps mcp.client.streamable_http.streamablehttp_client + ClientSession.
Auth is the frozen contract: the ``X-Hearth-Key`` HTTP header. The installed
SDK (mcp 1.28.1) accepts a ``headers`` dict on ``streamablehttp_client``,
which it feeds to the httpx client factory so the header rides every request.

Requires the mcp SDK (venv-omen interpreter). Keep imports of this module
lazy in mcp-free code paths (see local_caller.py).
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_ENDPOINT = "http://127.0.0.1:8710/mcp"


def _result_to_dict(result: Any) -> dict:
    """Flatten a CallToolResult into a plain JSON-serializable dict."""
    text_parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    return {
        "ok": not bool(getattr(result, "isError", False)),
        "text": "\n".join(text_parts),
        "structured": getattr(result, "structuredContent", None),
    }


def _tool_to_dict(tool: Any) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


class HearthClient:
    """Caller-side handle on the HEARTH gateway.

    Usage (async, one session reused):
        async with HearthClient(key="dev-local") as client:
            tools = await client.list_tools()
            result = await client.call("record_event", event={...})

    Usage (sync convenience, one session per call):
        client = HearthClient(key="dev-local")
        tools = client.list_tools_sync()
        result = client.call_sync("record_event", event={...})
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        key: str = "dev-local",
        task_id: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.key = key
        self.task_id = task_id
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    def _headers(self) -> dict[str, str]:
        return {"X-Hearth-Key": self.key}

    def _meta(self) -> dict[str, Any] | None:
        if self.task_id is None:
            return None
        return {"task_id": self.task_id}

    async def __aenter__(self) -> "HearthClient":
        self._stack = AsyncExitStack()
        read, write, _get_session_id = await self._stack.enter_async_context(
            streamablehttp_client(self.endpoint, headers=self._headers())
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def list_tools(self) -> list[dict]:
        if self._session is not None:
            result = await self._session.list_tools()
            return [_tool_to_dict(tool) for tool in result.tools]
        async with HearthClient(self.endpoint, self.key, self.task_id) as client:
            return await client.list_tools()

    async def call(self, tool: str, **args: Any) -> dict:
        if self._session is not None:
            result = await self._session.call_tool(tool, arguments=args, meta=self._meta())
            return _result_to_dict(result)
        async with HearthClient(self.endpoint, self.key, self.task_id) as client:
            return await client.call(tool, **args)

    def list_tools_sync(self) -> list[dict]:
        return asyncio.run(self.list_tools())

    def call_sync(self, tool: str, **args: Any) -> dict:
        return asyncio.run(self.call(tool, **args))
