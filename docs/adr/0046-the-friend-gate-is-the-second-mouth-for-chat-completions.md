# 0046 — The friend gate is the recognised second mouth for raw chat-completions: an invited friend's own agent may consume the B70s, per-friend key, budgeted and ledgered at the gate, never anonymously

**Status:** Accepted (2026-09-13) — Derek's decisions in the 06:20 planning window, after the first real
guest session of the nightshift front door. Execution state per decision below (**LANDED** /
**DECIDED, NOT YET EXECUTED** / **OPEN**).

**Companion to:** `docs/adr#0014` (Tailscale is for humans and the Funnel; this is a designated Funnel
lane), `docs/adr#0018` (door-side payload budgets are load-bearing; *"nothing may bypass the door to
`:8082` for real work"* — amended here, narrowly), `docs/adr#0019`/`#0023` (authority is granted, never
assumed; inference was never an authority domain — the gate is where a friend's authority over inference
is expressed), `docs/adr#0025` (Funnel + Caddy stamps identity until the studio can — the `:8711` hop, no
secret stamped, rate limiting "open and more material since a real peer arrived"), `docs/adr#0030`
(HEARTH is the system of record; trusted adapters submit on behalf of authenticated downstream
principals), `docs/adr#0040`/`#0045` (llama-swap owns the serving lifecycle).

## Context

The IRC community project (`C:\work\irc`) invites friends with Ergo SASL accounts (nick == account, no
self-registration) and lets them `!ask` the B70s through the bot-herder → HEARTH → `omen-arc` lane:
512-token, 15-line answers over a control surface that is deliberately not bulk transport. Its written
Phase 3 (`docs/COMPUTE-LAYER-PLAN.html`, 2026-07-30): *"the bot ALSO exposes the pool as an
OpenAI-compatible endpoint → a member's Buzz agent or any harness can consume the community pool
directly."* Never built. The BUZZ brief's Lane A said the same. `peer-inference` (ADR-0025 amendment)
proved a peer can reach the door over the Funnel — but the door is MCP, and an agent harness speaks
OpenAI chat-completions with tools and streaming.

On 2026-09-13 the first real guest of the nightshift front door (a seven-phase, sandboxed, no-file-access
web UI built for a non-technical friend) said: *"the approach having stuff locally assisted by your rig
not possible? I want sometimes to simply alter little thing … I have no direct file access. I thought
about the openhands solution beeing possible?"* He is not the non-technical guest; he wants **his own
agent, on his own files, using Derek's GPUs**. That is Phase 3, and it is one small service, not a
product.

What existed, and why none of it fit: the door (`local_generate`) has no `messages`/`tools`/streaming;
the Open Notebook facade (`:8780`) rejects `stream=true` and has no `tool_calls`; the Caddy `:8083`
VM proxy injects the rung's bearer for `/v1/*` but erases the caller — an anonymous mouth. Per-caller
quotas exist nowhere; the kernel ledger attributes door traffic only; raw `:8081` traffic is
unattributable (ADR-0018 §3 made that doctrine).

## Decisions

1. **A friend gate exists (`hearth/friendgate/`, `127.0.0.1:8791`) and is the only OpenAI-compatible
   surface for external principals.** It authenticates a per-friend bearer (sha256 at rest in
   `hearth/var/friendgate/keys.json`, `friendctl mint --account <ergo account>`), forwards
   `/v1/chat/completions` to llama-swap verbatim — streaming (SSE pass-through) and native tool calls
   included — with the rung's bearer injected from `OMEN_ARC_TOKEN` (sourced only through
   `with-gateway-env.cmd`; unarmed = 503, fail closed). Only `/healthz`, `/v1/models`,
   `/v1/chat/completions` exist; llama-swap's admin paths are unreachable through it. **LANDED**
   (`hearth/tests/friendgate/test_gate.py`, 12 tests; live through the public Funnel 2026-09-13 07:0x).
2. **ADR-0018's "nothing bypasses the door" is amended for exactly this mouth, on two conditions the gate
   meets itself:** (a) the payload budget is enforced *before* the rung sees a byte — estimated prompt +
   requested completion tokens must fit `min(per-slot context of the model, the key's max_context)`,
   refused with the door's shape (`payload_over_budget_for_model`), never silently truncated;
   (b) every request is ledgered as principal `{type: irc_account, id: <friend>}`, source
   `{transport: https, adapter: friend-gate}`, with tokens in/out from the server's `usage`, in
   `hearth/var/friendgate/usage.ndjson` (`friendctl usage`). Feeding those rows into the kernel ledger /
   `knowledge/offload.json by_caller` is the follow-up (`record_event` validates a workflow-event
   schema; a reader in `hearth/projection/economics.py` is the likelier path). **LANDED** (a, b local);
   **OPEN** (kernel-ledger projection).
