# Phase 5 deployment preflight

> **SUPERSEDED IN PART by [ADR-0022](../adr/0022-container-access-needs-no-exposure.md)
> (2026-07-19).** This host runs WSL2 `networkingMode=mirrored`, so containers
> already reach the loopback-bound gateway; the blocker was the MCP SDK's
> DNS-rebinding allowlist, now fixed in `build_server`. **Do not perform the
> bind change or create a firewall rule.** The live deployment is a single
> restart on the existing `127.0.0.1:8710` bind — see "Revised gate" at the
> bottom. The Target State and Deployment gate sections below are retained as
> the historical record of what was planned, not as instructions.

## Current State

- The durable HEARTH gateway is running on `127.0.0.1:8710`.
- The running process predates Phase 4, so local `/healthz` returns `404`.
- ~~`host.docker.internal:8710` is unreachable from the host/container path.~~
  **Falsified 2026-07-19 (ADR-0022):** a container reaches the loopback bind
  today. `/healthz` answered `404` (route absent pre-Phase-4, i.e. connected)
  and `/mcp` answered `421 Misdirected Request` — a Host-allowlist refusal from
  the app, not a network failure.
- Open Notebook and SurrealDB remain local-only in the separate notebook Compose stack.
- Inference payload budgeting is fail-closed: an over-budget request is refused with
  `routing_refusal` / `payload_over_budget_no_eligible_backend` when no qualifying rung is
  available; it is never sent to the failed default rung.
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

## Revised gate (ADR-0022 — the one that applies)

Steps 1–3 of the original gate are struck. No firewall rule, no bind change, no
`HEARTH_GATEWAY_HOST` / `HEARTH_CONTAINER_ACCESS_ENABLED`. The gateway stays on
`127.0.0.1:8710` and is never exposed to any network.

The whole live change is a restart, which activates Phase 4 health facets,
ADR-0019 capability enforcement, the ADR-0022 transport-security allowlist, and
the minted `docker-open-notebook-facade` key together:

1. Restart the durable gateway via the approved `HearthGatewayRestart` task
   (UAC-free; no environment changes).
2. `GET http://127.0.0.1:8710/healthz` → HTTP 200, exactly `{"status":"ok"}`
   (it returns 404 before the restart — that is the pre/post tell).
3. From a temporary container: `GET http://host.docker.internal:8710/mcp`
   → **406**, not 421. 421 means the allowlist did not take.
4. Authenticated MCP initialization + `kernel_status` from the container using
   the profiled `docker-open-notebook-facade` key.
5. `doorcheck --json` → healthy `process_listener`, `authentication`,
   `mcp_surface`.
6. `callerctl list --json` → registry ACL not degraded.

**Rollback:** restart the previous gateway. There is no firewall rule to remove
and no bind to restore, because neither is changed.

**Pre-verified** against an isolated gateway carrying this change, bound to
`127.0.0.1` only: container got HTTP 200 from `/healthz` and 406 from `/mcp`.
Step 1 is the only unperformed action.
