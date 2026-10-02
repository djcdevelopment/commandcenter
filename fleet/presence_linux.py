#!/usr/bin/env python3
"""presence_linux — is the operator at this machine right now?

Banked Fire (ADR-0006) asks two idle questions before unattended dispatch: is the queue
empty, is the rung free. On a daily-driver desktop there is a third: is Derek sitting at
it. This module answers that from signals the GNOME/Wayland session already exposes, and
it FAILS CLOSED: any signal it cannot read counts as "present".

    away  iff  idle_ms >= PRESENCE_IDLE_MIN minutes      (Mutter IdleMonitor over D-Bus)
          and  no established RDP session on :3389        (ss)
               -- these two only while PRESENCE_GATE=idle; the environment's PRESENCE_GATE=off
                  (dev, ADR-0053) skips them
          and  no dispatch hold (HEARTH_DISPATCH_PAUSE_FILE / ~/hearth-production/pause.dispatch)
          and  ai-mode.json says "online"                 (omen-ai-mode)

The mode names follow fleet/mode_arbiter.py's vocabulary: "frontier-collab" while present,
"deep-research" while away. Stdlib only; every subprocess has a short timeout.

    python3 -m fleet.presence_linux            # JSON report
    python3 -m fleet.presence_linux --away     # exit 0 iff away (for ExecCondition= and shell gates)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from fleet import environment as envmod
except ImportError:  # run as a plain script from fleet/
    import environment as envmod

DEFAULT_IDLE_MIN = 20
HEARTH_ROOT = Path(os.environ.get("HEARTH_ROOT", str(Path.home() / "hearth-production")))
PAUSE_FILE = Path(os.environ.get("HEARTH_DISPATCH_PAUSE_FILE", str(HEARTH_ROOT / "pause.dispatch")))
AI_MODE_FILE = HEARTH_ROOT / "ai-mode.json"
RDP_PORT = int(os.environ.get("PRESENCE_RDP_PORT", "3389"))


def _run(argv: list[str], timeout: float = 3.0) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return None
    return out.stdout if out.returncode == 0 else None


def idle_ms() -> int | None:
    """Milliseconds since the last input on the GNOME session, or None when unreadable."""
    env = dict(os.environ)
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        uid = os.getuid()
        candidate = f"/run/user/{uid}/bus"
        if Path(candidate).exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={candidate}"
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
             "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
             "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
            capture_output=True, text=True, timeout=3.0, env=env, check=False)
    except Exception:
        return None
    m = re.search(r"uint64\s+(\d+)", out.stdout or "")
    return int(m.group(1)) if m else None


def rdp_sessions() -> int | None:
    out = _run(["ss", "-Htn", "state", "established", f"( sport = :{RDP_PORT} )"])
    if out is None:
        return None
    return sum(1 for line in out.splitlines() if line.strip())


def ai_mode() -> str | None:
    try:
        return str(json.loads(AI_MODE_FILE.read_text()).get("mode"))
    except Exception:
        return None


def report(idle_min: int | None = None) -> dict:
    env = envmod.read_environment()
    gate = envmod.knob("PRESENCE_GATE", "idle", env)
    threshold_min = idle_min if idle_min is not None else int(envmod.knob("PRESENCE_IDLE_MIN", str(DEFAULT_IDLE_MIN), env))
    idle = idle_ms()
    rdp = rdp_sessions()
    paused = PAUSE_FILE.exists()
    mode = ai_mode()
    reasons = []
    if gate != "off":  # anything but an explicit "off" keeps the prod gate (fail-closed)
        if idle is None:
            reasons.append("idle:unreadable")
        elif idle < threshold_min * 60_000:
            reasons.append(f"idle:{idle // 60_000}m<{threshold_min}m")
        if rdp is None:
            reasons.append("rdp:unreadable")
        elif rdp > 0:
            reasons.append(f"rdp:{rdp}")
    # the dispatch hold and ai-mode apply in every environment
    if paused:
        reasons.append("pause.dispatch")
    if mode != "online":
        reasons.append(f"ai-mode:{mode}")
    away = not reasons
    return {
        "schema": "presence-linux.v1",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "away": away,
        "mode": "deep-research" if away else "frontier-collab",
        "idle_ms": idle,
        "idle_threshold_min": threshold_min,
        "rdp_sessions": rdp,
        "dispatch_paused": paused,
        "ai_mode": mode,
        "present_reasons": reasons,
        "presence_gate": gate,
        "environment": envmod.stamp(env),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rep = report()
    if "--away" in argv:
        return 0 if rep["away"] else 1
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
