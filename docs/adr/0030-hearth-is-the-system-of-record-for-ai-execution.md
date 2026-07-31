# ADR-0030 — HEARTH is the system of record for AI execution

**Status:** Accepted and implemented (2026-07-30). Amends ADR-0014 for one
authenticated control-plane lane.

## Context

HEARTH already owned provider authorization, routing, and the audit ledger, but
each ingress still invented part of execution for itself. BotHerder was the
clearest example: it selected a model, called the provider, bounded concurrency,
assigned request IDs, retained usage, and projected output to IRC. That made IRC
an execution service rather than a protocol adapter. A retry or future CLI,
notebook, scheduler, or workflow ingress would create another partial source of
truth.

The existing `hearth-event.v1` ledger is an audit record of gateway tool calls.
It is not a job lifecycle: it has no Request/Job/Invocation hierarchy, result
artifact, cancellation state, or capacity lease. Reusing that event schema for
execution would also overload its frozen caller vocabulary. The evidence-derived
capability and historical-capacity projections similarly answer different
questions from an invocable Operation and current execution headroom.

## Decision

HEARTH is the system of record for AI execution. Every supported ingress
terminates at one pipeline:

```text
protocol adapter
    → Operation + desired policy
    → execution scheduler and global capacity lease
    → existing backend router
    → Provider
    → immutable result Artifact
    → ingress-specific projection
```

The execution domain has three identities:

- A **Request** is the caller's accepted intent (`req_…`).
- A **Job** is the durable desired work and lifecycle (`job_…`).
- An **Invocation** is one concrete provider attempt (`inv_…`). Retries and
  fallback create new invocations rather than rewriting the first one.

The canonical **Execution Ledger** is append-only NDJSON. It records immutable
desired-versus-observed facts. SQLite tables are rebuildable projections for
current state, cursors, idempotency, and the artifact index; they are not the
source of truth. Input and result bodies live in an immutable, content-addressed
artifact store. Ledger events contain metadata and digests, not prompt or result
text.

Operations, Providers, and policy remain distinct:

- An **Operation** names invocable work such as `llm.chat`.
- A **Provider** is a declared execution target such as `am4-moe`.
- **Execution policy** constrains deadline, output budget, and priority.

The router still chooses the Provider. A shared, SQLite-backed lease manager
bounds concurrent work per Provider endpoint across gateway workers and
protocol adapters. Per-adapter limits remain useful abuse controls, but never
substitute for global admission control.

Trusted adapters may submit on behalf of an authenticated downstream principal.
The adapter cannot choose its own attribution: the gateway stamps the
authenticated caller ID into `source.adapter`. BotHerder delegates the
server-supplied IRC account, not a nickname or client-provided tag.

BotHerder is no longer an execution service. It is an IRC ingress and projection
adapter: it authenticates commands, submits desired work, follows lifecycle
events, retrieves the result artifact, and renders a bounded IRC response.
Claude Code, CLI, notebooks, schedulers, and future integrations are the same
kind of adapter and must converge on the same execution tools.

Large results are artifacts, not IRC floods. An adapter may send a concise
summary and an artifact reference containing identifier, media type, size, and
SHA-256. Raising chat line limits is not the bulk-output strategy.

## Network decision

ADR-0014 correctly keeps latency-sensitive local fleet lanes on the LAN. The
AM4 BotHerder-to-OMEN HEARTH path is a narrower exception: AM4 and OMEN are
already tailnet members, the payload is an authenticated control-plane request,
and Tailscale Serve supplies private DNS and trusted TLS without a public
listener or a second HEARTH process. Port 8443 is served to gateway loopback
only. Funnel is not enabled for this route.

The route is both transport- and capability-scoped:

- Tailscale Serve limits reachability to the tailnet.
- `X-Hearth-Key` identifies one caller with the `irc-adapter` profile.
- That profile exposes execution and status only.
- HEARTH's host/origin guard admits the exact MagicDNS hostname, never a
  wildcard.

## Consequences

- Request, job, invocation, routing, policy, usage, and artifacts now have one
  canonical owner.
- A provider retry or fallback is visible instead of being collapsed into one
  opaque call.
- The canonical history survives projection loss; SQLite can be rebuilt by
  replay.
- Prompt content no longer appears in the legacy gateway audit preview. The
  audit event retains only its byte count and SHA-256.
- BotHerder can be rolled between `direct`, `shadow`, and `hearth` modes. Shadow
  mode compares routing without dispatching duplicate inference.
- Cancellation is cooperative. A running provider call cannot yet be
  force-aborted; its result is discarded if cancellation wins.
- Lifecycle delivery is cursor-based long polling, not push streaming.
- Artifact retrieval is currently inline and text-only up to 1 MiB. A dedicated
  artifact delivery endpoint remains future work.
- Approval states exist in the event vocabulary, but human approval workflows
  are not claimed by this decision.

