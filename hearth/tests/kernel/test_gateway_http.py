"""INTEGRATION: real gateway subprocess + authenticated MCP call over
streamable-http. Skips itself if the server cannot bind/start on this box.

The gateway binds an OS-assigned ephemeral port (``--port 0``) and announces the
actual bound port in its log; the test reads that number back rather than
pre-probing a port and assuming it stays free. Pre-probing (bind :0, read the
number, close, then hand it to the gateway) is a time-of-check/time-of-use race:
on a busy box another process can occupy that port in the sub-second gap before
the gateway binds it. A plain TCP connectivity probe cannot tell the two apart,
so the client gets misdirected to the foreign listener while the test reads its
own (empty) ledger root -- the intermittent failure this test used to exhibit.
Binding :0 in the gateway itself closes the window: the gateway owns the port
from the moment it binds, and the port the test learns is guaranteed to be its.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
STARTUP_TIMEOUT_S = 30
LEDGER_TIMEOUT_S = 5.0

# uvicorn announces the real bound port once the socket is bound and the app has
# finished starting: "Uvicorn running on http://127.0.0.1:54102 (Press ...)".
# With --port 0 that number is the OS-assigned ephemeral port -- the source of
# truth for where THIS gateway is listening.
_LISTEN_RE = re.compile(r"Uvicorn running on https?://[^\s:]+:(\d+)")


def _client(url: str, key: str):
    """streamable_http_client wired with an X-Hearth-Key header (mcp 1.28: headers
    travel on a caller-provided httpx.AsyncClient)."""
    import httpx
    from mcp.client.streamable_http import streamable_http_client

    http = httpx.AsyncClient(headers={"X-Hearth-Key": key},
                             timeout=httpx.Timeout(30, read=60))
    return streamable_http_client(url, http_client=http)


def _wait_for_bound_port(log_path: Path, proc: subprocess.Popen,
                         timeout: float) -> Optional[int]:
    """Return the port the gateway actually bound (read from its log), or None if
    it exits or fails to announce one within ``timeout``.

    Waiting for uvicorn's "Uvicorn running on ..." line -- emitted only after the
    socket is bound AND application startup is complete -- is strictly stronger
    than a TCP connectivity probe against an assumed port: the announced port is
    guaranteed to belong to THIS gateway, so a foreign listener that transiently
    grabbed some other ephemeral port can never be mistaken for it, and the
    server is provably ready to serve MCP by the time the line appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists():
            match = _LISTEN_RE.search(
                log_path.read_text(encoding="utf-8", errors="replace"))
            if match:
                return int(match.group(1))
        if proc.poll() is not None:
            return None  # exited before announcing a bound port
        time.sleep(0.1)
    return None


def _read_ledger_events(events_path: Path) -> list[dict]:
    """Parse events.ndjson into event dicts. Tolerates the file not existing yet
    (returns []) and a torn final line from an in-flight append (skips it), so a
    poller can retry rather than crash on a transient partial read."""
    if not events_path.is_file():
        return []
    events = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # final line still being written; a later poll will see it
    return events


def _wait_for_events(events_path: Path, predicate: Callable[[list[dict]], bool],
                     timeout: float = LEDGER_TIMEOUT_S) -> list[dict]:
    """Poll the ledger until ``predicate(events)`` holds, then return the events
    (or the last read once ``timeout`` elapses).

    The gateway appends the event before the HTTP result/error is returned to the
    caller, so the first read normally satisfies the predicate; the bounded poll
    only guards against read/flush skew and a momentarily slow box, and keeps a
    genuine "never written" case a clean assertion failure rather than a crash."""
    deadline = time.monotonic() + timeout
    while True:
        events = _read_ledger_events(events_path)
        if predicate(events) or time.monotonic() >= deadline:
            return events
        time.sleep(0.05)


class GatewayHttpIntegrationTest(unittest.TestCase):
    """End to end: subprocess gateway on an OS-assigned port, dev-local key, one
    tool call. Timers are off so the subprocess is hermetic (no ops-loop
    subprocesses, no network) and the only ledger writer is the call under test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "gateway.log"
        env = {**os.environ, "HEARTH_ROOT": self.tmp.name, "HEARTH_TIMERS": "off"}
        with self.log_path.open("wb") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "hearth.kernel.gateway",
                 "--port", "0", "--no-timers"],
                cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        self.addCleanup(self._stop)
        self.port = _wait_for_bound_port(self.log_path, self.proc, STARTUP_TIMEOUT_S)
        if self.port is None:
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-500:]
            self.skipTest(f"gateway failed to bind/announce a port: {detail}")

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
        # End-to-end proof that discovery mirrors authorization: the shipped
        # dev-local key carries `probe` (status only), so kernel_change must not
        # even be ADVERTISED to it. A caller should not learn the shape of a
        # surface it cannot reach.
        self.assertNotIn("kernel_change", tool_names)
        self.assertFalse(result.isError, result.content)

        events_path = Path(self.tmp.name) / "var" / "ledger" / "events.ndjson"
        events = _wait_for_events(events_path, lambda evs: any(
            e.get("tool") == "kernel_status" for e in evs))
        self.assertTrue(events_path.is_file(), "ledger not written under HEARTH_ROOT")
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
        events = _wait_for_events(events_path, lambda evs: any(
            e.get("tool") == "__auth__" and not e.get("ok") for e in evs))
        rejections = [e for e in events if e.get("tool") == "__auth__" and not e.get("ok")]
        self.assertGreaterEqual(len(rejections), 1)
        self.assertNotIn("wrong", json.dumps(rejections))


class NonLoopbackGatewayHttpIntegrationTest(unittest.TestCase):
    """Ephemeral proof of the explicit non-loopback container-access mode."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "gateway.log"
        env = {**os.environ, "HEARTH_ROOT": self.tmp.name, "HEARTH_TIMERS": "off"}
        with self.log_path.open("wb") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "hearth.kernel.gateway",
                 "--host", "0.0.0.0", "--port", "0", "--allow-non-loopback",
                 "--no-timers"],
                cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        self.addCleanup(self._stop)
        self.port = _wait_for_bound_port(self.log_path, self.proc, STARTUP_TIMEOUT_S)
        if self.port is None:
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-500:]
            self.skipTest(f"non-loopback gateway failed to bind: {detail}")

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def test_non_loopback_bind_authenticates_over_mcp(self):
        from mcp import ClientSession

        log = self.log_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("HEARTH IS BINDING A NON-LOOPBACK INTERFACE", log)
        self.assertIn("0.0.0.0", log)
        url = f"http://127.0.0.1:{self.port}/mcp"

        async def call():
            async with _client(url, "dev-local") as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("kernel_status", {})
                    return result

        result = asyncio.run(call())
        self.assertFalse(result.isError, result.content)


if __name__ == "__main__":
    unittest.main()
