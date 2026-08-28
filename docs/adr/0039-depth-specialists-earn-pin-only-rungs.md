# 0039 — Depth specialists earn pin-only rungs; fx99 enlists as the CUDA sidecar

**Status:** Accepted (2026-08-27, Derek — both calls made explicitly in session)

**Companion to:** `docs/adr#0038` (a verdict cites only evidence from the
configuration it promotes), `docs/adr#0034` (the dual-B70 rung is the door
default), `docs/adr#0031` (a pin chooses a rung, not a waiver of arithmetic),
`docs/adr#0027` (gateway dispatches are not observations yet — gate 2)

## Context

The Qwen 3.8 campaign ended `do_not_promote` (12/15): the 27B dense candidate
does not replace Qwen3-30B-A3B as the door **default**. That verdict answered
the question it was designed to answer — measured at the 512-token operating
point, where the 27B delivers 0.687× the incumbent's jobs/hour.

The same corpus contains the number the verdict does not act on: the ratio
inverts with depth. At 8K prompts the 27B delivers **2.63×** the incumbent's
jobs/hour; at 32K, **5.49×** — corroborated single-stream by the llama-bench
comparability sweep, where the dense model passes the MoE on prefill at 32K
(181.6 vs 139.7 tok/s). It also near-doubles the deterministic pass rate
(0.861 vs 0.472) and wins or ties 95.6% of blind pairwise comparisons against
the incumbent. The door's `files=`-pack traffic asks the deep-context
question far more often than the 512-token one.

Separately: the retirement of the AM4 B70 rungs (`docs/adr#0034`) left the
fleet with no always-on second inference host. Derek's 2026-08-24 ruling —
"do not build the CUDA rung on AM4, re-map only" — stands. The box that
already serves CUDA inference is **fx99** (RTX 2070 SUPER, Ollama on
`192.168.12.220:11434`), deliberately un-enlisted until now. Verified live
2026-08-28: 12 resident models, 1.9–8.4 GB each.

## Decision

1. **`omen-arc-27b` becomes a pin-only rung** — declared in `backends.toml`
   with `tags = []`, exactly like `omen-arc-oss`: never opportunistically
   routed, pinned with cause, serving costs a model swap. Initial
   configuration is the campaign's proven production shape with **MTP off**
   (the temperature-0 divergence question of `docs/adr#0038` blocks MTP-on
   deployment) and **text only** (the unexplained 27.4-vs-6.8 tok/s
   text/vision decode split stays out of production until diagnosed). The
   rung's charter is deep-context offloads: summarize/extract over large
   `files=` packs, long-horizon analysis.

2. **fx99 enlists as the CUDA sidecar rung** (`fx99-ollama`) — small
   always-on critic/utility seats on hardware that already serves them. AM4
   remains a services host per the standing ruling. During OMEN maintenance
   windows this rung is also the designated cheap local fallback, so dark
   windows stop leaking to metered/trial cloud rungs by default.

## Consequences

- The do_not_promote verdict is **unchanged and reinforced**: the default
  rung stays 30B-A3B. This ADR routes around the verdict's operating point,
  not through it. Phase 1's accuracy-at-depth grading (R10) either confirms
  the 27B rung's charter or retracts the rung; validity already degrades at
  64K (16/24) and 128K is thermally quarantined — the rung's context ceiling
  is set accordingly.
- Gate 2 (`docs/adr#0027`) begins to unlock: capability formation has been
  blocked because every rung declared exactly one model on one host. A
  second model reachable through the OMEN pool and a second host serving
  varied small models are precisely the variation the association engine has
  been waiting for.
- Context budgets follow `docs/adr#0031`: each new rung declares
  `context_bytes` derived from its serving arithmetic, never hand-copied.
- fx99's models are 7–14B class on 8 GB VRAM: the rung is honest about being
  a light-duty seat. It is not a rehabilitation of the dead AM4 rungs and
  its declaration says so.
