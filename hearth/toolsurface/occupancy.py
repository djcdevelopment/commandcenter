"""Banked Fire — occupancy probe + lease helper (P2 · Yield).

"Mechnet jobs always win" (design principle #4) needs a serve-truth answer to
one question: does a backend's underlying hardware already belong to someone
else? For ``am4-oxen`` the truth is not the conductor's filesystem (a plan can
be queued without a GPU actually being held) — it's the render nodes on AM4
itself: ``/dev/dri/renderD128`` and ``renderD129``. Whoever holds those via
``fuser`` owns the B70s, full stop. That's what ``render_owners`` on the
AM4-MCP surface reports, and it's what this probe replicates over a one-shot
SSH command (we do not speak MCP-over-stdio to AM4 from the gateway; a plain
SSH call emitting the same signal is sufficient and has no extra moving parts).

Fail-open discipline: SSH hiccups happen. A probe failure must never be
confused with "definitely free" (that would let opportunistic traffic land on
a GPU someone else owns) NOR with "definitely busy forever" (that would wedge
legitimate pinned calls). So an unreachable probe reports ``unknown``, and the
caller decides what unknown means for their lane:

  - opportunistic (routed by tag/default, no explicit backend pin): unknown
    treated as BUSY — skip and fall back to omen-ollama. Conservative: never
    guess free on someone else's hardware.
  - pinned (caller explicitly asked for this backend by name): unknown treated
    as AVAILABLE — an operator who named the backend gets what they asked for;
    the router does not second-guess a deliberate choice.

A short TTL cache (default 30s) means a burst of local_generate calls does not
SSH-storm AM4 — one probe per window, shared by every call in it.

The lease helper (``acquire_lease``) is the P5 seam: opportunistic idle-drain
work re-probes on renewal (busy -> lease not renewed -> work re-queues);
synchronous inference calls are short enough to just finish, so their "lease"
is simply probe-before-dispatch, one-shot, no renewal loop. Both paths go
through the same probe + cache so there is exactly one occupancy truth.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

Occupancy = Literal["available", "busy", "unknown"]

SSH_USER_HOST = "derek@am4.tail8e749c.ts.net"
SSH_TIMEOUT_S = 8
CACHE_TTL_S = 30.0

# Same detection rule as am4-fleet-node/scripts/am4-mcp-server.py:render_busy() —
# any of these process names holding a render node means the B70s are owned by
# someone else (image-gen or a manual llama.cpp/oxen run).
_BUSY_PROCESS_NAMES = ("python", "llama", "ComfyUI")

_RENDER_OWNERS_CMD = (
    "for node in /dev/dri/renderD128 /dev/dri/renderD129; do "
    "echo \"--- $node\"; "
    "[ -e \"$node\" ] && fuser -v \"$node\" 2>&1 || true; "
    "done"
)


def _run_ssh(command: str, timeout_s: float,
            runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[Optional[str], Optional[str]]:
    """Run one command on AM4 over SSH. Returns (stdout, error); error is None on success."""
    try:
        completed = runner(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(timeout_s)}",
             SSH_USER_HOST, command],
            capture_output=True, text=True, timeout=timeout_s + 4,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0 and not completed.stdout:
        return None, f"ssh exit {completed.returncode}: {(completed.stderr or '').strip()[:200]}"
    return completed.stdout + completed.stderr, None


def probe_render_owners(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                        timeout_s: float = SSH_TIMEOUT_S) -> dict:
    """One-shot SSH probe of AM4's render-node ownership (the primary occupancy signal).

    Returns {"occupancy": "available"|"busy"|"unknown", "detail": <raw fuser output
    or error>}. Mirrors am4-fleet-node's render_owners/render_busy exactly, so this
    probe and the AM4-MCP surface can never disagree about what "busy" means.
    """
    output, error = _run_ssh(_RENDER_OWNERS_CMD, timeout_s, runner)
    if error is not None:
        return {"occupancy": "unknown", "detail": error}
    busy = "COMMAND" in output and any(name in output for name in _BUSY_PROCESS_NAMES)
    return {"occupancy": "busy" if busy else "available", "detail": output.strip()}


@dataclass
class _CacheEntry:
    result: dict
    expires_at: float


class OccupancyCache:
    """TTL cache over a probe function, keyed by backend name.

    A plain instance-level dict (not a module global) so tests get isolation for
    free; the gateway module holds one process-wide instance (see
    ``default_cache()``).
    """

    def __init__(self, ttl_s: float = CACHE_TTL_S,
                probe: Callable[[], dict] = probe_render_owners,
                clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self._probe = probe
        self._clock = clock
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str, probe: Optional[Callable[[], dict]] = None) -> dict:
        """Return the cached probe result for `key`, refreshing if stale/absent."""
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > now:
            return {**entry.result, "cached": True}
        result = (probe or self._probe)()
        self._entries[key] = _CacheEntry(result=result, expires_at=now + self.ttl_s)
        return {**result, "cached": False}

    def invalidate(self, key: Optional[str] = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)


_default_cache: Optional[OccupancyCache] = None


def default_cache() -> OccupancyCache:
    """The process-wide occupancy cache (lazily created)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = OccupancyCache()
    return _default_cache