3. **Ingress is the existing Funnel hop.** `:8711` gains `handle /v1/*` → `127.0.0.1:8791`; `/mcp*` is
   unchanged; nothing is stamped on the hop; the Caddy proof test now asserts `/v1` on the Funnel dials
   the gate's port and never llama-swap's. Friend URL: `https://omen.tail8e749c.ts.net/v1`. **LANDED**
   (Caddy reloaded 2026-09-13 07:05; `hearth/tests/proxy/test_vm_proxy_caddyfile.py` 29 tests).
4. **Hours are a property of the serving shape, not a clock.** The gate lists only models that are
   resident *and* offer ≥ `FRIENDGATE_MIN_CONTEXT` (65,536) tokens per slot — parsed from the launch
   yamls (`-c`/`-np`) so the source of truth is the shape itself. Today that is the night shape's
   `qwen38-27b-mtp` (262144/2 slots); by day production's 8×16k slots are ineligible and the gate
   answers 503 `off_hours` with the schedule text. A daytime seat for friends is a serving decision
   (ADR-0045 territory), not a gate setting. **LANDED**.
5. **Fairness is the gate's job, not HEARTH's, for this lane.** Per key: `max_concurrent` (1),
   `max_context_tokens` (65,536), `tokens_per_day` (3,000,000, resets local midnight, survives restarts);
   globally `FRIENDGATE_MAX_CONCURRENT` (1) so the owner keeps a slot; 429 + `Retry-After` when busy.
   This closes ADR-0025's rate-limiting gap for this route (the Funnel's `/mcp` route still has none).
   **LANDED**.
6. **Thinking is off by default at the gate** (`chat_template_kwargs.enable_thinking=false` unless the
   friend sends their own): the 3.8 models think by default and an agent harness pays for it in output
   budget and wall time (campaign lesson, re-measured 2026-09-13: 48 tokens of `reasoning_content`, no
   answer). Explicit opt-in survives. **LANDED**.
7. **Key custody and lifecycle are manual in v1.** Derek mints and revokes with `friendctl`; the portal's
   invite-redeem flow minting a gate key automatically is a follow-up in `C:\work\irc`. The plaintext is
   shown once; the file holds hashes; a revoked or expired key answers 403 on the next request (the gate
   reloads the file on change). **LANDED** (manual), **DECIDED, NOT YET EXECUTED** (portal minting).
8. **The nightshift front door is unchanged and remains the mode for a truly non-technical guest.** The
   two are complementary: one link + password for someone who wants a website and will wait; one URL +
   key for someone who runs their own agent. **LANDED** (nothing to do).

## Consequences

- A friend's harness will run long contexts. On this shape decode falls from ~75 tok/s shallow to
  8–10 tok/s at ~50k (measured 2026-09-13); the per-key `max_context_tokens` is the throttle and the
  onboarding text says so. Their harness's own condenser is their tool; the gate does not summarise.
- The rung's bearer never leaves OMEN; the friend's key never reaches llama-swap; no bearer of either
  kind appears in any log (gate: no access log; Caddy: header-redacting loggers; test-asserted).
- Boot survival: `HearthFriendGateBoot` clones `HearthFunnelProxyBoot` (S4U, boot trigger, restart 3@1m,
  `ExecutionTimeLimit PT0S`); registering it needs an elevated shell. **OPEN** until registered. The
  gate is in `fleet/inventory.toml` so `/checkmechnet` sees it die.
- Pre-existing hazard surfaced while planning, not addressed here: `omen-arc-oss` (`:8083`) and the
  Caddy VM proxy (`:8083`) collide; whichever binds second fails.

## Verification (2026-09-13)

- `pytest hearth/tests/friendgate hearth/tests/proxy`: 41 passed.
- Public path from the Funnel ingress (208.111.34.11): `/v1/models` 401 without a key, model list with
  one; `/mcp` still the gateway's; `/running`, `/v1/running` 404; a streamed German completion
  (*"e.V. steht für eingetragener Verein."*, 12 tokens) with a usage row attributed to the test key;
  the key absent from both Caddy logs.
- Pending: an agent-harness lap from a second machine (OpenHands on the i5 against the public URL).
