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

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

Occupancy = Literal["available", "busy", "unknown"]

SSH_USER_HOST = "derek@192.168.12.233"  # LAN, not tailnet (ADR-0014)
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


# --- am4-moe: HTTP slot/KV goodput probe -----------------------------------
# The resident gpt-oss-120b llama-server holds BOTH render nodes by design, so
# the fuser probe above would read it permanently busy. Its real occupancy
# signal is llama-server's own /slots endpoint: how many parallel slots are
# processing, and (best-effort, field names vary by build) how full the KV
# cache is. Goodput policy: saturated slots OR KV past the pressure ceiling
# reports "busy" so opportunistic traffic steers away; pinned calls still
# dispatch and wait in llama-server's internal request queue.

MOE_SLOTS_URL = "http://192.168.12.233:8082/slots"  # LAN, not tailnet (ADR-0014)
MOE_TOKEN_ENV = "AM4_OXEN_TOKEN"  # llama-server --api-key uses the same bearer
MOE_HTTP_TIMEOUT_S = 4.0
MOE_KV_PRESSURE_MAX = 0.90


def _http_get_json(url: str, timeout_s: float) -> tuple[Optional[object], Optional[str]]:
    """GET `url` and parse JSON. Returns (data, error); error is None on success.

    Sends the moe bearer token when the env var is present (the /slots endpoint
    sits behind llama-server's --api-key along with the inference routes).
    """
    request = urllib.request.Request(url)
    token = os.environ.get(MOE_TOKEN_ENV)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _slot_is_processing(slot: dict) -> bool:
    # Recent llama-server builds report `is_processing`; older ones a numeric
    # `state` where 0 means idle. Default to idle when neither field exists.
    if "is_processing" in slot:
        return bool(slot["is_processing"])
    return bool(slot.get("state", 0))


def probe_moe_slots(fetch: Callable[[str, float], tuple[Optional[object], Optional[str]]] = _http_get_json,
                    timeout_s: float = MOE_HTTP_TIMEOUT_S) -> dict:
    """Probe the resident moe llama-server's slot/KV state over HTTP.

    Returns {"occupancy": ..., "detail": {...}} where detail carries the goodput
    signal (slots_total/slots_busy/slots_idle, kv_used_frac when the build
    exposes per-slot n_past/n_ctx). Fail-open discipline matches the SSH probe:
    unreachable, still-loading (HTTP 503), or unparseable all report "unknown" —
    opportunistic traffic skips, a pin proceeds.
    """
    data, error = fetch(MOE_SLOTS_URL, timeout_s)
    if error is not None:
        return {"occupancy": "unknown", "detail": error}
    if not isinstance(data, list) or not data:
        return {"occupancy": "unknown", "detail": f"unexpected /slots shape: {type(data).__name__}"}

    slots_total = len(data)
    slots_busy = sum(1 for slot in data if isinstance(slot, dict) and _slot_is_processing(slot))
    kv_used = kv_capacity = 0
    for slot in data:
        if isinstance(slot, dict):
            used, cap = slot.get("n_past"), slot.get("n_ctx")
            if isinstance(used, int) and isinstance(cap, int) and cap > 0:
                kv_used += used
                kv_capacity += cap
    kv_used_frac = round(kv_used / kv_capacity, 4) if kv_capacity else None

    saturated = slots_busy >= slots_total
    kv_pressure = kv_used_frac is not None and kv_used_frac > MOE_KV_PRESSURE_MAX
    detail = {
        "slots_total": slots_total,
        "slots_busy": slots_busy,
        "slots_idle": slots_total - slots_busy,
        "kv_used_frac": kv_used_frac,
    }
    return {"occupancy": "busy" if (saturated or kv_pressure) else "available", "detail": detail}


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


# Which backends have a live occupancy probe, and which. Everything else
# reports "available" (no probe declared — nothing is known to contend for it),
# so the skip-busy logic only ever engages where a real occupancy signal exists.
_PROBES: dict[str, Callable[[], dict]] = {
    "am4-oxen": probe_render_owners,   # SSH render-node fuser (someone else's GPU?)
    "am4-moe": probe_moe_slots,        # HTTP slot/KV goodput on the resident server
}


def check_occupancy(backend_name: str, cache: Optional[OccupancyCache] = None,
                    probe: Optional[Callable[[], dict]] = None) -> dict:
    """Cached occupancy check for a named backend, via the ``_PROBES`` registry.

    Probe precedence: an explicit ``probe`` arg wins; otherwise an injected
    ``cache`` keeps its own probe (the test/Lease.renew contract — never
    override a caller-owned cache); only the default-cache path resolves the
    backend's declared registry probe.
    """
    declared = _PROBES.get(backend_name)
    if probe is None and declared is None:
        return {"occupancy": "available", "detail": "no occupancy probe declared for this backend"}
    if probe is None and cache is None:
        probe = declared
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
