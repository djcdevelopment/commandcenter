"""HEARTH whole-lab configuration awareness (Task 10, Task 8, ADR-0052).

Provides the router and admission layers with knowledge of the active lab
configuration and whether each backend is expected live or absent.
"""

from __future__ import annotations

import os
from pathlib import Path
import tomllib
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "host" / "lab-configurations.toml"
ALT_CONFIG_PATH = Path.home() / ".config" / "omen-vllm" / "lab-configurations.toml"
OMEN_PROFILE_PATH = Path.home() / ".config" / "omen-vllm" / "profile"
AM4_PROFILE_PATH = Path.home() / ".config" / "omen-vllm" / "am4-profile"
ACTIVE_CONFIG_PATH = Path.home() / ".config" / "omen-vllm" / "active-configuration"

_CONFIG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _resolve_config_path() -> Path:
    env = os.environ.get("HEARTH_LAB_CONFIGURATIONS")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    if ALT_CONFIG_PATH.is_file():
        return ALT_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def load_lab_configurations() -> dict[str, Any]:
    """Load lab-configurations.toml, cached by mtime."""
    p = _resolve_config_path()
    if not p.is_file():
        return {}
    try:
        mtime = p.stat().st_mtime
        hit = _CONFIG_CACHE.get(str(p))
        if hit and hit[0] == mtime:
            return hit[1]
        with open(p, "rb") as f:
            data = tomllib.load(f).get("configuration", {})
        _CONFIG_CACHE[str(p)] = (mtime, data)
        return data
    except Exception:
        return {}


def get_active_configuration_name() -> str:
    """Return the currently active lab configuration name."""
    env = os.environ.get("HEARTH_LAB_CONFIGURATION")
    if env:
        return env
    if ACTIVE_CONFIG_PATH.is_file():
        try:
            val = ACTIVE_CONFIG_PATH.read_text(encoding="utf-8").strip()
            if val:
                return val
        except OSError:
            pass

    omen_profile = "two-lane"
    if OMEN_PROFILE_PATH.is_file():
        try:
            t = OMEN_PROFILE_PATH.read_text(encoding="utf-8").strip()
            if t:
                omen_profile = t
        except OSError:
            pass

    am4_profile = "tool-pair"   # AM4's resting shape: the CUDA cards serve tool calls and rapid calls
    if AM4_PROFILE_PATH.is_file():
        try:
            t = AM4_PROFILE_PATH.read_text(encoding="utf-8").strip()
            if t:
                am4_profile = t
        except OSError:
            pass

    configs = load_lab_configurations()
    for name, spec in configs.items():
        if spec.get("omen_profile") == omen_profile and spec.get("am4_profile") == am4_profile:
            return name
    return "day"


def get_backend_status(backend_name: str, config_name: Optional[str] = None) -> tuple[str, str]:
    """Return (status, config_name) for a backend under the active or named config."""
    cname = config_name or get_active_configuration_name()
    configs = load_lab_configurations()
    cfg = configs.get(cname, {})
    backends = cfg.get("backends", {})
    if backend_name in backends:
        return backends[backend_name].get("status", "unknown"), cname
    return "unknown", cname


def is_backend_absent(backend_name: str, config_name: Optional[str] = None) -> bool:
    """Return True if backend is explicitly marked absent under configuration."""
    status, _ = get_backend_status(backend_name, config_name)
    return status == "absent"


def is_backend_live(backend_name: str, config_name: Optional[str] = None) -> bool:
    """Return True if backend is explicitly marked live under configuration."""
    status, _ = get_backend_status(backend_name, config_name)
    return status == "live"
