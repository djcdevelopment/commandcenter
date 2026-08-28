"""External liveness watch for the BF6 media pipeline.

The three OMEN workers deliberately live in an interactive Windows session,
outside the HEARTH gateway.  That makes GPU access work, but also means the
gateway cannot notice when they exit.  This one-shot watcher is run by Task
Scheduler once per minute.  It heals stopped OMEN tasks, restarts a wedged
render agent when its existing heartbeat goes stale, and records AM4 health
without attempting remote mutation.

It is intentionally a one-shot, not a fourth resident service.  If this process
is interrupted, Task Scheduler starts a fresh observation on the next minute.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional

from hearth.media import handoff


TASK_NAMES = ("BF6Extractor", "BF6RenderAgent", "BF6RenderBridge")
AGENT_TASK = "BF6RenderAgent"
_PYTHON = r'(?:"[^"]*python(?:\.exe)?"|[^\s"]*python(?:\.exe)?)'
WORKER_PATTERNS = {
    "BF6Extractor": re.compile(
        r"^\s*%s\s+extract\.py\s+--poll\s+20\s*$" % _PYTHON, re.IGNORECASE
    ),
    "BF6RenderAgent": re.compile(
        r"^\s*%s\s+-m\s+hearth\.media\.agent\s*$" % _PYTHON, re.IGNORECASE
    ),
    "BF6RenderBridge": re.compile(
        r"^\s*%s\s+-m\s+hearth\.media\.bf6_bridge\s*$" % _PYTHON,
        re.IGNORECASE,
    ),
}
DEFAULT_AM4_HEALTH_URL = os.environ.get(
    "BF6_AM4_HEALTH_URL", "http://192.168.12.233:8787/health"
)
VAR_RENDER = Path(__file__).resolve().parents[1] / "var" / "render"
DEFAULT_STATUS_PATH = VAR_RENDER / "pipeline-watchdog.json"
DEFAULT_EVENT_LOG_PATH = VAR_RENDER / "pipeline-watchdog.ndjson"
DEFAULT_PAUSE_PATH = VAR_RENDER / "PAUSED"


def paused_report(
    pause_path: Path = DEFAULT_PAUSE_PATH,
    *,
    clock: Callable[[], float] = time.time,
) -> dict:
    """Describe an intentional maintenance pause without healing workers.

    The marker is deliberately file-based so an operator can pause the pipeline
    without changing a privileged Scheduled Task. Removing it is the resume
    operation; the next watchdog tick restores missing workers.
    """
    detail = "operator maintenance hold"
    try:
        configured = pause_path.read_text(encoding="utf-8").strip()
        if configured:
            detail = configured
    except OSError:
        pass
    return {
        "schema_version": 1,
        "at": clock(),
        "status": "paused",
        "healthy": True,
        "paused": True,
        "pause_path": str(pause_path),
        "detail": detail,
        "tasks": {},
        "agent": {},
        "am4": {},
        "actions": [],
        "errors": [],
    }


def _ps_literal(value: str) -> str:
    return "'%s'" % value.replace("'", "''")


def _powershell(script: str, *, timeout_s: float = 15.0) -> str:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout_s,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "PowerShell failed").strip()
        raise RuntimeError(detail[:500])
    return (completed.stdout or "").strip()


def scheduled_task_states(names: Iterable[str] = TASK_NAMES) -> dict[str, str]:
    """Return Task Scheduler's current state for each named worker."""
    literals = ",".join(_ps_literal(name) for name in names)
    script = (
        "$ErrorActionPreference='Stop'; $names=@(%s); "
        "$rows=@(foreach($name in $names){"
        "$task=Get-ScheduledTask -TaskName $name;"
        "[pscustomobject]@{name=$name;state=[string]$task.State}});"
        "ConvertTo-Json -InputObject $rows -Compress" % literals
    )
    rows = json.loads(_powershell(script))
    if isinstance(rows, dict):
        rows = [rows]
    return {str(row["name"]): str(row["state"]) for row in rows}


def worker_processes() -> dict[str, list[int]]:
    """Find only the exact production worker command lines.

    The deployment incident was caused by a broad command-line regex that also
    matched test processes.  These anchored patterns intentionally exclude
    ``--once`` probes, shell polling loops, and any command carrying extra
    arguments.  Process identity is observation truth; Task Scheduler state is
    only launch control because stopping a ``.cmd`` task can orphan its child.
    """
    script = (
        "$rows=@(Get-CimInstance Win32_Process | "
        "Where-Object {$_.Name -ieq 'python.exe'} | "
        "ForEach-Object {[pscustomobject]@{pid=$_.ProcessId;command=$_.CommandLine}});"
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    raw = _powershell(script)
    rows = json.loads(raw) if raw else []
    if isinstance(rows, dict):
        rows = [rows]
    found = {name: [] for name in TASK_NAMES}
    for row in rows:
        command = str(row.get("command") or "")
        for name, pattern in WORKER_PATTERNS.items():
            if pattern.fullmatch(command):
                found[name].append(int(row["pid"]))
                break
    return found


def start_scheduled_task(name: str) -> None:
    _powershell(
        "$ErrorActionPreference='Stop'; "
        "Stop-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 250; Start-ScheduledTask -TaskName %s"
        % (_ps_literal(name), _ps_literal(name))
    )


def restart_worker(name: str, pids: Iterable[int]) -> None:
    """Restart only PIDs that still match this exact production worker."""
    expected = {int(pid) for pid in pids}
    current = set(worker_processes().get(name, []))
    if not expected or not expected.issubset(current):
        raise RuntimeError("worker PID identity changed; refusing restart")
    for pid in sorted(expected):
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "taskkill failed")[:500])
    start_scheduled_task(name)


