# ADR-0002 — The belief layer must not ingest infra/harness-caused failures

**Status:** Accepted (2026-07-04)
**Context sources:** `runs/regression-probe-ccb1/PROVENANCE.md`, wave-2 results
(`runs/*/conductor/result.json` on the conductor), fleet MAC-collision incident.

## Context

The belief layer projects capability findings (`known_good` / `known_bad` / `regression` /
`uncertain`) from run *observations* — one per builder per pour, carrying the outcome
(`success` / `timeout` / `error`). Those findings drive `policy.json` (block / quarantine /
exploratory_only) and thus real scheduling.

Wave-2 (2026-07-04) exposed two ways a raw fleet outcome misrepresents capability:

- **Harness timeouts ≠ deliverable failure.** cc-builder-1 recorded `agent_timed_out=True,
  agent_rc=-1` on all five wave-2 pours, yet produced the winning, landable code each time.
  Materializing those as `timeout` observations would have formed a strong regression /
  known_bad on `cc-builder-1|sonnet|claude` and **quarantined the best builder**.
- **Infra failures ≠ model failure.** omen-worker-1 recorded F/0 "workspace not found" on
  all five — pure infrastructure (its shell host claudefarm1 was off the network due to a
  MAC collision), nothing to do with `qwen3-coder:30b|ollama`. Ingesting those would have
  falsely marked that combo `known_bad`.

The belief layer's value is that it learns real capability from real evidence; feeding it
outcomes whose cause is the harness or the network *poisons* it — precisely the failure the
corpus/fixture guards exist to prevent, one layer up.

## Decision

**Only observations that reflect genuine capability signal are materialized into `runs/`
and projected.** Outcomes caused by the harness (timeout that coincides with a complete
commit), the infrastructure (unreachable shell host, transport error), or a broken assay
are **excluded** from the belief corpus — not silently, but with the exclusion and its
reason recorded (a run `PROVENANCE.md`; the "no silent caps" clause applies).

Corollary: `agent_timed_out` and transport errors are kept as run *metadata* (useful to the
economics/observability layers) but are not, on their own, capability evidence.

## Consequences

- The wave-2 build observations were deliberately **withheld** from the belief layer
  (documented in `runs/regression-probe-ccb1/PROVENANCE.md`), even though the code landed.
- Belief re-projection after a campaign is a *curated* step: materialize the clean signals
  (e.g. the regression-probe double-success that lifted the cc-builder-1 quarantine),
  exclude the contaminated ones, and record why.
- A legitimate corpus regression (a finding that correctly retires, shrinking the count) is
  accepted via an authored, scoped `corpus_regression_override.json` (Clause 2), with the
  permit on the `policy_audit.ndjson` trail — the mechanism's first real use was lifting the
  cc-builder-1 quarantine.
- This ADR is downstream of [ADR-0001](0001-assay-acceptance-gap.md): the same signal
  decoupling (fleet outcome ≠ deliverable reality) drives both the "don't auto-land" and the
  "don't auto-ingest" rules. The durable fix is deliverable-aware outcomes at the source.
