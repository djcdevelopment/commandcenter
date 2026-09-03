"""Banked Fire — occupancy probe + lease helper (P2 · Yield).

"Mechnet jobs always win" (design principle #4) needs a serve-truth answer to
one question: can this backend take work right now, or is its hardware already
spoken for? The answer is never the conductor's filesystem (a plan can be
queued without a GPU actually being held) — it is always AM4 itself. But there
is no single signal that is honest for every rung, so each one declares its own
in the ``_PROBES`` registry at the bottom of this module:

  - ``am4-moe``  -> llama-server's own /slots (goodput: busy slots + KV pressure)
  - ``am4-oxen`` -> the :8090 facade's /health (is a model loaded behind it at all?)

``probe_render_owners`` — ``fuser`` on ``/dev/dri/renderD128``/``129`` over one
SSH call, mirroring ``render_owners`` on the AM4-MCP surface — is deliberately
NOT wired to either rung as of 2026-07-30. Both are served by a resident
llama-server that holds both render nodes by design, so for them "someone holds
a render node" is true forever and the probe reports busy forever
(B70-VERTICAL-TRACE.html). It is kept because it is still the right question for
a tenant that cannot share the cards at all (imagegen/ComfyUI), and it is the
only probe that sees a NON-HTTP holder of the GPUs.

Fail-open discipline: probes fail — SSH hiccups, a facade restart, a model
mid-load. A probe failure must never be confused with "definitely free" (that
would let opportunistic traffic land on a GPU someone else owns) NOR with
"definitely busy forever" (that would wedge legitimate pinned calls). So a
probe that cannot get a definite reading reports ``unknown``, and the caller
decides what unknown means for their lane:

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

MOE_SLOTS_URL = "http://192.168.12.233:8082/slots"  # LAN, not tailnet (ADR-0014); ☠ am4 tombstone
AM4_TOKEN_ENV = "AM4_OXEN_TOKEN"  # both AM4 rungs share this bearer name (backends.toml auth_env)
OMEN_ARC_SLOTS_URL = "http://127.0.0.1:8082/slots"  # the resident omen-arc llama-server (ADR-0034)
OMEN_ARC_TOKEN_ENV = "OMEN_ARC_TOKEN"
MOE_HTTP_TIMEOUT_S = 4.0
MOE_KV_PRESSURE_MAX = 0.90


def _http_get_json(url: str, timeout_s: float,
                   token_env: str = AM4_TOKEN_ENV) -> tuple[Optional[object], Optional[str]]:
    """GET `url` and parse JSON. Returns (data, error); error is None on success.

    Sends the named bearer token when the env var is present: llama-server's /slots
    sits behind --api-key along with the inference routes. (The oxen facade
    leaves /health open, so the header is merely harmless there.)
    """
    request = urllib.request.Request(url)
    token = os.environ.get(token_env)
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
                    timeout_s: float = MOE_HTTP_TIMEOUT_S,
                    slots_url: str = MOE_SLOTS_URL) -> dict:
    """Probe a llama-server's slot/KV state over HTTP (the goodput probe).

    Returns {"occupancy": ..., "detail": {...}} where detail carries the goodput
    signal (slots_total/slots_busy/slots_idle, kv_used_frac when the build
    exposes per-slot n_past/n_ctx). Fail-open discipline matches the SSH probe:
    unreachable, still-loading (HTTP 503), or unparseable all report "unknown" —
    opportunistic traffic skips, a pin proceeds.

    Written for the am4-moe rung; kept name and defaults for the historical
    tests, parametrized so omen-arc reuses the same body (ADR-0034).
    """
    data, error = fetch(slots_url, timeout_s)
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


# --- am4-oxen: facade health probe ------------------------------------------
# The :8090 facade proxies to whichever llama-server is resident on
# 127.0.0.1:8080, and that server holds the render nodes, so the fuser probe is
# blind here (see module docstring). What the facade CAN answer is whether a
# model is actually loaded and serving behind it — the failure this rung really
# suffers, per the 2026-07-30 trace: the port answers during a crash loop while
# nothing can serve a request.
#
# Read `backend.ok`, never the top-level "status": the facade hardcodes
# {"status": "ok"} whenever the facade process itself is up, so top-level status
# stays "ok" with a dead model behind it. The honest field is:
#   {"backend": {"ok": true,  "status": 200, "body": {"status": "ok"}}}   ready
#   {"backend": {"ok": false, "status": 503, "body": {...}}}              loading
#   {"backend": {"ok": false, "error": "<connect error>"}}                gone
#
# Note this is a READINESS signal, not a contention one: the planner runs
# -np 1, so a second concurrent call queues behind the first rather than being
# refused, and this probe still reads "available". That is accepted — it is
# strictly better than the fuser probe's permanent "busy", and pinned calls
# (this rung is pin-only) bypass occupancy anyway.

OXEN_HEALTH_URL = "http://192.168.12.233:8090/health"  # LAN, not tailnet (ADR-0014)
OXEN_HTTP_TIMEOUT_S = 4.0


def probe_oxen_facade(fetch: Callable[[str, float], tuple[Optional[object], Optional[str]]] = _http_get_json,
                      timeout_s: float = OXEN_HTTP_TIMEOUT_S) -> dict:
    """Probe the am4-oxen facade's health endpoint over HTTP.

    Returns {"occupancy": ..., "detail": {...}} where detail carries the
    readiness signal (facade state, backend_ok/backend_status, and which
    backend URL the facade is fronting). Fail-open discipline matches the other
    probes: an unreachable facade, an unparseable payload, or a backend that is
    loading/absent all report "unknown" — opportunistic traffic skips, a pin
    proceeds. A not-ready backend is deliberately NOT "busy": we cannot tell a
    model mid-load from a card someone else took, and "busy" would refuse even a
    deliberate operator lease.
    """
    data, error = fetch(OXEN_HEALTH_URL, timeout_s)
    if error is not None:
        return {"occupancy": "unknown", "detail": error}
    if not isinstance(data, dict):
        return {"occupancy": "unknown", "detail": f"unexpected /health shape: {type(data).__name__}"}
    backend = data.get("backend")
    if not isinstance(backend, dict):
        return {"occupancy": "unknown", "detail": "no backend readiness in /health payload"}

    detail = {
        "facade": data.get("facade"),
        "backend_ok": bool(backend.get("ok")),
        "backend_status": backend.get("status"),
        "backend_base_url": data.get("backend_base_url"),
    }
    if not backend.get("ok"):
        detail["reason"] = backend.get("error") or backend.get("body")
        return {"occupancy": "unknown", "detail": detail}
    return {"occupancy": "available", "detail": detail}


@dataclass
class _CacheEntry:
    result: dict
    expires_at: float


class OccupancyCache:
    """TTL cache over a probe function, keyed by backend name.

    A plain instance-level dict (not a module global) so tests get isolation for
    free; the gateway module holds one process-wide instance (see
    ``default_cache()``).

    ``probe`` is optional: with none installed, each key resolves the probe
    declared for that backend in ``_PROBES``. That default used to be
    ``probe_render_owners`` for every key, which meant any cache-carrying path
    (``acquire_lease``, ``Lease.renew``) silently probed AM4's render nodes no
    matter which backend it was asked about.
    """

    def __init__(self, ttl_s: float = CACHE_TTL_S,
                probe: Optional[Callable[[], dict]] = None,
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
        result = (probe or self._probe or _registry_probe(key))()
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


_NO_PROBE_DETAIL = "no occupancy probe declared for this backend"

# Which backends have a live occupancy probe, and which one. Everything else
# reports "available" (no probe declared — nothing is known to contend for it),
# so the skip-busy logic only ever engages where a real occupancy signal exists.
# Each entry must be a probe that can actually go BOTH ways for that rung: a
# signal that is pinned to one answer by the rung's own design is not a probe,
# it is decoration on every ledger event (which is what "am4-oxen" ->
# probe_render_owners had become — see the module docstring).
def probe_omen_arc_slots() -> dict:
    """Slot/KV goodput probe for the resident omen-arc llama-server (ADR-0034).

    Same body as the moe probe, pointed at loopback :8082 with the OMEN token.
    """
    # Art mode is a hard tenancy boundary, not ordinary goodput occupancy. It
    # also applies to explicitly pinned calls: the server is being drained or
    # stopped and must not receive a late request through the normal pin bypass.
    try:
        from hearth.execution.coordination import GpuTenancyStore

        session = GpuTenancyStore().active_image_session("omen-b70-pool")
    except Exception as exc:
        return {"occupancy": "unknown", "detail": "tenancy probe failed: %s" % exc,
                "exclusive": True}
    if session is not None:
        return {
            "occupancy": "busy", "exclusive": True,
            "detail": "B70 pool owned by imagegen session %s epoch %d (%s)" % (
                session.session_id, session.epoch, session.state),
        }
    return probe_moe_slots(
        fetch=lambda url, t: _http_get_json(url, t, token_env=OMEN_ARC_TOKEN_ENV),
        slots_url=OMEN_ARC_SLOTS_URL,
    )


_PROBES: dict[str, Callable[[], dict]] = {
    "omen-arc": probe_omen_arc_slots,  # HTTP slot/KV goodput on the resident rung (ADR-0034)
    # am4-oxen / am4-moe probes removed 2026-08-21: those rungs are tombstones
    # (cards moved into OMEN); a probe that can only ever answer "unreachable"
    # is decoration, per this registry's own rule above.
}


def _registry_probe(backend_name: str) -> Callable[[], dict]:
    """The probe declared for `backend_name`, or a stub reporting "available"."""
    declared = _PROBES.get(backend_name)
    if declared is not None:
        return declared
    return lambda: {"occupancy": "available", "detail": _NO_PROBE_DETAIL}


def check_occupancy(backend_name: str, cache: Optional[OccupancyCache] = None,
                    probe: Optional[Callable[[], dict]] = None) -> dict:
    """Cached occupancy check for a named backend, via the ``_PROBES`` registry.

    Probe precedence: an explicit ``probe`` arg wins; then a cache's own probe
    if one was installed (the test/Lease.renew contract — never override a probe
    a caller deliberately handed us); then the backend's declared registry
    probe. A cache with no probe installed — including the process-wide default
    — resolves the registry per key, so ``acquire_lease``/``Lease.renew`` reach
    the same probe a direct call does.
    """
    if probe is None and _PROBES.get(backend_name) is None:
        return {"occupancy": "available", "detail": _NO_PROBE_DETAIL}
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