def probe_am4(url: str = DEFAULT_AM4_HEALTH_URL, *, timeout_s: float = 5.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "BF6PipelineWatchdog/1"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ok = (
        payload.get("status") == "ok"
        and payload.get("rawMounted") is True
        and payload.get("workWritable") is True
    )
    return {"ok": ok, "url": url, **payload}


def _agent_dict(status: handoff.AgentStatus) -> dict:
    return {
        "available": status.available,
        "capable": status.capable,
        "detail": status.detail,
        "age_s": round(status.age_s, 1) if status.age_s is not None else None,
        "lanes": list(status.lanes),
    }


def inspect_and_heal(
    *,
    heal: bool = True,
    task_probe: Callable[[Iterable[str]], dict[str, str]] = scheduled_task_states,
    process_probe: Callable[[], dict[str, list[int]]] = worker_processes,
    task_start: Callable[[str], None] = start_scheduled_task,
    worker_restart: Callable[[str, Iterable[int]], None] = restart_worker,
    agent_probe: Callable[[], handoff.AgentStatus] = handoff.agent_status,
    am4_probe: Callable[[], dict] = probe_am4,
    clock: Callable[[], float] = time.time,
) -> dict:
    """Take one observation and perform only the two bounded local repairs."""
    report = {
        "schema_version": 1,
        "at": clock(),
        "status": "healthy",
        "healthy": True,
        "tasks": {},
        "agent": {},
        "am4": {},
        "actions": [],
        "errors": [],
    }

    try:
        states = task_probe(TASK_NAMES)
    except Exception as exc:
        states = {}
        report["errors"].append("task probe failed: %s" % exc)
    try:
        processes = process_probe()
        process_probe_ok = True
    except Exception as exc:
        processes = {}
        process_probe_ok = False
        report["errors"].append("worker process probe failed: %s" % exc)

    started = set()
    for name in TASK_NAMES:
        state = states.get(name, "missing")
        pids = list(processes.get(name, []))
        report["tasks"][name] = {
            "scheduler_state": state,
            "pids": pids,
            "running": bool(pids),
        }
        if len(pids) > 1:
            report["errors"].append("%s has %d production processes" % (name, len(pids)))
            continue
        if pids:
            continue
        if not process_probe_ok:
            continue  # unknown is not permission to launch a possible duplicate
        report["errors"].append("%s has no production process (task is %s)" % (name, state))
        if not heal:
            continue
        try:
            task_start(name)
            started.add(name)
            report["actions"].append({"action": "start_task", "task": name})
        except Exception as exc:
            report["errors"].append("could not start %s: %s" % (name, exc))

    try:
        agent = agent_probe()
        report["agent"] = _agent_dict(agent)
        if not agent.available:
            report["errors"].append(agent.detail or "render agent heartbeat unavailable")
            agent_pids = processes.get(AGENT_TASK, [])
            if heal and AGENT_TASK not in started and len(agent_pids) == 1:
                try:
                    worker_restart(AGENT_TASK, agent_pids)
                    report["actions"].append(
                        {"action": "restart_stale_agent", "task": AGENT_TASK}
                    )
                except Exception as exc:
                    report["errors"].append("could not restart %s: %s" % (AGENT_TASK, exc))
        elif not agent.capable:
            # Repeated restarts cannot manufacture a D3D adapter. Make this
            # loud and leave the process available for inspection.
            report["errors"].append("render agent is alive but not GPU-capable: %s" % agent.detail)
    except Exception as exc:
        report["errors"].append("agent heartbeat probe failed: %s" % exc)

    try:
        am4 = am4_probe()
        report["am4"] = dict(am4)
        if not am4.get("ok"):
            report["errors"].append(
                "AM4 unhealthy: status=%s rawMounted=%s workWritable=%s"
                % (am4.get("status"), am4.get("rawMounted"), am4.get("workWritable"))
            )
    except Exception as exc:
        report["am4"] = {"ok": False, "error": str(exc)}
        report["errors"].append("AM4 health probe failed: %s" % exc)

    if report["errors"]:
        report["healthy"] = False
        report["status"] = "healing" if report["actions"] else "unhealthy"
    return report


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def persist_report(
    report: dict,
    *,
    status_path: Path = DEFAULT_STATUS_PATH,
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH,
) -> None:
    """Publish current truth; append only transitions and repair actions."""
    previous = _read_json(status_path)
    _write_atomic(status_path, report)
    changed = (
        previous is None
        or previous.get("status") != report.get("status")
        or previous.get("errors") != report.get("errors")
        or bool(report.get("actions"))
    )
    if not changed:
        return
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    with event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BF6 pipeline external liveness watcher")
    parser.add_argument("--no-heal", action="store_true", help="observe without starting tasks")
    parser.add_argument("--json", action="store_true", help="print the report")
    parser.add_argument("--am4-health-url", default=DEFAULT_AM4_HEALTH_URL)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--event-log-path", type=Path, default=DEFAULT_EVENT_LOG_PATH)
    parser.add_argument("--pause-path", type=Path, default=DEFAULT_PAUSE_PATH)
    args = parser.parse_args(argv)

    if args.pause_path.exists():
        report = paused_report(args.pause_path)
    else:
        report = inspect_and_heal(
            heal=not args.no_heal,
            am4_probe=lambda: probe_am4(args.am4_health_url),
        )
    persist_report(report, status_path=args.status_path, event_log_path=args.event_log_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
