# HEARTH execution control plane

HEARTH is the system of record for AI execution. This runbook covers its
append-only Execution Ledger, projections, artifacts, capacity leases, tools,
and the private BotHerder ingress.

## Runtime layout

By default, durable execution state is under `hearth/var/execution`:

```text
execution/
├── events.ndjson        # canonical append-only history
├── projection.sqlite   # rebuildable current-state/index projections
├── coordination.sqlite # cross-process provider capacity leases
└── artifacts/
    └── objects/        # immutable content-addressed bytes
```

`HEARTH_EXECUTION_DIR` may override the ledger root,
`HEARTH_ARTIFACT_DIR` the object store, and `HEARTH_COORDINATION_DB` the lease
database. `HEARTH_OPERATIONS` may select a different operation registry. These
runtime files contain prompts, results, and principal identifiers and must not
be committed.

The normal state sequence is:

```text
request.accepted → job.queued → job.dispatched → invocation.started
→ job.running → invocation.succeeded|failed → artifact.recorded
→ job.succeeded|failed
```

Queued, dispatched, executing, and backend timeout are separate stages. The
execution deadline includes queue time. Each provider attempt has its own
`inv_…` ID. Restart recovery marks an interrupted invocation failed and creates
a new invocation; it never edits the original event.

## Protocol-neutral tools

The gateway mounts:

- `submit_execution`
- `plan_execution`
- `submit_delegated_execution`
- `get_execution`
- `cancel_execution`
- `watch_execution`
- `get_execution_artifact`
- `list_operations`
- `list_execution_providers`

`plan_execution` resolves Operation, Provider, and constrained policy without
prompt content and without dispatch. It is safe for a migration shadow.
`watch_execution` is a supported cursor contract; retain `next_sequence` and
request events after it. Direct callers can read only their own work. A trusted
adapter can read only work that the gateway attributed to that adapter.

The historical gateway `local_generate` name remains compatible, but its
gateway-mounted function submits through the same execution service. The raw
module-level provider function is an internal scheduler primitive, not another
public execution path.

## Operation and Provider configuration

Invocable work is declared in `hearth/etc/operations.toml`. Provider endpoints,
models, routing tags, and `parallel_slots` remain in
`hearth/etc/backends.toml`. Do not encode provider endpoints inside an
Operation.

For every provider, verify `parallel_slots` against the real server before
raising it. The lease scope is the provider endpoint, so model aliases sharing
one llama.cpp process also share its slots. Adapter-local concurrency does not
increase global capacity.

## Verify and rebuild

Run all tests:

```powershell
python -m unittest discover -s hearth/tests -t .
```

Inspect the configured surface without printing caller keys:

```powershell
python -c "from hearth.execution.defaults import get_execution_service; s=get_execution_service(); print({'jobs': len(s.ledger.list_jobs(limit=10000)), 'events': len(s.ledger.iter_events(limit=10000))})"
```

Rebuild the mutable projection from the canonical ledger:

```powershell
python -c "from hearth.execution.defaults import get_execution_service; s=get_execution_service(); print({'events_replayed': s.ledger.rebuild()})"
```

Rebuild only when no gateway process is writing, or restore into a staging
directory and verify there first.

## Backup and restore

For a consistent backup, stop the HEARTH gateway, then copy the entire execution
root. The NDJSON ledger and artifact objects are inseparable: a ledger entry
without its object is not a complete record. `projection.sqlite` and
`coordination.sqlite` may be omitted because they are rebuildable and ephemeral,
respectively, but including them is harmless while stopped.

Restore the ledger and artifacts to an empty execution root, start once, and
rebuild the projection. Verify artifact digests by reading them through
`ArtifactStore`; reads fail closed on size or SHA-256 mismatch.

## Private AM4 ingress

OMEN exposes the existing loopback gateway to tailnet peers with Tailscale
Serve, not Funnel:

```powershell
.\hearth\tools\configure-private-ingress.ps1 -HttpsPort 8443 -GatewayPort 8710
```

Expected adapter endpoint:

```text
https://omen.tail8e749c.ts.net:8443/mcp
```

The script adds the exact MagicDNS hostname to
`HEARTH_TRUSTED_PROXY_HOSTS` in ignored local gateway state. Restart the gateway
afterward. Confirm that `tailscale serve status` shows a private Serve route and
does not label port 8443 as Funnel.

Remove only this route with:

```powershell
.\hearth\tools\remove-private-ingress.ps1 -HttpsPort 8443
```

Do not use a global `tailscale serve reset`; other routes may share the node.

## Provision or rotate BotHerder

Create a dedicated caller with profile `irc-adapter` and install its secret
directly into AM4's root-only environment file:

```powershell
.\hearth\tools\provision-irc-adapter.ps1
```

The secret is streamed over SSH and is not printed. The target is
`/etc/omen-irc/hearth-bot.env`, mode 0600. Recreating BotHerder is required
after rotation. The profile grants execution and status only; it does not grant
filesystem, kernel, cloud, or operator capabilities.

## BotHerder rollout and rollback

BotHerder has three modes in `config/compute-bot/bot.toml`:

- `direct`: prior behavior, calling the configured AM4 endpoint.
- `shadow`: direct answer plus a content-free, no-dispatch HEARTH plan.
- `hearth`: canonical HEARTH submission and artifact projection.

Use `shadow` first, compare chosen provider/model, then set `hearth`. Rollback is
one configuration value back to `direct` followed by container recreation.
Shadow mode never duplicates model execution.

## Security checks

- Never expose the private MCP route with Funnel.
- Never put a caller key in TOML, Compose, command history, logs, or chat.
- Keep caller registry, execution root, and AM4 env files root/operator-only.
- Treat input and result artifacts as sensitive user content.
- Search gateway and BotHerder logs for secret values after rotation.
- Preserve gateway host/origin protection; trusted proxy hosts must be exact
  hostnames.
- Do not infer delegated identity from nicknames. Only an authenticated
  downstream account may become the principal.
