"""Banked Fire — HEARTH inference backend pool + routing (P1 + P2 occupancy).

The declarative pool lives in ``hearth/etc/backends.toml`` (override with the
HEARTH_BACKENDS env var). This module turns it into ``Backend`` records and picks
one per call. Pure stdlib, no network, no gateway import — so it unit-tests
offline exactly like the rest of the toolsurface. (The occupancy *probe* lives
in ``hearth.toolsurface.occupancy``, which does SSH; it is injected here as a
callable so this module never imports it directly.)

Routing is confined to *synchronous inference* calls (Banked Fire design
principle #1: HEARTH routes inference, the conductor owns queued work). Policy,
in order:

  1. caller-pinned ``endpoint``  -> legacy behavior, handled by the caller, not here
  2. caller-pinned ``backend``   name -> exact match or error; ordinary busy/unknown
     remains fail-open, but an active exclusive GPU-pool fence is always refused,
     but still refused if the payload exceeds the rung's ``context_bytes`` (ADR-0031)
  3. ``task``/``tags`` tag match -> first candidate whose occupancy is NOT busy
     (busy or unknown -> skip, try the next tag/candidate)
  4. fall back to the pool's ``default`` backend (never itself occupancy-gated)

``select_backend`` returns ``(Backend, reason, occupancy)`` so the router can
ledger *why* a backend was chosen and what its occupancy looked like at decision
time — every dispatch is an assay observation (principle #6).
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

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

_VALID_APIS = ("ollama", "openai", "gemini")


class BackendConfigError(ValueError):
    """Raised when the pool declaration is structurally invalid."""


class BackendRoutingRefusal(BackendConfigError):
    """A request was refused because no safe backend qualified.

    ``reason_code`` names which way: the class default is the ladder's (nothing in
    the pool could take the payload); a caller-pinned rung that cannot take it
    raises ``payload_over_budget_for_pinned_backend`` with a single ``attempted``
    row flagged ``pinned``. Both share the ``payload_over_budget`` prefix that
    ``hearth.errortax`` classifies on — keep any new code inside that family.
    """

    reason_code = "payload_over_budget_no_eligible_backend"

    def __init__(self, *, payload_bytes: int, required_context_bytes: int,
                 attempted: list[dict], default_backend: str,
                 default_context_bytes: Optional[int],
                 reason_code: Optional[str] = None) -> None:
        self.reason_code = reason_code or type(self).reason_code
        self.payload_bytes = payload_bytes
        self.required_context_bytes = required_context_bytes
        self.attempted = attempted
        self.default_backend = default_backend
        self.default_context_bytes = default_context_bytes
        super().__init__(self.message())

    def message(self) -> str:
        """A one-line refusal that carries its numbers.

        This is ``str(exc)``, so any boundary that flattens the exception —
        ExecutionService admission, a bare log line — reports the payload and
        every rung the router weighed without re-deriving them. Keeping it here
        rather than at each boundary means one place knows the ``attempted`` row
        shape, next to the code that builds those rows.
        """
        rungs = ", ".join(
            f"{row['name']} ({row['context_bytes']} B, {row['rejection_reason']})"
            for row in self.attempted
        )
        return f"{self.reason_code}: payload is {self.payload_bytes} bytes; rejected {rungs}"

    def as_dict(self) -> dict:
        return {
            "reason": self.reason_code,
            "payload_bytes": self.payload_bytes,
            "required_context_bytes": self.required_context_bytes,
            "default_backend": self.default_backend,
            "default_context_bytes": self.default_context_bytes,
            "attempted": self.attempted,
        }


@dataclass(frozen=True)
class Backend:
    """One declared inference backend.

    ``retired`` marks a rung whose hardware is gone (a tombstone). The entry
    stays in the pool so its history, ``revive`` wiring, and declared budgets
    remain readable, and so a pin still fails with a specific error rather than
    "unknown backend" — but it is withheld from outward-facing projections like
    ``list_execution_providers``. Declaring capacity that cannot emit a token is
    worse than declaring none: the IRC storefront advertised three dead rungs as
    real hardware for four days after the 2026-08-20 rebuild.
    """
    name: str
    endpoint: str
    api: str
    models: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    auth_env: Optional[str] = None
    revive: Optional[str] = None
    retired: bool = False
    occupancy: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    def cost_class(self) -> Optional[str]:
        """The declared cost_class setting (e.g. "trial"), or None."""
        val = self.settings.get("cost_class")
        return str(val) if val is not None else None

    def context_bytes(self) -> Optional[int]:
        """The declared context_bytes setting as a positive int, or None (unlimited)."""
        val = self.settings.get("context_bytes")
        if val is not None:
            try:
                ival = int(val)
                if ival > 0:
                    return ival
            except (TypeError, ValueError):
                pass
        return None

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
    trial: dict = field(default_factory=dict)

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

    def by_model(self, model: str) -> tuple[Backend, ...]:
        """Return every declared provider for an exact model identifier."""
        return tuple(backend for backend in self.backends if model in backend.models)

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
        retired=bool(raw.get("retired", False)),
        occupancy=dict(raw.get("occupancy") or {}),
        settings=dict(raw.get("settings") or {}),
    )


def load_pool(path: Optional[Path | str] = None) -> Pool:
    """Load the backend pool. HEARTH_BACKENDS > `path` arg > packaged default.

    A missing file is not an error: the built-in omen-ollama fallback is used so
    the gateway keeps serving local inference exactly as before Banked Fire.
    """
    resolved = Path(path) if path else Path(os.environ.get(ENV_VAR, DEFAULT_POOL_PATH))
    if not resolved.is_file():
        raw_backends, default, trial = _FALLBACK_BACKENDS, _FALLBACK_DEFAULT, {}
    else:
        with open(resolved, "rb") as fh:
            data = tomllib.load(fh)
        raw_backends = data.get("backend") or []
        default = data.get("default")
        trial = dict(data.get("trial") or {})
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
    return Pool(backends=backends, default=default, trial=trial)


# P8 dispatch stamp: identity of the pool declaration a call was routed under.
# Keyed by resolved path -> (mtime_ns, size, sha256[:12]); the file is re-read
# only when its stat changes, so the hot path costs one stat per dispatch.
_POOL_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
POOL_HASH_CHARS = 12


def pool_config_hash(path: Optional[Path | str] = None) -> Optional[str]:
    """sha256[:12] of the pool TOML bytes, or None when no pool file exists.

    Resolves the file exactly as ``load_pool`` does (HEARTH_BACKENDS > ``path`` >
    packaged default) and hashes the BYTES on disk, not the parsed pool, so a
    comment-only edit still changes the stamp — a ledger row must be able to
    say precisely which declaration routed it. Cached by (mtime_ns, size).
    Never raises: a stamp is provenance, and provenance must not be able to
    break a dispatch.
    """
    try:
        resolved = Path(path) if path else Path(os.environ.get(ENV_VAR, DEFAULT_POOL_PATH))
        stat = resolved.stat()
    except (OSError, TypeError, ValueError):
        return None
    key = str(resolved)
    cached = _POOL_HASH_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()[:POOL_HASH_CHARS]
    except OSError:
        return None
    _POOL_HASH_CACHE[key] = (stat.st_mtime_ns, stat.st_size, digest)
    return digest


def select_backend(pool: Pool, *, backend: Optional[str] = None,
                   model: Optional[str] = None,
                   task: Optional[str] = None,
                   tags: Optional[list[str]] = None,
                   occupancy_check: Optional[Callable[[str], dict]] = None,
                   payload_bytes: Optional[int] = None,
                   exclude: Optional[set[str]] = None) -> tuple[Backend, str, dict]:
    """Pick a backend and return (backend, reason, occupancy).

    `backend` pins by name (error if unknown) — a pin is a deliberate operator
    choice and is not skipped for ordinary occupancy (Banked Fire P2 fail-open
    policy: unknown/busy occupancy on a pinned call still routes there; the caller
    asked for it by name). An exclusive GPU tenancy fence is stronger than a pin
    and is refused. A pin IS also refused when `payload_bytes` exceeds the
    pinned rung's declared `context_bytes` — that raises `BackendRoutingRefusal`
    with reason `payload_over_budget_for_pinned_backend`. A pin overrides
    scheduling judgment, not arithmetic: a busy rung eventually serves you from
    its own queue, whereas an over-budget payload never fits. Callers that pass
    no `payload_bytes` keep the historical unconditional behavior. Otherwise
    `task` and any `tags` are matched against each
    backend's declared tags in order; a tag match whose backend is BUSY is
    skipped and the next candidate (then the pool default) is tried instead —
    this is the P2 skip-busy behavior. With no signal, the pool default is
    returned unconditionally (the default is the safe fallback, never itself
    occupancy-gated).

    `occupancy_check(name) -> {"occupancy": "available"|"busy"|"unknown", ...}`
    is injected so this module stays pure/offline (no SSH import here); pass
    `hearth.toolsurface.occupancy.check_occupancy` in production. Omitted, every
    backend is treated as available (P1 behavior, unchanged).
    """
    def _occ(name: str) -> dict:
        if occupancy_check is None:
            return {"occupancy": "available"}
        checked = occupancy_check(name)
        if not isinstance(checked, dict):
            return {"occupancy": "unknown"}
        result = dict(checked)
        if result.get("occupancy") not in {"available", "busy", "unknown"}:
            result["occupancy"] = "unknown"
        return result

    if backend is not None:
        chosen = pool.by_name(backend)
        if chosen is None:
            raise BackendConfigError(
                f"no backend named {backend!r} (have: "
                f"{', '.join(b.name for b in pool.backends)})")
        if model is not None and model not in chosen.models:
            raise BackendConfigError(
                f"backend {backend!r} does not provide model {model!r} "
                f"(provides: {', '.join(chosen.models) or 'none'})"
            )
        if payload_bytes is not None:
            # Decided BEFORE _occ() so an unreachable rung costs no probe to
            # refuse. Refusing here is what attributes the failure to the door
            # rather than letting it surface as a server fault — which is how the
            # am4-oxen ctx miscalculation (0eeb1df) stayed hidden for a month.
            # Callers that pass no payload_bytes (build_requests) are untouched.
            pinned_context = chosen.context_bytes()
            if pinned_context is not None and payload_bytes > pinned_context:
                raise BackendRoutingRefusal(
                    payload_bytes=payload_bytes,
                    required_context_bytes=payload_bytes,
                    attempted=[{
                        "name": chosen.name,
                        "context_bytes": pinned_context,
                        "occupancy": "not_checked",
                        "rejection_reason": "payload_over_budget",
                        "pinned": True,
                    }],
                    default_backend=pool.default,
                    default_context_bytes=pool.default_backend().context_bytes(),
                    reason_code="payload_over_budget_for_pinned_backend",
                )
        occ = _occ(chosen.name)
        if occ.get("exclusive"):
            raise BackendConfigError(
                "backend %r unavailable during exclusive GPU tenancy [%s]: %s" %
                (chosen.name, occ.get("exclusive_reason") or "unspecified",
                 occ.get("detail") or "resource owned elsewhere")
            )
        occ["occupancy"] = occ.get("occupancy", "unknown")
        # Pinned calls resolve unknown -> available (fail-open for a deliberate pin).
        return chosen, f"pinned:{backend}", occ

    if model is not None:
        candidates = pool.by_model(model)
        if not candidates:
            declared = sorted({item for candidate in pool.backends for item in candidate.models})
            raise BackendConfigError(
                f"no provider declares model {model!r} "
                f"(have: {', '.join(declared) or 'none'})"
            )
        for candidate in candidates:
            if exclude and candidate.name in exclude:
                continue
            if payload_bytes is not None:
                context = candidate.context_bytes()
                if context is not None and payload_bytes > context:
                    continue
            occupancy = _occ(candidate.name)
            if occupancy.get("occupancy") == "available":
                return candidate, f"model:{model}", occupancy
        raise BackendRoutingRefusal(
            payload_bytes=payload_bytes or 0,
            required_context_bytes=payload_bytes or 0,
            attempted=[
                {
                    "name": candidate.name,
                    "context_bytes": candidate.context_bytes(),
                    "occupancy": _occ(candidate.name).get("occupancy", "unknown"),
                    "rejection_reason": "provider_unavailable",
                }
                for candidate in candidates
            ],
            default_backend=pool.default,
            default_context_bytes=pool.default_backend().context_bytes(),
        )

    wanted: list[str] = []
    if task:
        wanted.append(task)
    if tags:
        wanted.extend(tags)
    for tag in wanted:
        for candidate in pool.backends:
            if tag in candidate.tags:
                if exclude and candidate.name in exclude:
                    continue
                if payload_bytes is not None:
                    c_bytes = candidate.context_bytes()
                    if c_bytes is not None and payload_bytes > c_bytes:
                        # A1: the payload cannot fit this rung's declared context —
                        # skip it exactly like a busy candidate.
                        continue
                occ = _occ(candidate.name)
                occupancy = occ.get("occupancy", "unknown")
                if occupancy == "busy" or (occupancy == "unknown"):
                    # Opportunistic (tag-routed) call: unknown resolves to busy —
                    # skip this candidate and keep looking, same as a hard busy.
                    continue
                return candidate, f"tag:{tag}", occ

    default = pool.default_backend()
    d_exclude = bool(exclude and default.name in exclude)
    d_overflow = False
    if payload_bytes is not None:
        c_bytes = default.context_bytes()
        if c_bytes is not None and payload_bytes > c_bytes:
            d_overflow = True

    if d_exclude or d_overflow:
        # A1/A2: the default can't take this call (payload too big, or it just
        # failed and is excluded). Walk the ladder: big-context first (sunk),
        # then cloud-overflow (trial). No qualifying rung is a hard refusal.
        attempted: list[dict] = [{
            "name": default.name,
            "context_bytes": default.context_bytes(),
            "occupancy": "not_checked",
            "rejection_reason": "excluded" if d_exclude and not d_overflow else "payload_over_budget",
        }]
        for f_tag in ("big-context", "cloud-overflow"):
            for candidate in pool.backends:
                if f_tag in candidate.tags:
                    if exclude and candidate.name in exclude:
                        continue
                    if payload_bytes is not None:
                        c_bytes = candidate.context_bytes()
                        if c_bytes is not None and payload_bytes > c_bytes:
                            attempted.append({
                                "name": candidate.name,
                                "context_bytes": c_bytes,
                                "occupancy": "not_checked",
                                "rejection_reason": "payload_over_budget",
                                "ladder": f_tag,
                            })
                            continue
                    occ = _occ(candidate.name)
                    occupancy = occ.get("occupancy", "unknown")
                    if occupancy == "busy" or occupancy == "unknown":
                        attempted.append({
                            "name": candidate.name,
                            "context_bytes": candidate.context_bytes(),
                            "occupancy": occupancy,
                            "rejection_reason": "occupancy_unavailable",
                            "ladder": f_tag,
                        })
                        continue
                    reason_prefix = "fallback" if d_exclude else "payload"
                    return candidate, f"{reason_prefix}:{f_tag}:{candidate.name}", occ
        raise BackendRoutingRefusal(
            payload_bytes=payload_bytes if payload_bytes is not None else 0,
            required_context_bytes=payload_bytes if payload_bytes is not None else 0,
            attempted=attempted,
            default_backend=default.name,
            default_context_bytes=default.context_bytes(),
        )

    return default, "default", _occ(default.name)
