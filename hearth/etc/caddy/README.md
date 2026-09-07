# hearth/etc/caddy — the two HEARTH reverse proxies

One `Caddyfile`, one Caddy process (`HearthFunnelProxyBoot` →
`fleet/funnelproxy/serve-funnel-proxy.cmd`), two site blocks:

```
internet ── tailscale funnel ──▶ :8711 ──/mcp*──▶ 127.0.0.1:8710   HEARTH gateway
builder VM ─ omen.mshome.net ──▶ :8083 ──/v1/*──▶ 127.0.0.1:8081   llama-swap ──▶ :8082
```

Because it is **one process**, a syntax error, a bind failure or a crash in either block
takes the *public* Funnel door down with it. `caddy validate --config
hearth/etc/caddy/Caddyfile --adapter caddyfile` is mandatory before every commit — and see
the bind note below for the failure class validate cannot catch.

## :8711 — the Funnel hop

Unchanged (ADR-0025 as amended 2026-08-24). No secret is stamped; callers present their own
`X-Hearth-Key` and authenticate per-request at the gateway. Only `/mcp*` is forwarded.

## :8083 — the VM inference proxy (added 2026-09-06)

Builder VMs decoded on the 8 GB fx99 sidecar (`192.168.12.220:11434`) because llama-swap on
`127.0.0.1:8081` serves an OpenAI-compatible `/v1` surface **next to an unauthenticated
admin surface** — `/api/models/unload*`, `/upstream/*`, `/running`, `/unload`, `/ui`,
`/logs`, `/metrics`. Binding `:8081` beyond loopback would let any VM unload production
(ADR-0045 Consequences; DECISIONS-PENDING 2026-09-03). This block is the narrowing:

- **`/v1/*` only.** Every other path answers `404` without dialling the upstream.
- **The bearer is injected here.** `header_up Authorization "Bearer {env.OMEN_ARC_TOKEN}"`
  — a *set*, so a client-supplied `Authorization` is replaced, never forwarded. The VMs
  never hold the rung's key. `{env....}` is a **runtime** placeholder: the value is not in
  the repo and not in the adapted JSON config.
  ⚠ Do not "harden" this with `header_up -Authorization` before the set. Caddy's header
  handler applies **deletes after sets**, so the pair strips the credential it just
  injected and every request 401s.
- **Fails closed.** `@noauth expression {env.OMEN_ARC_TOKEN} == ""` → `503` for every path.
  A launcher started without the token refuses to proxy at all rather than reaching
  llama-swap unauthenticated. (llama-server on `:8082` does enforce `LLAMA_API_KEY`, set
  from the same variable by `fleet/arcserve/serve-arc-swap.cmd`; but llama-swap answers
  parts of `/v1` from its own config without touching the upstream, and nothing tracked in
  this repo says whether v251 gates those. So the refusal is made where we control it.)
- **32 MiB request body cap.** ~30× the largest legitimate prompt (the door's `files=` pack
  cap is 1 MiB); it cannot truncate real work and it bounds a hostile body.
- **Header redaction** identical to `:8711` — `X-Hearth-Key`, `Authorization`, `Cookie`
  deleted from the access log (`hearth/var/caddy-vm-proxy-access.log`) *and* from the
  global default logger, which is what leaked a key in 2026-08.

Proof: `hearth/tests/proxy/test_vm_proxy_caddyfile.py` derives a temporary config from this
file (asserting every substitution count, so the proof cannot drift from the config) and
runs a throwaway Caddy on ephemeral loopback ports against a recording fake upstream.

### Listener boundary — `:8083` binds all interfaces; the firewall rule is the boundary

The block deliberately does **not** `bind omen.mshome.net`. Measured 2026-09-06: that name
resolves to **two** addresses on this host — `172.31.96.1` (today's Default Switch) and
`172.25.96.1`, a stale registration from an earlier prefix that is assigned to no
interface. Caddy binds every resolved address and **aborts the whole process** when one
fails (`bind: The requested address is not valid in its context`), which would take `:8711`
down at every boot — and `caddy validate` accepts that config, so the mandatory gate would
not catch it. Never make this process's startup depend on an address that may not exist.

So the boundary is one inbound rule, scoped to the Default Switch interface. **Derek runs
this; it is not run by any script in this repo:**

```powershell
New-NetFirewallRule -DisplayName "HEARTH VM inference proxy :8083" -Direction Inbound -Protocol TCP -LocalPort 8083 -InterfaceAlias "vEthernet (Default Switch)" -Action Allow
```

`-InterfaceAlias "vEthernet (Default Switch)"` is the load-bearing part. Without it the
`/v1` surface — with the rung's bearer injected for the caller — is reachable from the
192.168.12.0/24 LAN. (The admin surface stays unreachable regardless, and the token is
never sent to a client.)

To undo: `Remove-NetFirewallRule -DisplayName "HEARTH VM inference proxy :8083"`.

### Repointing the builders (gated — not done by this change)

Order matters: restart, then rule, then repoint. Nothing below is automated.

1. `schtasks /Run /TN HearthFunnelProxyRestart` from **PowerShell** (Git Bash mangles
   `schtasks` flags), then confirm `:8711` still answers before touching anything else.
2. The firewall rule above.
3. From the host: `ssh claude@cc-conductor.mshome.net 'ssh cc-builder-2 "curl -s
   http://omen.mshome.net:8083/v1/models"'` → the model list. Then a real decode against
   `/v1/chat/completions`; a `200` on the model list alone proves the *proxy*, not capacity
   (the `:8090` oxen facade lesson: a port that answers is not a rung that can emit a
   token).
4. Only then edit `~/fleet-worker-node/runner.json` on `cc-builder-2` and `cc-builder-3`:
   `base_url` → `http://omen.mshome.net:8083/v1`, model → `qwen3-30b-a3b`.
5. Update the `runner_class` comments in `fleet/inventory.toml` from "not yet repointed" to
   the dated repoint.

**Rollback:** the pre-2026-08-29 values are on each node as
`~/fleet-worker-node/runner.json.bak-2026-08-29`; the *current* fx99 values
(`http://192.168.12.220:11434/v1`, `qwen2.5-coder:7b` on `-2`, `qwen2.5:14b` on `-3`) are
recorded in `fleet/inventory.toml`. Copy the fx99 values back to fall back to the sidecar.

### Never the bare llama-swap unload

`POST /api/models/unload` with no model **unloads production too**. Model lifecycle goes
through the door's rotation window (`rotation_window` → `rotation_load` → … →
`rotation_unload` → close; `hearth/rotation/README.md`), which uses the **path form**
`/api/models/unload/{model}`. This proxy makes the whole `/api` surface unreachable from
the VMs on purpose — that is the reason it exists, not a side effect.
