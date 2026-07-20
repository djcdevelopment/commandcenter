# 0022 — Container access needs no network exposure: mirrored WSL + an explicit transport-security allowlist

**Status:** Accepted (2026-07-19) — amends
[0019](0019-container-access-capability-profiles.md) §1 and §7 and the Phase 5
deployment gate; the authorization model in 0019 (§2–§5) stands unchanged.

## Context

ADR-0019 opens: "Docker Desktop containers cannot reach a host service bound to
host loopback." That premise is true under Docker Desktop's default NAT
networking. **It is false on this host**, and the correction removes the entire
network-exposure half of the plan.

Three facts, each measured rather than reasoned:

**1. Mirrored networking already delivers container traffic to loopback.**
`%USERPROFILE%\.wslconfig` sets `networkingMode=mirrored`. A throwaway container
calling `http://host.docker.internal:8710/healthz` against the *unmodified,
loopback-bound* durable gateway received `HTTP 404` — the pre-Phase-4 gateway
answering, not a connection failure. There is no `vEthernet (WSL)` adapter on
this host at all; the WSL VM shares the host's network namespace.

**2. The actual blocker was the MCP SDK's DNS-rebinding guard, which the bind
change does not touch.** A container calling `/mcp` received `421 Misdirected
Request`. The same call with `Host: 127.0.0.1:8710` received `406 Not
Acceptable` — byte-identical to a host-local call. The guard lives in
`mcp/server/fastmcp/server.py`, which auto-enables
`allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"]`.

**3. The guard survived ADR-0019's bind mode, so Phase 5 would have failed its
own verification.** `build_server` called `FastMCP("hearth")` with no `host`
argument and assigned `settings.host` *afterwards*. The SDK computes
transport security inside `__init__` from the host it is **given**, so the
allowlist was always the loopback triple — including under a consented
`0.0.0.0` bind. Verified directly: construct, set `settings.host = "0.0.0.0"`,
and `allowed_hosts` is still loopback-only. Executing the Phase 5 gate as
written would have created a firewall rule, opened a non-loopback bind,
restarted the door — and still answered every container `421`.

**Why ADR-0020's proof did not catch this.** The container smoke test reached
`/healthz`, which is registered via FastMCP's `custom_route` and therefore
*bypasses* the transport-security middleware. It was the one endpoint on the
gateway that could not observe the defect. Liveness reachability and MCP
reachability are different claims.

Compounding it, in mirrored mode ADR-0019 §7's mitigation is not available:
there is no distinct Docker source subnet to scope a firewall rule to, while
`0.0.0.0` would expose port 8710 on this host's real Wi-Fi (`192.168.12.194`)
and Tailscale (`100.124.12.37`) addresses.

## Decision

**1. Container access ships with no network exposure.** The durable gateway
stays on `127.0.0.1:8710`. No firewall rule is created, no non-loopback bind is
made. ADR-0019's bind mode remains in the code as consented, tested machinery
for a host where mirrored networking is not in play — it is simply not the
mechanism this deployment uses.

**2. The transport-security allowlist is ours, explicit, and derived from the
host we actually bind.** `build_server` passes `host=`, `port=`, and
`transport_security=` into the `FastMCP` constructor. `_transport_security(host)`
returns the loopback triple plus `host.docker.internal:*`, and additionally the
bound address itself when that address is a specific non-loopback interface
(`0.0.0.0`/`::` name every interface, so there is nothing to add). DNS-rebinding
protection is always enabled; a settings object we build ourselves could regress
to `False` far more quietly than the SDK's default, so a test pins it.

**3. `host.docker.internal:*` is allowed unconditionally, not gated on
container-access mode.** Under mirrored networking a container reaches this host
over the *loopback* bind, so gating the alias behind non-loopback mode would deny
exactly the configuration that needs it. The relaxation is bounded: `localhost:*`
is already allowed, so a browser-driven rebinding attempt gains no reach it did
not already have, and every tool call still requires an `X-Hearth-Key` header
that a web page cannot supply.

## Consequences

- The Phase 5 gate shrinks from an exposure decision to a **restart**. Steps 1, 2
  and 3 of `docs/operations/phase-5-deployment-preflight.md` (subnet
  confirmation, firewall rule, bind change) are struck; the remaining act is
  restarting the durable gateway on its existing loopback bind, which also
  activates Phase 4 and ADR-0019 capability enforcement.
- Rollback is correspondingly smaller: restart the previous gateway. No firewall
  rule to remove, no bind to restore, because neither was ever changed.
- Verified end to end against an isolated gateway carrying this change, bound to
  `127.0.0.1` only: a container received `HTTP 200` from `/healthz` and `406`
  (not `421`) from `/mcp` via `host.docker.internal`.
- **Proof obligation added:** a container-access claim must be exercised against
  an endpoint that passes through the transport-security middleware. `/healthz`
  alone does not qualify. `hearth/tests/container_access_smoke.py` should be
  extended to make an authenticated MCP call, not only a liveness probe.
- This host's configuration is now load-bearing documentation. If
  `networkingMode=mirrored` is ever removed from `.wslconfig`, container access
  reverts to needing ADR-0019's bind mode — which now works, because the guard
  follows the bind.
- `hearth/kernel/auth.py` reads `callers.json` as strict UTF-8; a BOM is a hard
  parse failure. Noted here because PowerShell 5.1's `Out-File -Encoding utf8`
  writes one by default, which will bite anyone hand-creating a registry.
