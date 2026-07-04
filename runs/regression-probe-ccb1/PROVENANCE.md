# PROVENANCE — runs/regression-probe-ccb1

The regression probe that lifted the cc-builder-1|sonnet quarantine.

## Why it ran
pour-c2 (2026-07-03) timed out on cc-builder-1|sonnet after a pour-b2 success, so the
belief layer formed `regression:cc-builder-1|sonnet|claude` and `project_policy` derived a
`quarantine:cc-builder-1|sonnet|claude|*|*` rule (Stage-2 "quarantine regressions" firing on
real fleet behavior). The belief layer also emitted a `regression_probe` experiment candidate
demanding a retest. This run answered that demand.

## How it ran
Dispatched via the conductor inbox (`inbox/regression-probe-ccb1.md`,
CCMETA `{"builders":["cc-builder-1","cc-builder-2"], "promote": false}`): a small,
deterministic wordcount task (`tools/text/wordcount.py` + tests). Both builders produced a
clean lap — assay `167/167 passed, score 70/B`; cc-builder-1 `agent_rc 0, timed_out False`.
Promotion was disabled (probe, not a landing).

## Evidence
- `conductor/result.json` — the raw conductor payload (builds, assay scoreboard, promotion).
- `artifacts/obs_regression_probe_ccb1_cc_builder_1.json` — cc-builder-1|sonnet|claude, outcome
  `success`. This is the newest observation for that combo, so it is no longer "failure after
  last success".
- `artifacts/obs_regression_probe_ccb1_cc_builder_2.json` — cc-builder-2|vllama-planner|openai,
  outcome `success`.
- `events.jsonl` — a `retrospective.created` event linking both observations (the projection
  ingests observations only through an events.jsonl artifact_ref).

Timestamp `2026-07-04T04:18:00+00:00` is the run's completion (filed 04:15, graded shortly
after). Observations carry no throughput/physical fields — this was a build lap, not a probe.

## Belief-layer effect (re-projected 2026-07-04)
- `regression:cc-builder-1|sonnet|claude` **retired** → the combo re-derives as `uncertain`
  (3 samples, 2 success, 1 fail = 0.67 < KNOWN_GOOD_MIN_SUCCESS_RATE).
- `quarantine:cc-builder-1|sonnet|claude|*|*` policy rule **lifted**; the `regression_probe`
  candidate **retired**.
- Bonus: **cc-builder-2|vllama-planner|openai upgraded uncertain → known_good** (its success
  pushed it to 3/4 = 0.75 ≥ 0.7).
- The findings count decrease 18→17 (one regression resolved) was a legitimate corpus
  regression, accepted via an authored, scoped `corpus_regression_override.json` (Clause 2);
  the permit is recorded in `knowledge/policy_audit.ndjson`. The override auto-deactivated
  after both scoped files wrote, and was restored to its template.

## NOT folded: the wave-2 build observations
The five wave-2 pours (d1/e1/f1/ga/gc) were deliberately NOT materialized into the belief
layer, because their fleet outcome signals do not reflect deliverable reality and would
POISON it:
- cc-builder-1|sonnet shows `agent_timed_out=True, rc=-1` on ALL FIVE wave-2 pours — yet it
  produced the winning, landable code every time (d1/e1 landed green; f1/ga's code landed with
  curated tests). The harness-timeout flag is uncorrelated with deliverable quality here;
  folding it would falsely re-quarantine the best builder with 5 timeouts.
- omen-worker-1 shows F/0 "workspace not found" on all five — pure infra (claudefarm1 down,
  see fleet/inventory.toml), not a model failure. Folding it would falsely mark
  omen-worker-1|qwen3-coder:30b|ollama as bad.
This withholding is intentional (no silent cap): wave-2's belief-layer capacity fold-in awaits
the finding #2 observation-layer fix (deliverable-aware grading + agent_timed_out that reflects
completion), tracked in CONDUCTOR-FOLLOWUPS-2026-07-04.md.