def check_occupancy(backend_name: str, cache: Optional[OccupancyCache] = None,
                    probe: Optional[Callable[[], dict]] = None) -> dict:
    """Cached occupancy check for a named backend. Only ``am4-oxen`` has a live
    probe today; any other backend name reports "available" (no probe declared —
    nothing is known to contend for it), so the skip-busy logic only ever engages
    where a real occupancy signal exists."""
    if backend_name != "am4-oxen":
        return {"occupancy": "available", "detail": "no occupancy probe declared for this backend"}
    return (cache or default_cache()).get(backend_name, probe=probe)


def resolve_for_lane(occupancy: Occupancy, *, pinned: bool) -> bool:
    """True if the backend should be treated as USABLE right now.

    Fail-open policy (module docstring): "unknown" resolves to busy for
    opportunistic (unpinned) calls and to available for pinned calls.
    """
    if occupancy == "available":
        return True
    if occupancy == "busy":
        return False
    # occupancy == "unknown"
    return pinned


@dataclass
class Lease:
    """A short-lived hold used by opportunistic work (P5 seam).

    Inference calls (P2/current use) are a degenerate one-shot lease: acquire,
    use immediately, never renew. Idle-drain tasks (P5) hold a lease across a
    longer job and call `renew()` periodically; a renewal re-probes and returns
    False the moment the backend becomes busy, so the caller can yield without
    a kill signal (design principle #4: "stops the moment he is" is a lease
    policy, not a kill).
    """
    backend_name: str
    granted: bool
    occupancy_at_grant: Occupancy
    _cache: OccupancyCache = field(repr=False)

    def renew(self) -> bool:
        """Re-probe (bypassing the cache, since a lease renewal wants fresh
        truth) and return whether the lease still holds."""
        self._cache.invalidate(self.backend_name)
        result = check_occupancy(self.backend_name, cache=self._cache)
        self.occupancy_at_grant = result["occupancy"]
        self.granted = resolve_for_lane(result["occupancy"], pinned=False)
        return self.granted


def acquire_lease(backend_name: str, *, pinned: bool = False,
                  cache: Optional[OccupancyCache] = None) -> Lease:
    """Probe once (cached) and grant/refuse a lease for `backend_name`.

    For the inference lane this is the whole lease: probe-before-dispatch, use
    the grant immediately, done. P5's idle-drain lane reuses the same helper
    but keeps the returned Lease around and calls `renew()` on its own cadence.
    """
    active_cache = cache or default_cache()
    result = check_occupancy(backend_name, cache=active_cache)
    granted = resolve_for_lane(result["occupancy"], pinned=pinned)
    return Lease(backend_name=backend_name, granted=granted,
                occupancy_at_grant=result["occupancy"], _cache=active_cache)


# No get_tools() here: occupancy is a support module consulted by
# inference.py's routing internals (and, later, P5's idle-drain scheduler),
# not its own caller-facing HEARTH tool. It is deliberately NOT a provider
# module (the gateway --providers list never names hearth.toolsurface.occupancy).
