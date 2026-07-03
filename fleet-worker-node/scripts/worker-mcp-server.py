#!/usr/bin/env python3
"""MCP control surface for a commandcenter fleet *worker* node.

Reusable, GPU-agnostic worker template — the northbound contract a conductor
(OMEN) uses to discover and drive a worker. Modeled on am4-fleet-node's MCP
server, with the inference/GPU specifics stripped out.

Launched on demand over SSH stdio, e.g.:

  ssh -i ~/.ssh/claudevm_ed25519 claude@<host> \
      ~/fleet-worker-node/.venv/bin/python \
      ~/fleet-worker-node/scripts/worker-mcp-server.py

S0 exposes node_status + ping + a node resource. run_plan/agent_status arrive in S1.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("FLEET_WORKER_ROOT", Path(__file__).resolve().parent.parent))
NODE_JSON = ROOT / "node.json"

mcp = FastMCP("fleet-worker-node")


def _cmd(args: list[str], timeout: int = 10) -> str:
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
        return (p.stdout or p.stderr).strip()
    except Exception as exc:  # noqa: BLE001 - reported as a status string
        return f"(unavailable: {exc})"


def _which(name: str) -> str:
    return shutil.which(name) or "(absent)"


@mcp.resource("worker://node")
def node_resource() -> str:
    if NODE_JSON.exists():
        return NODE_JSON.read_text(encoding="utf-8")
    return json.dumps({"node": socket.gethostname(), "note": "no node.json"}, indent=2)


@mcp.tool()
def node_status() -> dict[str, Any]:
    """Return this worker's identity, OS, resources, and tool availability."""
    du = shutil.disk_usage(str(ROOT))
    try:
        load = os.getloadavg()  # POSIX only
    except (OSError, AttributeError):
        load = None
    return {
        "node": socket.gethostname(),
        "user": _cmd(["whoami"]),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "loadavg": load,
        "disk_root": str(ROOT),
        "disk_free_gib": round(du.free / 1024**3, 1),
        "disk_total_gib": round(du.total / 1024**3, 1),
        "tools": {
            "node": _which("node"),
            "git": _which("git"),
            "claude": _which("claude"),
            "python3": _which("python3"),
        },
        "time": int(time.time()),
    }


@mcp.tool()
def ping(message: str = "ok") -> dict[str, Any]:
    """Liveness echo over the MCP transport."""
    return {"pong": message, "node": socket.gethostname(), "time": int(time.time())}


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("http", "streamable-http"):
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
