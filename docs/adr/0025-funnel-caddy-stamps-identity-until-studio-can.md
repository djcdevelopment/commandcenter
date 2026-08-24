# 0025 — A Funnel-facing Caddy proxy stamps caller identity until Google Agent Platform Studio can send one itself

**Status:** Accepted (2026-07-21) — first exposure of HEARTH to an external,
cloud-hosted MCP client; does not amend ADR-0019's authorization model, only
adds a new ingress path in front of it.

## Context

The first non-local caller ever granted real HEARTH access is an ADK agent
running in Google's Agent Platform Studio (project `lumberjacks-exp-20260711-djc`),
reached by Derek adding Studio's native "MCP Server" tool to an agent already
built there. HEARTH's gateway binds `127.0.0.1:8710` only and requires every
caller to present an `X-Hearth-Key` header (ADR-0019); a cloud-hosted agent has
no LAN path to that loopback bind at all.

Two problems surfaced only by trying it live, not by planning it:

**1. Tailscale Funnel alone reaches the gateway, but fails its Host allowlist.**
Funnel forwards a request from the public hostname
(`omen.tail8e749c.ts.net`) straight to the local port. The gateway's own
DNS-rebinding guard (ADR-0022's `_transport_security`, unchanged here) only
trusts loopback-shaped `Host` values, so a bare `tailscale funnel 8710` gets a
real, working request all the way to the gateway process — and a `421 Invalid
Host header` from it. This is not a new security control; it is the existing
ADR-0022 allowlist correctly doing its job against a Host value it was never
told to trust.

**2. Studio's native MCP Server tool cannot send the header HEARTH requires.**
Its Authentication dropdown offers only `None` today — `OAuth` and `API key`
are both disabled, labeled "Coming soon." `None` sends no custom header at
all. Since `X-Hearth-Key` is how HEARTH identifies any caller at all,
`Authentication: None` reaching the gateway directly would not be "less
secure" — every call would be denied outright, capability profile or not.

A narrow reverse proxy was the chosen shape for the exposure itself
(Derek's call, over a bare-port Funnel): Caddy, already the house tool for
exactly this job (the AM4 image-gallery precedent), configured to forward
only `/mcp` — the gateway's one real path — and 404 everything else.

## Decision

**1. A Caddy proxy sits between Funnel and the gateway** (`hearth/etc/caddy/Caddyfile`,
launched via `hearth/etc/start-hearth-funnel-proxy.cmd`, mirroring the naming
and secrets convention of `start-hearth-gateway.cmd`). `tailscale funnel`
targets the proxy's port, never the gateway's `:8710` directly.

**2. The proxy rewrites `Host` to a trusted value before forwarding**
(`header_up Host 127.0.0.1:8710`), satisfying ADR-0022's allowlist rather than
weakening it — the allowlist itself is untouched.

**3. Given Studio's current auth ceiling, the proxy stamps a fixed
`X-Hearth-Key` on every request reaching this one endpoint.** The secret
HEARTH checks therefore lives in a value Studio's Endpoint URL field
implicitly carries (the Funnel hostname), not in a per-request header a
client chooses to send. This is a real, named tradeoff, not a discovered
vulnerability: the Funnel URL itself becomes the de facto shared secret for
this specific proxy instance. Accepted explicitly by Derek ("it's trial
credits, we let it ride"), on the following bound: the caller this stamped
key resolves to (`gcp-adk-test`) carries the **existing** `research` profile
(read-only: `read`, `query`, `generate`, `status`, `repo_metadata` — no
write, no git-content, no test execution), narrowed to `file_scope`/
`repo_access = C:\work\commandcenter` only. Worst case of the URL leaking is
read access to one repo's docs/code, not a mutation path.

**4. The secret itself never enters git.** `hearth/etc/caddy/Caddyfile` (tracked)
stays secret-free and does `import ../../var/caddy-funnel-secret.caddy`; the
one-line `header_up X-Hearth-Key <value>` lives in that gitignored file under
`hearth/var/`, the same convention `start-hearth-gateway.cmd` already uses for
`AM4_OXEN_TOKEN`. (The secret was briefly written directly into the tracked
Caddyfile during implementation and caught before it was ever staged — see
the 2026-07-21 retro.)

**5. No rate limiting yet.** Stock Caddy has none built in — real rate limiting
needs a custom `xcaddy` build with the third-party `caddy-ratelimit` module.
Derek chose to skip that build step for this test rather than block on it.

## Consequences

- **A cloud-hosted caller's identity is currently a property of which URL it
  was given, not of a credential it presents per request.** This inverts the
  normal HEARTH model (ADR-0019: caller → profile → capability) for exactly
  one ingress path. Any future caller minted behind this same proxy inherits
  the same weakening unless it gets its own Funnel/Caddy instance.
- **Revisit when Studio ships API-key auth** (currently "Coming soon"):
  switch the MCP Server tool's Authentication to a real per-request header,
  drop the Caddy-side stamp, and `gcp-adk-test`'s identity again depends on a
  credential it presents, not a URL it was handed.
- **`callerctl`'s `--runner-class` has no "cloud" value** (`frontier`/`local`/
  `human` only). `gcp-adk-test` was minted as `frontier` — the closest fit,
  matching `claude-frontier`'s precedent — but this is a placeholder, not a
  considered taxonomy decision. Revisit if more cloud-hosted callers are
  minted.
- **No rate limiting is a live, accepted gap**, not an oversight — tracked in
  `DECISIONS-PENDING.md` for a future `xcaddy` build if this proxy sees
  real traffic beyond one test caller.
- Verified end to end: a real MCP client sending zero headers (matching
  exactly what Studio's `Authentication: None` sends) successfully called
  `kernel_status`, `read_file`, and `git_status` through
  Funnel → Caddy → gateway, each ledgered as `gcp-adk-test` / profile
  `research` / `ok: true`.

---

## Amendment — 2026-08-24: the stamp is removed, and it leaked before it left

**Status of the original decision: superseded in its mechanism, vindicated in
its reasoning.** ADR-0025 named the exact conditions for revisiting, and both
arrived at once.

### What happened

**1. The stamped key leaked into the proxy's own logs.** Caddy's
`reverse_proxy` error path (`"aborting with incomplete response"`) serialises
the whole request object, headers included. The live `X-Hearth-Key` therefore
sat in plaintext in `hearth/var/caddy-funnel-proxy.log` — 5 occurrences,
undetected from 2026-07-24. The file is gitignored, so it never left the box,
and the endpoint had been returning 502 since the 2026-08-20 rebuild, so the
key unlocked nothing at the time of discovery. Rotated regardless; the log was
destroyed.

Worth naming as a class, because this ADR's own threat model missed it: we
reasoned about who could *reach* the endpoint and never about where the
credential would be *written down* on the way through.

**2. The proxy had no boot task and silently died.** It ran from 2026-07-21 as
a hand-started process. The OMEN rebuild wiped it. Tailscale Funnel stayed
configured and kept serving the public hostname — a 502 — for four days while
every map still described the lane as live. The endpoint reported *presence*
while providing *nothing*.

**3. A caller arrived that can send its own header.** The prerequisite this ADR
set for dropping the stamp was "once Studio ships API-key auth." The equivalent
condition was met from the other direction: a peer's Hermes router, which sends
custom headers natively.

### What changed

- **The stamp is gone.** Callers present their own `X-Hearth-Key`; the gateway
  identifies them per request by the normal ADR-0019 path. **The Funnel URL is
  no longer a credential** — knowing it now earns a rejection, not read access.
  This restores the model this ADR knowingly inverted.
- **`peer-inference` minted** on profile `generation-proxy` — `generate`,
  `execution`, `status`. No file authority, no repo authority.
- **Both Caddy loggers redact** `X-Hearth-Key`, `Authorization`, and `Cookie`,
  and now roll at 10 MiB × 3. The *default* logger needed filtering, not just
  the access log — that is where the leak actually was.
- **`HearthFunnelProxyBoot` / `HearthFunnelProxyRestart`** registered on the
  ADR-0032 pattern, so the hop survives a rebuild.
- **`funnel-proxy` added to `fleet/inventory.toml`** with a revive command, so
  the sweep notices next time instead of four days of quiet 502s.

### Consequence, stated plainly

**The GCP Studio lane goes dark** until Studio ships API-key auth for its MCP
tool. That is precisely the trade this ADR pre-authorised. `gcp-adk-test`
remains minted and rotated; it simply has no ingress that will stamp for it.

### Verified end to end, 2026-08-24

Through `https://omen.tail8e749c.ts.net/mcp` from the public internet:
no key → **denied**; wrong key → **denied**; `peer-inference` → `kernel_status`
**allowed**, `read_file` refused (`profile 'generation-proxy' does not grant
'read'`), `git_status` refused (`does not grant 'repo_metadata'`), and
`local_generate` returned 52 tokens from `omen-arc`/`qwen3-30b-a3b` in 12.5 s.
Both proxy logs contain zero occurrences of the key header afterward.

The rate-limiting gap from the original Consequences section **remains open and
is now more material**, since this ingress is intended for a real peer rather
than one test caller.
