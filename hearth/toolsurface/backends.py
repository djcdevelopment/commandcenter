"""Banked Fire — HEARTH inference backend pool + routing (P1).

The declarative pool lives in ``hearth/etc/backends.toml`` (override with the
HEARTH_BACKENDS env var). This module turns it into ``Backend`` records and picks
one per call. Pure stdlib, no network, no gateway import — so it unit-tests
offline exactly like the rest of the toolsurface.

Routing is confined to *synchronous inference* calls (Banked Fire design
principle #1: HEARTH routes inference, the conductor owns queued work). Policy,
in order:

  1. caller-pinned ``endpoint``  -> legacy behavior, handled by the caller, not here
  2. caller-pinned ``backend``   name -> exact match or error
  3. ``task``/``tags`` tag match -> first backend carrying a matching tag
  4. (occupancy skip -- P2, not yet consulted here)
  5. fall back to the pool's ``default`` backend

``select_backend`` returns ``(Backend, reason)`` so the router can ledger *why*
a backend was chosen — every dispatch is an assay observation (principle #6).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Pool file: HEARTH_BACKENDS wins; else the etc/ default beside this package.
ENV_VAR = "HEARTH_BACKENDS"
DEFAULT_POOL_PATH = Path(__file__).resolve().parents[1] / "etc" / "backends.toml"

# Built-in fallback so a missing pool file never breaks the gateway: the default
# local Ollama backend, identical to inference.py's historical behavior.
_FALLBACK_DEFAULT = "omen-ollama"
_FALLBACK_BACKENDS = [
    {
        "name": _FALLBACK_DEFAULT,
        "endpoint": "http://127.0.0.1:11434",
        "api": "ollama",
        "models": ["qwen3-coder:30b"],
        "tags": ["default", "code"],
    }
]

_VALID_APIS = ("ollama", "openai")


class BackendConfigError(ValueError):
    """Raised when the pool declaration is structurally invalid."""


@dataclass(frozen=True)
class Backend:
    """One declared inference backend."""
    name: str
    endpoint: str
    api: str
    models: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    auth_env: Optional[str] = None
    revive: Optional[str] = None
    occupancy: dict = field(default_factory=dict)

    def token(self) -> Optional[str]:
        """The bearer token for this backend from its ``auth_env`` var, or None.

        Missing/blank is returned as None rather than raised — an openai backend
        with no token yields a clean ok:false result, not a 401 surprise.
        """
        if not self.auth_env:
            return None
        value = os.environ.get(self.auth_env)
        return value if value and value.strip() else None


@dataclass(frozen=True)
class Pool:
    """The resolved backend pool: backends plus the name of the default."""
    backends: tuple[Backend, ...]
    default: str

    def by_name(self, name: str) -> Optional[Backend]:
        for backend in self.backends:
            if backend.name == name:
                return backend
        return None

    def by_endpoint(self, endpoint: str) -> Optional[Backend]:
        """Match a declared backend by endpoint (trailing slash-insensitive)."""
        needle = endpoint.rstrip("/")
        for backend in self.backends:
            if backend.endpoint.rstrip("/") == needle:
                return backend
        return None

    def default_backend(self) -> Backend:
        backend = self.by_name(self.default)
        if backend is None:  # malformed default -> fall to the first declared
            return self.backends[0]
        return backend


def _coerce_backend(raw: dict) -> Backend:
    if not isinstance(raw, dict):
        raise BackendConfigError("each [[backend]] must be a table")
    name = raw.get("name")
    endpoint = raw.get("endpoint")
    api = raw.get("api")
    if not isinstance(name, str) or not name.strip():
        raise BackendConfigError("backend.name must be a non-empty string")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BackendConfigError(f"backend {name!r}: endpoint must be a non-empty string")
    if api not in _VALID_APIS:
        raise BackendConfigError(
            f"backend {name!r}: api must be one of {_VALID_APIS}, got {api!r}")
    return Backend(
        name=name,
        endpoint=endpoint,
        api=api,
        models=tuple(raw.get("models") or ()),
        tags=tuple(raw.get("tags") or ()),
        auth_env=raw.get("auth_env"),
        revive=raw.get("revive"),
        occupancy=dict(raw.get("occupancy") or {}),
    )


def load_pool(path: Optional[Path | str] = None) -> Pool:
    """Load the backend pool. HEARTH_BACKENDS > `path` arg > packaged default.

    A missing file is not an error: the built-in omen-ollama fallback is used so
    the gateway keeps serving local inference exactly as before Banked Fire.
    """
    resolved = Path(path) if path else Path(os.environ.get(ENV_VAR, DEFAULT_POOL_PATH))
    if not resolved.is_file():
        raw_backends, default = _FALLBACK_BACKENDS, _FALLBACK_DEFAULT
    else:
        with open(resolved, "rb") as fh:
            data = tomllib.load(fh)
        raw_backends = data.get("backend") or []
        default = data.get("default")
        if not raw_backends:
            raise BackendConfigError(f"{resolved}: no [[backend]] entries declared")

    backends = tuple(_coerce_backend(raw) for raw in raw_backends)
    names = [b.name for b in backends]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise BackendConfigError(f"duplicate backend name(s): {', '.join(dupes)}")
    if not default:
        default = names[0]
    if default not in names:
        raise BackendConfigError(
            f"default {default!r} names no declared backend (have: {', '.join(names)})")
    return Pool(backends=backends, default=default)


def select_backend(pool: Pool, *, backend: Optional[str] = None,
                   task: Optional[str] = None,
                   tags: Optional[list[str]] = None) -> tuple[Backend, str]:
    """Pick a backend and return (backend, reason).

    `backend` pins by name (error if unknown). Otherwise `task` and any `tags`
    are matched against each backend's declared tags, first match wins. With no
    signal, the pool default is returned. (Occupancy is a P2 concern and is not
    consulted here.)
    """
    if backend is not None:
        chosen = pool.by_name(backend)
        if chosen is None:
            raise BackendConfigError(
                f"no backend named {backend!r} (have: "
                f"{', '.join(b.name for b in pool.backends)})")
        return chosen, f"pinned:{backend}"

    wanted: list[str] = []
    if task:
        wanted.append(task)
    if tags:
        wanted.extend(tags)
    for tag in wanted:
        for candidate in pool.backends:
            if tag in candidate.tags:
                return candidate, f"tag:{tag}"

    return pool.default_backend(), "default"
