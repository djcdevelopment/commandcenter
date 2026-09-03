"""On-demand launcher for OMEN's device-local image worker.

Monitoring schedules belong on fx99. This launcher is deliberately not a
watchdog: it starts the worker only when an operator requests an image session.
The worker retires itself after its configured no-session idle interval.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from hearth.imagegen import handoff

START_SCRIPT = Path(r"E:\omen\imagegen\ops\Start-ImageGenAgent.ps1")
RECOVERY_SCRIPT = Path(r"E:\omen\imagegen\ops\Invoke-ImageGenRecovery.ps1")
START_TIMEOUT_SECONDS = 90.0
_launch_lock = threading.Lock()


def runtime_preflight() -> dict:
    """Report whether the out-of-repo imagegen runtime is actually installed.

    Both entry points into `E:\\omen\\imagegen` are hardcoded absolute paths into a
    SEPARATE repository, and nothing in this one deploys or validates them. A missing
    runtime should be a loud, named failure -- not a launcher that times out after 90 s and
    a scheduled recovery that quietly never runs again.
    """
    scripts = {"start_script": START_SCRIPT, "recovery_script": RECOVERY_SCRIPT}
    missing = sorted(name for name, path in scripts.items() if not path.is_file())
    return {
        "ok": not missing,
        "missing": missing,
        "paths": {name: str(path) for name, path in scripts.items()},
        "detail": "imagegen runtime present" if not missing else
                  "imagegen runtime is not installed at the expected paths: " +
                  ", ".join(str(scripts[name]) for name in missing),
    }


def ensure_running(*, timeout_s: float = START_TIMEOUT_SECONDS) -> handoff.AgentStatus:
    """Return a ready worker, starting one locally when necessary."""
    status = handoff.agent_status()
    if status.available:
        return status
    if os.name != "nt":
        return handoff.AgentStatus(
            False, status.age_seconds,
            "image worker can only be launched on OMEN/Windows", status.record,
        )

    with _launch_lock:
        status = handoff.agent_status()
        if status.available:
            return status
        if not START_SCRIPT.is_file():
            return handoff.AgentStatus(
                False, status.age_seconds,
                "image worker launch script is missing: %s" % START_SCRIPT,
                status.record,
            )
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
            r"System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        try:
            subprocess.Popen(
                [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                 "-File", str(START_SCRIPT)],
                cwd=str(START_SCRIPT.parent.parent),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        except OSError as exc:
            return handoff.AgentStatus(
                False, status.age_seconds,
                "image worker launch failed: %s" % exc, status.record,
            )

        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            status = handoff.agent_status()
            if status.available:
                return status
            time.sleep(0.5)
        status = handoff.agent_status()
        return handoff.AgentStatus(
            False, status.age_seconds,
            "image worker did not become ready within %.0f seconds" % timeout_s,
            status.record,
        )
