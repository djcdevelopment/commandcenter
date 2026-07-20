# Gateway bindings and container access

> **On THIS host, container access needs none of the bind/firewall machinery
> below — see [ADR-0022](../adr/0022-container-access-needs-no-exposure.md).**
> `.wslconfig` sets `networkingMode=mirrored`, so a container already reaches
> the loopback bind through `host.docker.internal`. Keep the gateway on
> `127.0.0.1:8710`, create no firewall rule, and set neither environment
> variable. The rest of this page applies only to a host using Docker Desktop's
> default NAT networking.

The durable gateway remains loopback-only by default:

```text
http://127.0.0.1:8710/mcp
```

Under NAT networking, container clients cannot use that host-loopback address
and container access is an explicit mode:

```powershell
$env:HEARTH_GATEWAY_HOST = '0.0.0.0'
$env:HEARTH_CONTAINER_ACCESS_ENABLED = '1'
```

The gateway refuses a non-loopback host without explicit consent and never
falls back silently. The client-facing container endpoint is the same either
way:

```text
http://host.docker.internal:8710/mcp
```

Authentication remains `X-Hearth-Key`; keys come from the runtime secret
environment and are never placed in an image or Compose file. The container
caller should receive only its capability profile, with `repo_metadata`,
`repo_content`, `repo_write`, and `file_scope` coherence enforced by the
gateway.

## Firewall boundary

Firewall changes are intentionally manual. Before enabling the durable mode,
identify the current Docker/WSL source subnet and add a narrowly scoped inbound
rule for TCP 8710. Docker Desktop may change that subnet after restart, so
recheck it after network changes. Remove the rule and unset the enable flag to
roll back to loopback-only.

## Non-destructive smoke test

Run the opt-in test from the repository root. It starts a temporary gateway,
probes `/healthz` from a temporary Docker container through
`host.docker.internal`, and terminates the temporary gateway:

```powershell
python hearth\tests\container_access_smoke.py
```

This proves the host/container route and minimal health contract. It does not
change the durable gateway, firewall, caller registry, or scheduled tasks.

**Known gap (ADR-0022):** `/healthz` is registered via FastMCP's `custom_route`
and therefore bypasses the transport-security middleware, so this smoke test
cannot observe a Host-allowlist refusal. It was green while `/mcp` returned
`421 Misdirected Request` to the same container. A container-access claim needs
a probe that crosses the middleware — extend this test to an authenticated MCP
call before trusting it as proof of reachability.

