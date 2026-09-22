"""Calling the live door as a DATA SOURCE for `inspect --refresh`.

The production gateway runs the code it was started with, from
``C:\\work\\commandcenter`` master, and WI-G1 does not restart it. So the
operator never mounts itself into the running door in order to read capacity —
it calls the tools that door already serves (``kernel_status``,
``capture_resource_snapshot``, ``query_rung_state``, ``list_execution_providers``,
``rotation_status``, ``list_image_lanes``) over the same authenticated MCP
transport every other caller uses.

Everything here fails soft and loudly: an unreachable door, a missing SDK, a
missing key, or a tool that raised all come back as ``ok: False`` with a reason
that names the cause, and the snapshot records ``door.reachable: false`` with
per-field nulls. Nothing is guessed, and no exception escapes into a capture.

The key travels from ``HEARTH_API_KEY`` into the HTTP header and nowhere else.
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.parse
from typing import Any, Callable, Optional

DoorCall = Callable[..., dict]

# Everything the MCP client can raise when the far end is not there. CancelledError
# is in the list on purpose: an MCP handshake against a closed port fails inside an
# anyio task group and surfaces as asyncio.CancelledError, which derives from
# BaseException and therefore walks straight through `except Exception`. Measured
# here on 2026-09-17 by pointing the real client at a closed port: without this,
# `inspect --refresh` crashed instead of recording door.reachable=false.
DOOR_FAILURES = (Exception, asyncio.CancelledError)


def _unpack(result: dict) -> Any:
    """The tool's own return value, out of the MCP envelope."""
    structured = result.get("structured")
    if isinstance(structured, dict):
        # FastMCP wraps a non-object return in {"result": ...}.
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        try:
            return json.loads(text)
        except ValueError:
            return text
    return None


def unavailable_door(reason: str) -> DoorCall:
    """A door that answers every call with the same honest refusal."""

    def call(tool: str, **_kwargs: Any) -> dict:
        return {"ok": False, "tool": tool, "value": None, "error": reason}

    return call


def listening(endpoint: str, timeout_s: float = 2.0) -> tuple[bool, str]:
    """Is anything accepting connections at the endpoint's host and port?

    Checked before the MCP handshake because a handshake against a closed port
    fails deep inside the SDK's task group and prints a page of teardown noise
    for a fact one connect answers. A TCP listener proves a socket, never that a
    door can serve — the handshake still decides that.
    """
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, f"{host}:{port} accepts connections"
    except OSError as exc:
        return False, f"no listener on {host}:{port} ({type(exc).__name__})"


def make_door(key: Optional[str], endpoint: Optional[str] = None) -> DoorCall:
    """A door client bound to this caller's key, or a refusing stand-in."""
    if not key:
        return unavailable_door(
            "no door key: HEARTH_API_KEY is not set, so the live door cannot be called")
    try:
        from hearth.callers.client import DEFAULT_ENDPOINT, HearthClient
    except Exception as exc:  # noqa: BLE001 - the mcp SDK is not in every interpreter
        return unavailable_door(
            f"door client unavailable in this interpreter ({type(exc).__name__}: {exc}); "
            "run the CLI under fleet-worker-node/.venv-omen for --refresh")

    resolved = endpoint or DEFAULT_ENDPOINT
    up, detail = listening(resolved)
    if not up:
        return unavailable_door(f"the door is not reachable: {detail}")

    client = HearthClient(endpoint=resolved, key=key)

    def call(tool: str, **kwargs: Any) -> dict:
        try:
            raw = client.call_sync(tool, **kwargs)
        except DOOR_FAILURES as exc:  # a down door is data, not a crash
            return {"ok": False, "tool": tool, "value": None,
                    "error": f"{type(exc).__name__}: {exc}"}
        if not raw.get("ok"):
            return {"ok": False, "tool": tool, "value": None,
                    "error": (raw.get("text") or "the door refused the call")[:400]}
        return {"ok": True, "tool": tool, "value": _unpack(raw), "error": None}

    return call
