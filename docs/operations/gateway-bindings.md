# Gateway bindings and container access

The durable gateway remains loopback-only by default:

```text
http://127.0.0.1:8710/mcp
```

Container clients cannot use that host-loopback address. Container access is an
explicit mode:

```powershell
$env:HEARTH_GATEWAY_HOST = '0.0.0.0'
$env:HEARTH_CONTAINER_ACCESS_ENABLED = '1'
```

The gateway refuses a non-loopback host without explicit consent and never
falls back silently. The client-facing container endpoint is:

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

