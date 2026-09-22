# 0048 — Local-model repository work remains a candidate until a frontier verdict

**Status:** Accepted (2026-09-21). This ADR supersedes the production-routing
portions of ADR-0039 and ADR-0047; their measurements and historical artifacts
remain evidence.

## Decision

HEARTH is the only front door and system of record for bounded local repository
work. `LocalWorkService` freezes sources from a named Git commit, submits the
versioned prompt through `ExecutionService`, stores the model response as an
immutable candidate, validates its structure and mechanical claims, and then
stops at `awaiting_review`. No producer path applies, commits, merges, or calls a
candidate successful.

The frontier caller independently checks citations, applies diffs only in an
isolated worktree, runs tests, reviews security, and records `accepted`,
`rejected`, or `superseded`. Acceptance requires an evidenced passing row for
every original criterion. Local route failure is loud and has no cloud fallback.

`auto` sends evidence below 8,192 exact server-counted tokens to `fast` and
evidence at or above that floor to `deep`; quote retrieval uses 4,096. Explicit
lanes never substitute. Vision is refused. One retry may repair malformed
candidate JSON; semantic failures and unavailable/capacity failures do not retry.

## Production topology

- `fast`: `Qwen/Qwen3-30B-A3B-GPTQ-Int4` on native Ubuntu vLLM on AM4,
  tensor-parallel across the RTX 4070 Ti and RTX 5070, initially 16,384 context,
  four sequences, 4,096 output reserve and 0.90 GPU utilization, exposed only
  through AM4's authenticated `:8090` facade.
- `deep`: Qwen3.8-27B Q4_K_M on both OMEN B70s under llama.cpp SYCL, layer split
  `1,1`, 131,072 context, one slot, f16 KV, `-ub 2048 -b 4096`, MTP off, with an
  8,192 output reserve.

The route profile is promoted only after the gates in the local-work runbook.
If deep qualification fails, AM4 fast remains available and deep refuses.
The prior OMEN MoE launch recipe stays as rollback evidence but leaves
opportunistic routing after dense cutover. JEV and Hermes scheduler artifacts
are parked experiments, not this production path.

## Consequences

The manifest proves the pinned source and prompt digests, template and route
profile hashes, actual provider/model execution, artifact digest, receipt link,
and validating caller. Exact tokenizer admission replaces byte estimates for
this workflow. A model response is useful output, but it is not completion.
