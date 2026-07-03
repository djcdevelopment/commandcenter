"""INTEGRATION: real gateway subprocess + authenticated MCP call over
streamable-http. Skips itself if the server cannot bind/start on this box."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STARTUP_TIMEOUT_S = 30


def _client(url: str, key: str):
    """streamable_http_client wired with an X-Hearth-Key header (mcp 1.28: headers
    travel on a caller-provided httpx.AsyncClient)."""
    import httpx
    from mcp.client.streamable_http import streamable_http_client

    http = httpx.AsyncClient(headers={"X-Hearth-Key": key},
                             timeout=httpx.Timeout(30, read=60))
    return streamable_http_client(url, http_client=http)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


class GatewayHttpIntegrationTest(unittest.TestCase):
    """End to end: subprocess gateway on a random port, dev-local key, one tool call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = _free_port()
        self.log_path = Path(self.tmp.name) / "gateway.log"
        env = {**os.environ, "HEARTH_ROOT": self.tmp.name}
        with self.log_path.open("wb") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "hearth.kernel.gateway", "--port", str(self.port)],
                cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        self.addCleanup(self._stop)
        if not _wait_for_port(self.port, self.proc, STARTUP_TIMEOUT_S):
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-500:]
            self.skipTest(f"gateway failed to bind 127.0.0.1:{self.port}: {detail}")

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def test_authenticated_call_lands_in_ledger_with_provenance(self):
        from mcp import ClientSession

        url = f"http://127.0.0.1:{self.port}/mcp"

        async def call() -> tuple:
            async with _client(url, "dev-local") as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool("kernel_status", {})
                    return tools, result

        tools, result = asyncio.run(call())
        tool_names = {tool.name for tool in tools.tools}
        self.assertIn("kernel_status", tool_names)
        self.assertIn("kernel_change", tool_names)
        self.assertFalse(result.isError, result.content)

        events_path = Path(self.tmp.name) / "var" / "ledger" / "events.ndjson"
        self.assertTrue(events_path.is_file(), "ledger not written under HEARTH_ROOT")
        events = [json.loads(line) for line in
                  events_path.read_text(encoding="utf-8").splitlines()]
        status_events = [e for e in events if e["tool"] == "kernel_status"]
        self.assertEqual(len(status_events), 1)
        event = status_events[0]
        self.assertEqual(event["caller"],
                         {"id": "dev-local", "runner_class": "human", "node": "omen"})
        self.assertTrue(event["ok"])
        self.assertTrue(event["args_digest"].startswith("sha256:"))
        self.assertTrue(event["result_digest"].startswith("sha256:"))

    def test_unknown_key_rejected_over_http_and_logged(self):
        from mcp import ClientSession

        url = f"http://127.0.0.1:{self.port}/mcp"

        async def call():
            async with _client(url, "wrong") as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("kernel_status", {})

        result = asyncio.run(call())
        self.assertTrue(result.isError)
        events_path = Path(self.tmp.name) / "var" / "ledger" / "events.ndjson"
        events = [json.loads(line) for line in
                  events_path.read_text(encoding="utf-8").splitlines()]
        rejections = [e for e in events if e["tool"] == "__auth__" and not e["ok"]]
        self.assertGreaterEqual(len(rejections), 1)
        self.assertNotIn("wrong", json.dumps(rejections))


if __name__ == "__main__":
    unittest.main()
