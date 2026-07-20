# Phase 5 deployment preflight

## Current State

- The durable HEARTH gateway is running on `127.0.0.1:8710`.
- The running process predates Phase 4, so local `/healthz` returns `404`.
- `host.docker.internal:8710` is unreachable from the host/container path.
- Open Notebook and SurrealDB remain local-only in the separate notebook Compose stack.
- No durable gateway restart, firewall change, or bind change has been performed.

## Target State

After deployment:

- HEARTH listens on the explicitly approved non-loopback interface at `0.0.0.0:8710`.
- Docker clients use `http://host.docker.internal:8710/mcp` with `X-Hearth-Key`.
- The gateway serves unauthenticated minimal `GET /healthz` with exactly
  `{"status":"ok"}`.
- The container caller is profile-gated; authority remains coherent across
  `repo_metadata`, `repo_content`, `repo_write`, and `file_scope`.
- Open Notebook reaches Hearth through the capability-based route; no provider
  or worker credentials are placed in the container.
- The firewall permits TCP 8710 only from the verified Docker/WSL source subnet.

## Rollback

Use this single documented procedure:

1. Remove the narrowly scoped TCP 8710 firewall rule.
2. Unset `HEARTH_GATEWAY_HOST` and `HEARTH_CONTAINER_ACCESS_ENABLED` from the
   gateway runtime.
3. Run the approved `HearthGatewayRestart` task.
4. Confirm the listener is back on `127.0.0.1:8710`.

Do not revoke the caller first; rollback of network exposure is the priority.

## Verification

Deployment succeeds only when all checks pass:

1. `Get-NetTCPConnection -LocalPort 8710 -State Listen` shows the approved
   non-loopback bind.
2. `GET http://host.docker.internal:8710/healthz` returns HTTP 200 and exactly
   `{"status":"ok"}` from a temporary Docker container.
3. Authenticated MCP initialization and `kernel_status` succeed using the
   profiled runtime key.
4. `doorcheck --json` reports `healthy` for `process_listener`,
   `authentication`, and `mcp_surface`.
5. `callerctl list --json` does not report degraded registry ACL state.
6. An Open Notebook documentation source can be ingested and cited without
   exposing caller keys, inventory, worker SSH, or paths outside its grants.

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
