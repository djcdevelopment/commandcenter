# Phase 5 deployment preflight

## Current observed state

- Durable listener: `127.0.0.1:8710`.
- Durable local `/healthz`: `404 Not Found` because the running process predates the Phase 4 route.
- Durable `host.docker.internal:8710`: unreachable, as expected for loopback-only binding.
- No durable gateway restart or bind change was performed.

## Deployment gate

The implementation is ready, but enabling container access is a deliberate
operational change. Before applying it:

1. Confirm the intended Docker/WSL source subnet.
2. Create a narrowly scoped inbound TCP 8710 firewall rule.
3. Set `HEARTH_GATEWAY_HOST=0.0.0.0` and
   `HEARTH_CONTAINER_ACCESS_ENABLED=1` in the gateway runtime only.
4. Restart through the approved gateway task.
5. Run `/healthz` from the container, then run authenticated MCP discovery with
   the profiled caller key.
6. Run `doorcheck --json` and require healthy `process_listener`,
   `authentication`, and `mcp_surface` facets.
7. Roll back by removing the firewall rule, unsetting the enable flag, and
   restoring loopback binding if any check fails.

This document is a preflight record, not authorization to perform the live
deployment.

