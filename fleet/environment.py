"""The lab's environment (dev | prod): the policy axis beside lab configuration and caller profiles.

Stdlib only, no hearth imports: systemd units, shells and every interpreter on this host read the
same flat KEY=VALUE file, ~/.config/hearth/environment, rendered by tools/ops/hearth_env.py from
host/environments/<name>.env. lab-rnd carries a copy of this reader (daily/environment.py); the
file format is the contract, not this code (ADR-0053).

Fail-closed: a missing or unreadable file, an unknown name, or a past HEARTH_ENVIRONMENT_UNTIL all
read as prod, and callers pass prod values as their defaults. Precedence for one knob:
process environment > file > the caller's default.

Python reads the file itself on every call; units must NOT load it with EnvironmentFile=. A value
copied into a long-lived process environment would outrank the file and outlive its UNTIL, so the
process environment is reserved for explicit one-off overrides (e.g. a manual tick).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

def env_file() -> Path:
    """Resolved per call, so a test or a caller can point HEARTH_ENVIRONMENT_FILE elsewhere at any time."""
    return Path(os.environ.get("HEARTH_ENVIRONMENT_FILE", "~/.config/hearth/environment")).expanduser()
NAMES = ("dev", "prod")
HEADER_KEYS = ("HEARTH_ENVIRONMENT", "HEARTH_ENVIRONMENT_SET_BY", "HEARTH_ENVIRONMENT_SET_AT",
               "HEARTH_ENVIRONMENT_REASON", "HEARTH_ENVIRONMENT_UNTIL")


def parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def read_environment(path: Path | None = None) -> dict:
    """{"name", "knobs", "header", "expired", "path"}; knobs are empty when it reads as prod by default."""
    path = Path(path) if path else env_file()
    try:
        values = parse(path.read_text())
    except OSError:
        return {"name": "prod", "knobs": {}, "header": {}, "expired": False, "path": str(path), "missing": True}
    header = {k: values.pop(k) for k in HEADER_KEYS if k in values}
    name = header.get("HEARTH_ENVIRONMENT", "prod")
    expired = False
    until = header.get("HEARTH_ENVIRONMENT_UNTIL")
    if until:
        try:
            expired = datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone(timezone.utc) <= datetime.now(timezone.utc)
        except ValueError:
            expired = True
    if name not in NAMES or expired:
        return {"name": "prod", "knobs": {}, "header": header, "expired": expired, "path": str(path)}
    return {"name": name, "knobs": values, "header": header, "expired": False, "path": str(path)}


def knob(key: str, default: str, env: dict | None = None) -> str:
    """One policy value: process environment, then the environment file, then `default` (prod)."""
    if key in os.environ:
        return os.environ[key]
    env = env if env is not None else read_environment()
    return env["knobs"].get(key, default)


def stamp(env: dict | None = None) -> dict:
    """The small record every receipt, report and ledger row carries."""
    env = env if env is not None else read_environment()
    h = env["header"]
    return {"name": env["name"], "set_by": h.get("HEARTH_ENVIRONMENT_SET_BY"),
            "set_at": h.get("HEARTH_ENVIRONMENT_SET_AT"), "until": h.get("HEARTH_ENVIRONMENT_UNTIL"),
            "expired": env["expired"]}
