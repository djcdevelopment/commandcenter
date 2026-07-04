# ADR-0009 — Tiered agent builds: integration is the review, and frontier merge review is load-bearing

**Status:** Accepted (2026-07-04) — practiced across JS1–JS7 and U1–U6 in one session.
**Context sources:** SESSION-RETRO-2026-07-04.md (addendum 4), the token-tier delegation
directive, commits `9d3ba94` / `037fc1a` / `c4d3552` (integration fixes and hand-applied merges).

## Context

The scheduler build ran the tier ladder end-to-end: haiku scouted/generated/made surgical edits,
sonnet built slices from zero-context briefs, opus built the solver and the experiment, qwen
drafted retro prose, and frontier (Fable) wrote briefs, merged, and integrated. Derek waived the
staged review gates ("skip to the end and the integration test works — that's a huge win").

Results worth deciding around:

1. **All three real bugs were world-model bugs, caught only by integration.** Conductor success
   runs carry *no* `status` key (scout reported otherwise; two agents faithfully encoded the
   wrong belief); `required_model` jobs escaped to stateless machines to dodge load cost; the
   token objective ignored `est_out_tokens`. Unit suites were green through all three.
2. **3 of 5 haiku worktree agents branched from stale bases** and rebuilt existing code from
   scratch — one recreated the entire `hearth/scheduler/`, one made `task_class` *required* in
   the event schema (would have invalidated every legacy ledger event). None reached master:
   merge strategy was `checkout --ours` for rebuilt files + hand-applying the ~15-line intent.

## Decision

- **Integration against live data is the review step**, not an afterthought: every build wave
  ends with the orchestrator running the real end-to-end path (real ledger, real conductor,
  real catalog) before declaring done. Scout reports establish *existence*; only a live sample
  establishes *shape* — any data contract a brief depends on gets verified against one real
  record before the brief is dispatched.
- **Frontier merge review is mandatory for cheap-tier code**, and priced in: haiku agents get
  surgical, single-file briefs, and the merger expects stale-base rebuilds — resolve by keeping
  master's files and re-applying the stated intent by hand. A haiku "surgical edit" that costs a
  frontier hand-reapply is still cheaper than a frontier build, but not free; don't send haiku
  into shared files without this budget.
- **Additive contracts are the default and merge review enforces it**: schema changes that add
  required fields to existing event/document contracts are rejected at merge regardless of
  green tests.

## Consequences

- Briefs stay the contract (per the token-tier directive) but gain a verification preamble:
  the one live-sample check the orchestrator did, stated as fact, not scout hearsay.
- Worktree freshness is a known hazard: until agent worktrees reliably branch from current
  master, expect and plan for add/add conflicts on anything recently built.
- "Green suite" is necessary, never sufficient — the imagegen-250 assay pattern (promote the
  integration run into a deterministic fixture test) is the way a one-off proof becomes a
  standing regression guard.
