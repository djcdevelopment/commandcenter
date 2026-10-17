# ADR-0072 — Flash-Next remains an isolated research recipe

**Status:** Accepted 2026-10-17, documentary disposition under Derek’s explicit closeout instruction. No runtime routing or approval gate changes.

## Context

The [Flash-Next lab](../rnd/flash-next-20261017/RESULT.md) completed correct retrieval at 249,735 input tokens on one OMEN using two B70 s, host expert placement, quantized KV and MTP. It also proved fresh-process reuse of a 3,848-token prefix through existing MemSplice code. The measured recipe differs from Matt Munson’s A6000 reference. A single retrieval chain and saved-but-not-restored large state do not qualify a serving configuration.

Current links negotiate Gen4×8 and Linux Level Zero advertises no peer access. Earlier operator evidence includes successful Gen5 recovery and Windows peer reads. Checked Ubuntu/OMIX versions match published guidance. These observations identify conditions, not a resolved hardware/driver cause.

## Decision

1. Preserve the successful single-OMEN recipe as an isolated research option, with actual token counts, model/runtime/build/quant/KV identity, sampling regime and cache conditions. Keep the three-dense production configuration; add no router seat from this lap.
2. Distinguish saved, copied/hash-verified, restored, and correct continuation. The249K target state has achieved the first two. A claim of long-context MemSplice reuse requires fresh-process restore, expected cached/new token counts and a correct new suffix. MTP draft synchronization is separately unverified.
3. Record physical link negotiation, advertised peer capabilities and actual directional transfers as different observations. Preserve dated Windows and Linux evidence. Gen5 recovery does not establish P2P; capability refusal alone does not establish a universal board limit.
4. Keep the existing guarded reservation, lifecycle and verdict rules. No inference on the helper candidate is applied; its independent review remains open under ADR-0069. This ADR creates no exception to elevation, gate or production rules.

## Consequences

The [plan](../rnd/flash-next-20261017/PLAN.md) starts with a bounded long-state restore instead of another expensive prefill. A driver or firmware change, if chosen later, gets its own before/after conditions. Held-out substance and capacity evidence are required by the existing capability-record process before a production decision; this ADR does not manufacture that evidence.

The observed memory behavior narrows historical Windows-era Flash-Next footprint assumptions in ADR-0040/0041; it does not establish safe co-residency with today’s production seats. Original serving state was restored and verified before closing the lap.
