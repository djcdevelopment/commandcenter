"""doorcheck — deep health check (and revival) for the HEARTH gateway.

fleet_ping answers "does :8710 accept a TCP connect"; this answers "does the
door actually work": MCP handshake, toolsurface manifest match, backend pool
health, last ledger event, build-request lane. Run it under the venv-omen
interpreter (needs the mcp SDK for the live handshake).

    python -m hearth.callers.doorcheck                # human-readable verdict
    python -m hearth.callers.doorcheck --json          # machine-readable
    python -m hearth.callers.doorcheck --revive        # if down, relaunch detached + re-check
    python -m hearth.callers.doorcheck --restart       # bounce a stale/wedged door via
                                                        # the HearthGatewayRestart task, then
                                                        # re-verify
    python -m hearth.callers.doorcheck --probe-cloud   # also fire one real gcp-gemini
                                                        # generate (spends trial credit)

Exit code: 0 = gateway healthy AND default-ollama backend up AND toolsurface
manifest matches. 1 = degraded, down, or stale.

The --revive launch uses DETACHED_PROCESS so the gateway does NOT die with the
console that started it — the failure mode that killed it on 2026-07-03.

Toolsurface manifest (v2, added after the 2026-07-12 silent-staleness incident:
a provider landed in start-hearth-gateway.cmd but the running gateway process
predated it, so the door served 35/41 tools while reporting HEALTHY on raw tool
COUNT alone). The expected manifest is derived from the SAME source the gateway
itself reads at boot — the ``--providers`` list in start-hearth-gateway.cmd —
so there is exactly one authority for "what tools should this door have" and it
can never silently drift from the launcher. A mismatch (missing OR unexpected
tool names) is treated as DEGRADED, not healthy: a stale door quietly serving
old tools is worse than a door that is visibly down.

--restart never kills the gateway process directly (a normal shell cannot: the
HearthGatewayBoot task runs RL HIGHEST). It triggers the on-demand
HearthGatewayRestart scheduled task, which does the elevated kill + relaunch
(see hearth/etc/restart-hearth-gateway.cmd), and polls the port until it has
cycled down and back up.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from hearth.toolsurface import backends as backends_mod
from hearth.toolsurface import inference as inference_mod
from hearth.toolsurface.build_requests import DEFAULT_RECEIPT_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8710
LEDGER = REPO_ROOT / "hearth" / "var" / "ledger" / "events.ndjson"
START_CMD = REPO_ROOT / "hearth" / "etc" / "start-hearth-gateway.cmd"
CALLER_KEY = "dev-local"  # human runner_class — right identity for a health probe
RESTART_TASK = "HearthGatewayRestart"

# Mirrors hearth.kernel.gateway.builtin_get_tools' two tool names. Hardcoded
# (not imported) because hearth.kernel.gateway imports the mcp SDK at module
# level — importing it here would break doorcheck under a plain system python.
KERNEL_BUILTIN_TOOLS = ("kernel_status", "kernel_change")

_PROVIDERS_RE = re.compile(r"--providers\s+(\S+)")


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
    names = sorted(tool["name"] for tool in tools)
    return {
        "ok": True,
        "tools": len(tools),
        "tool_names": names,
        "handshake_ms": round((time.monotonic() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# Feature 1: toolsurface manifest
# ---------------------------------------------------------------------------

def _providers_from_start_cmd() -> list[str]:
    """Parse the --providers module list from start-hearth-gateway.cmd's
    `-m hearth.kernel.gateway` line — the SAME argument the gateway itself
    reads at boot."""
    text = START_CMD.read_text(encoding="utf-8", errors="ignore")
    match = _PROVIDERS_RE.search(text)
    if not match:
        raise ValueError(f"no --providers argument found in {START_CMD}")
    return [name for name in match.group(1).split(",") if name]


def _expected_manifest() -> tuple[set[str], list[str]]:
    """Import every provider named in start-hearth-gateway.cmd and collect the
    tool-name manifest the gateway should be serving, mirroring
    hearth.kernel.gateway.load_providers. Returns (names, errors); errors are
    non-fatal import/get_tools problems worth surfacing but not raising on."""
    names: set[str] = set(KERNEL_BUILTIN_TOOLS)
    errors: list[str] = []
    try:
        provider_modules = _providers_from_start_cmd()
    except (OSError, ValueError) as exc:
        return names, [f"could not read providers from {START_CMD.name}: {exc}"]

    for module_name in provider_modules:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            errors.append(f"{module_name}: not importable: {exc}")
            continue
        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            errors.append(f"{module_name}: no get_tools()")
            continue
        try:
            tools = get_tools()
        except Exception as exc:  # provider bug — surface it, don't crash doorcheck
            errors.append(f"{module_name}: get_tools() raised {type(exc).__name__}: {exc}")
            continue
        names.update(fn.__name__ for fn in tools)
    return names, errors


def _toolsurface_report(live_names: set[str] | None) -> dict:
    expected, errors = _expected_manifest()
    result: dict = {"expected": len(expected), "errors": errors}

    if live_names is None:
        result.update({
            "ok": False, "live": None, "missing": [], "extra": [],
            "line": "toolsurface: unknown - gateway not reachable for a handshake",
        })
        return result

    missing = sorted(expected - live_names)
    extra = sorted(live_names - expected)
    result["live"] = len(live_names)
    result["missing"] = missing
    result["extra"] = extra
    result["ok"] = not missing and not extra and not errors

    if result["ok"]:
        result["line"] = f"toolsurface: {len(live_names)}/{len(expected)} match"
    elif not missing and not extra:
        # Live matched, but computing the expected set itself hit a problem —
        # can't vouch for the manifest, so still not ok.
        result["line"] = "toolsurface: ERROR - " + "; ".join(errors)
    else:
        parts = []
        if missing:
            parts.append(f"missing {len(missing)}: {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {len(extra)}: {', '.join(extra)}")
        if errors:
            parts.append(f"manifest errors: {'; '.join(errors)}")
        result["line"] = "toolsurface: STALE - " + "; ".join(parts)
    return result


# ---------------------------------------------------------------------------
# Feature 3: backends layer
# ---------------------------------------------------------------------------

def _ollama_version(endpoint: str) -> str | None:
    try:
        url = f"{endpoint.rstrip('/')}/api/version"
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.load(resp).get("version")
    except OSError:
        return None


def _tcp_host_port(endpoint: str) -> tuple[str, int] | None:
    parsed = urllib.parse.urlsplit(endpoint)
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def _gemini_auth_status(backend) -> tuple[bool, str]:
    token, error = inference_mod._google_access_token(backend.auth_env)
    if token:
        source = "adc"
        if backend.auth_env and os.environ.get(backend.auth_env, "").strip():
            source = f"env:{backend.auth_env}"
        return True, f"auth ok ({source})"
    return False, f"auth FAILED - {error}"


def _probe_gemini() -> dict:
    """--probe-cloud: fire one real, cheap generate through gcp-gemini."""
    result = inference_mod.local_generate(
        prompt="ping", backend="gcp-gemini", max_tokens=8, timeout_s=60,
    )
    return {
        "ok": result.get("ok", False),
        "tokens_out": result.get("tokens_out"),
        "duration_ms": result.get("duration_ms"),
        "error": result.get("error"),
    }


def _backend_status(backend, probe_cloud: bool) -> dict:
    entry: dict = {"name": backend.name, "api": backend.api}

    if backend.api == "ollama":
        version = _ollama_version(backend.endpoint)
        entry["up"] = version is not None
        entry["version"] = version
        entry["line"] = f"{backend.name}: {version}" if version else f"{backend.name}: DOWN"

    elif backend.api == "openai":
        # am4-oxen: TCP-only, informational — AM4 sleeps by design (banked fire).
        host_port = _tcp_host_port(backend.endpoint)
        awake = bool(host_port) and _tcp_up(*host_port, timeout=2.0)
        entry["up"] = None  # informational: never gates exit
        entry["awake"] = awake
        entry["line"] = (
            f"{backend.name}: awake (tcp {host_port[0]}:{host_port[1]} up)" if awake
            else f"{backend.name}: asleep (banked fire — informational only)"
        )

    elif backend.api == "gemini":
        auth_ok, detail = _gemini_auth_status(backend)
        entry["up"] = None  # auth-only: never gates exit; WARNING not failure
        entry["auth_ok"] = auth_ok
        entry["line"] = f"{backend.name}: {detail}"
        if probe_cloud and auth_ok:
            probe = _probe_gemini()
            entry["probe"] = probe
            entry["line"] += (
                f"; probe ok tokens_out={probe['tokens_out']} {probe['duration_ms']}ms"
                if probe["ok"] else f"; probe FAILED - {probe['error']}"
            )

    else:  # pragma: no cover - backends.py validates api at load time
        entry["up"] = None
        entry["line"] = f"{backend.name}: unknown api {backend.api!r}"

    return entry


def _backends_report(probe_cloud: bool) -> dict:
    try:
        pool = backends_mod.load_pool()
    except backends_mod.BackendConfigError as exc:
        return {
            "backends": [{"name": None, "api": None, "up": False,
                         "line": f"backends: config error - {exc}"}],
            "default_up": False,
        }

    entries = []
    default_up = False
    for backend in pool.backends:
        entry = _backend_status(backend, probe_cloud)
        entry["default"] = backend.name == pool.default
        if entry["default"]:
            default_up = bool(entry.get("up"))
        entries.append(entry)
    return {"backends": entries, "default_up": default_up}


# ---------------------------------------------------------------------------
# Ledger staleness + build-request lane
# ---------------------------------------------------------------------------

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


def _age_str(iso_ts: str | None) -> str:
    if not iso_ts:
        return "unknown"
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _trial_burn_report() -> str:
    """A4: one line of trial-credit runway truth, tolerant of every failure mode."""
    try:
        pool = backends_mod.load_pool()
        budget = pool.trial.get("budget_tokens")
        if not budget:
            return "trial-burn: none configured"
        try:
            budget_int = int(budget)
            reserve_int = int(pool.trial.get("reserve_tokens", 0))
        except (TypeError, ValueError):
            return "trial-burn: invalid budget config"

        offload_path = inference_mod.resolve_in_scope("knowledge/offload.json")
        if not offload_path.is_file():
            return "trial-burn: no data"
        with open(offload_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        trial_data = (data.get("per_class") or {}).get("trial") or {}
        burn = int(trial_data.get("tokens_in") or 0) + int(trial_data.get("tokens_out") or 0)

        line = f"trial-burn: {burn:,} / {budget_int:,} tokens (reserve {reserve_int:,})"
        if burn >= budget_int - reserve_int:
            line += " [SUPPRESSED]"
        return line
    except Exception as exc:
        return f"trial-burn: ERROR - {type(exc).__name__}: {exc}"


def _build_request_lane() -> dict:
    root = Path(os.environ.get("HEARTH_BUILD_REQUEST_DIR", str(DEFAULT_RECEIPT_DIR)))
    ledger_path = root / "ledger.jsonl"
    if not ledger_path.is_file():
        return {"ok": True, "line": "build-reqs: no lane"}

    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"ok": True, "line": "build-reqs: no lane"}

    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        receipt_id = row.get("receipt_id", "?")
        status = row.get("status", "?")
        age = _age_str(row.get("updated_utc") or row.get("created_utc"))
        return {
            "ok": True, "receipt_id": receipt_id, "status": status, "age": age,
            "line": f"build-reqs: {receipt_id} {status} ({age})",
        }
    return {"ok": True, "line": "build-reqs: no lane"}


# ---------------------------------------------------------------------------
# Revive / restart
# ---------------------------------------------------------------------------

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


def _restart(timeout_s: float = 25.0) -> dict:
    """Bounce a stale/wedged door via the HearthGatewayRestart scheduled task
    (RL HIGHEST, S4U — the only reliable bounce from medium integrity, since
    HearthGatewayBoot's python child cannot be killed from a normal shell).
    Never attempts to kill the process directly. Polls until the port has
    cycled down and back up, or the timeout elapses."""
    proc = subprocess.run(
        ["schtasks", "/Run", "/TN", RESTART_TASK],
        capture_output=True, text=True, timeout=15,
    )
    triggered = proc.returncode == 0
    deadline = time.monotonic() + timeout_s
    saw_down = False
    while time.monotonic() < deadline:
        if _tcp_up(GATEWAY_HOST, GATEWAY_PORT, timeout=1.0):
            if saw_down:
                return {"triggered": triggered, "saw_down": True, "up": True}
        else:
            saw_down = True
        time.sleep(0.5)
    return {
        "triggered": triggered, "saw_down": saw_down,
        "up": _tcp_up(GATEWAY_HOST, GATEWAY_PORT),
    }


# ---------------------------------------------------------------------------
# check() + CLI
# ---------------------------------------------------------------------------

def check(revive: bool = False, probe_cloud: bool = False) -> dict:
    report: dict = {"gateway": "down", "revived": False}
    up = _tcp_up(GATEWAY_HOST, GATEWAY_PORT)

    if not up and revive:
        report["revived"] = up = _revive()

    live_names: set[str] | None = None
    if up:
        try:
            mcp_report = _mcp_handshake()
            report["mcp"] = mcp_report
            report["gateway"] = "up"
            live_names = set(mcp_report["tool_names"])
        except Exception as exc:  # port open but door broken — worth distinguishing
            report["gateway"] = "degraded"
            report["mcp"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    report["toolsurface"] = _toolsurface_report(live_names)

    backend_health = _backends_report(probe_cloud)
    report["backends"] = backend_health["backends"]
    report["default_backend_up"] = backend_health["default_up"]

    report["last_ledger_event"] = _last_ledger_event()
    report["build_requests"] = _build_request_lane()
    report["trial_burn_line"] = _trial_burn_report()

    report["ok"] = (
        report["gateway"] == "up"
        and report["default_backend_up"]
        and report["toolsurface"]["ok"]
    )
    return report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--revive", action="store_true", help="relaunch detached if down")
    ap.add_argument("--restart", action="store_true",
                    help="bounce a stale/wedged door via the HearthGatewayRestart "
                         "scheduled task, poll ~25s, then re-verify")
    ap.add_argument("--probe-cloud", action="store_true",
                    help="fire one real gcp-gemini generate (spends trial credit); "
                         "off by default")
    args = ap.parse_args(argv)

    restart_result = _restart() if args.restart else None

    report = check(revive=args.revive, probe_cloud=args.probe_cloud)
    if restart_result is not None:
        report["restart"] = restart_result

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        mcp = report.get("mcp") or {}
        print(f"gateway  : {report['gateway']}"
              + (" (revived just now)" if report["revived"] else ""))
        if restart_result is not None:
            print(f"restart  : triggered={restart_result['triggered']} "
                  f"saw_down={restart_result['saw_down']} up={restart_result['up']}")
        if mcp:
            detail = (f"{mcp['tools']} tools, handshake {mcp['handshake_ms']}ms"
                      if mcp.get("ok") else mcp.get("error", "?"))
            print(f"mcp      : {detail}")
        print(report["toolsurface"]["line"])
        for entry in report["backends"]:
            print(entry["line"])
        print(f"ledger   : last event {report['last_ledger_event'] or 'none'}")
        print(report["build_requests"]["line"])
        print(report["trial_burn_line"])
        print("verdict  : " + ("HEALTHY" if report["ok"] else "DEGRADED"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
