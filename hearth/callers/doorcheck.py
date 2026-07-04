"""doorcheck — deep health check (and revival) for the HEARTH gateway.

fleet_ping answers "does :8710 accept a TCP connect"; this answers "does the
door actually work": MCP handshake, tool count, Ollama backend, last ledger
event. Run it under the venv-omen interpreter (needs the mcp SDK).

    python -m hearth.callers.doorcheck            # human-readable verdict
    python -m hearth.callers.doorcheck --json     # machine-readable
    python -m hearth.callers.doorcheck --revive   # if down, relaunch detached + re-check

Exit code: 0 = gateway healthy (handshake OK), 1 = degraded or down.

The --revive launch uses DETACHED_PROCESS so the gateway does NOT die with the
console that started it — the failure mode that killed it on 2026-07-03.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8710
OLLAMA_URL = "http://127.0.0.1:11434/api/version"
LEDGER = REPO_ROOT / "hearth" / "var" / "ledger" / "events.ndjson"
START_CMD = REPO_ROOT / "hearth" / "etc" / "start-hearth-gateway.cmd"
CALLER_KEY = "dev-local"  # human runner_class — right identity for a health probe


def _tcp_up(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _mcp_handshake() -> dict:
    """Full MCP initialize + list_tools through the real client."""
    from hearth.callers.client import HearthClient  # lazy: needs mcp SDK

    t0 = time.monotonic()
    tools = HearthClient(key=CALLER_KEY).list_tools_sync()
    return {
        "ok": True,
        "tools": len(tools),
        "handshake_ms": round((time.monotonic() - t0) * 1000),
    }


def _ollama_version() -> str | None:
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=3) as resp:
            return json.load(resp).get("version")
    except OSError:
        return None


def _last_ledger_event() -> str | None:
    """Timestamp of the newest event, or None if the ledger is empty/missing."""
    try:
        with LEDGER.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 8192))
            lines = fh.read().splitlines()
        for raw in reversed(lines):
            if raw.strip():
                return json.loads(raw).get("ts")
    except (OSError, ValueError):
        pass
    return None


def _revive() -> bool:
    """Relaunch the gateway detached from any console; True if it comes up."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["cmd.exe", "/c", str(START_CMD)],
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
    )
    for _ in range(20):  # up to ~10s for uvicorn to bind
        time.sleep(0.5)
        if _tcp_up(GATEWAY_HOST, GATEWAY_PORT):
            return True
    return False


def check(revive: bool = False) -> dict:
    report: dict = {"gateway": "down", "revived": False}
    up = _tcp_up(GATEWAY_HOST, GATEWAY_PORT)

    if not up and revive:
        report["revived"] = up = _revive()

    if up:
        try:
            report["mcp"] = _mcp_handshake()
            report["gateway"] = "up"
        except Exception as exc:  # port open but door broken — worth distinguishing
            report["gateway"] = "degraded"
            report["mcp"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    report["ollama"] = _ollama_version()
    report["last_ledger_event"] = _last_ledger_event()
    report["ok"] = report["gateway"] == "up" and report["ollama"] is not None
    return report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--revive", action="store_true", help="relaunch detached if down")
    args = ap.parse_args(argv)

    report = check(revive=args.revive)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        mcp = report.get("mcp") or {}
        print(f"gateway  : {report['gateway']}"
              + (" (revived just now)" if report["revived"] else ""))
        if mcp:
            detail = (f"{mcp['tools']} tools, handshake {mcp['handshake_ms']}ms"
                      if mcp.get("ok") else mcp.get("error", "?"))
            print(f"mcp      : {detail}")
        print(f"ollama   : {report['ollama'] or 'DOWN'}")
        print(f"ledger   : last event {report['last_ledger_event'] or 'none'}")
        print("verdict  : " + ("HEALTHY" if report["ok"] else "DEGRADED"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
