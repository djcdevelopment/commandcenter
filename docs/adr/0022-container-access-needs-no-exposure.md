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

---

## Amendment (2026-08-24) — the host address moved; the alias is host-side only

The address in the Context above is stale, and correcting it surfaces a distinction the
original record left implicit.

**The address.** `192.168.12.194` was this host's Wi-Fi address. Since the 2026-08-20
motherboard swap OMEN is `192.168.12.239` on `Ethernet 3` (Marvell AQtion 10Gbit, DHCP,
MAC `B0-82-E2-32-88-71`), and there is no wireless adapter on the box at all. The
**decision is unaffected**: `.194` only ever named an exposure that was declined, so a
stale address inside a rejected alternative changes nothing the ADR decided. The door
still binds `127.0.0.1:8710` and `%USERPROFILE%\.wslconfig` still sets
`networkingMode=mirrored`, so both premises hold.

**The distinction the stale value obscured.** There are two independent resolutions of
`host.docker.internal`, and only one of them is the Windows `hosts` file:

- **Containers never read it.** `notebook/docker-compose.yml` pins
  `extra_hosts: - "host.docker.internal:host-gateway"` on the `hearth_facade` service, so
  Docker writes that name into the container's own `/etc/hosts` from its `host-gateway`
  value; `hearth/tests/container_access_smoke.py` relies on Docker Desktop's equivalent
  default injection. The container path proven at the bottom of Consequences above was
  never exposed to this drift. Measured after the pin landed:
  `python -m hearth.tests.container_access_smoke` passed on ephemeral port 62440 with the
  Windows aliases pointing at `127.0.0.1` — the container reached a `0.0.0.0`-bound
  gateway through a name the host file resolves to loopback, which only holds if container
  resolution is independent of that file.
- **`CONTAINER_HOST_ALIAS` is not a resolution target.** `hearth/kernel/gateway.py`'s use of
  the alias is a **Host-header allowlist entry** — the string a container sends, not an
  address the gateway dials. It too was untouched.

**The risk that was real.** Windows-side callers *do* read the file, and one recipe told
them to: `notebook/integration/README.md` set `HEARTH_MCP_URL` to the container form inside
a **PowerShell** snippet. A host-side facade run following it would have resolved to `.194`
and sent `X-Hearth-Key` to whoever holds that address. Measured 2026-08-24: `.194` answers
no ICMP and left no ARP entry after a probe — unclaimed, no observed leak — but it is an
assignable address in the same DHCP scope. The recipe now uses `127.0.0.1` and names the
container form as container-only.

**The fix, and why it is drift-proof.** The two aliases are pinned to `127.0.0.1` in the
Windows `hosts` file, in an authored block placed **above** Docker Desktop's managed
`# Added by Docker Desktop` section, with the stale `.194` records removed from inside it.
Three properties earn the choice over correcting the value to `.239`:

1. Loopback cannot drift. A DHCP reservation was considered and declined precisely because
   the loopback pin removes the dependency on the LAN address rather than stabilizing it.
2. Loopback is the *working* answer, not merely the safe one. The door binds `127.0.0.1`
   only, so a host-side caller resolving to `.239` would fail to connect — a correct-looking
   entry that still does not reach the service.
3. The placement is defensive against Docker Desktop rewriting its own block: an entry
   above the block wins on resolution order. That defence is untested, because the rewrite
   did not happen — measured 2026-08-24, starting Docker Desktop left the file untouched
   (same mtime, same 1889 bytes) and resolution stayed at `127.0.0.1`. Treat "survives a
   rewrite" as a design property, not a proven one.

Container access is unchanged by all of this, which is the point: `host.docker.internal`
means "the Docker host" to a container and "this machine" to a Windows process, and both now
resolve to something that reaches the loopback-bound door.
