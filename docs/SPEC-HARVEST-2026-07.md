# Speculative-brief harvest — 2026-07 (S7)

**Written:** 2026-07-11 · Fable session `cdcabbd4` · scheduler-lane step S7 of
[SCHEDULER-STRATEGY.html](../SCHEDULER-STRATEGY.html) · source: the 20 `spec:*` pours of the
2026-07-05 idle campaign, harvested to [campaign/harvest/](../campaign/harvest/) (S1).

## The headline: there was almost nothing to triage

The planned local-rank-20 → frontier-read-top-5 funnel collapsed on contact with the data.
Deliverables were pulled from the farmer-repo branches (`ccfarm/<plan>/<worker>/lap1` on
`cc-conductor:~/work/commandcenter-ontology/farmer-repo`) for every lap with `commits_ahead ≥ 1`:

| Outcome | Count | Plans |
|---|---|---|
| Real design proposal | **2** | watchfire-autoheal-envelope · watchfire-npu-classifier |
| Real code, off-brief | 1 | belief-recency-decay (generic toy `CorpusGuard`, no recency decay) |
| Max-steps debris (shell output captured into retro.md) | 11 | bankedfire-candidate-source · builder-elo-tournament · fleet-autoscale · **fleet-model-residency-planner** · idea-to-candidate-loop · imagegen-as-machine · learned-router · ledger-rebuild-dr · **scheduler-imagegen-inference** · self-learning-loop-improvements · sensory-dashboard |
| Nothing at all (every lap empty) | 6 | acceptance-oracle · architecture-alignment · end-to-end-lineage · knowledge-per-kwh · mechnet-modes · research-brief-assay |

The two briefs the strategy doc asked to surface explicitly — `spec:scheduler-imagegen-inference`
and `spec:fleet-model-residency-planner`, the ones that feed the scheduler lane itself — are both
in the debris row.

## The systemic finding (bigger than any single brief)

Every real deliverable in the campaign came from **omen-worker-1** (local qwen3-coder:30b) —
and **13 of its 14 non-empty laps lost the assay to an empty lap** (the baseline suite passes on
an unchanged tree, so empty scores 70/B and ties win by order fallback). The only real lap that
won was belief-recency-decay's, and it's the off-brief one. Combined with the JS5 pour (empty lap
crowned, see [REGRET-TREND-2026-07.md](REGRET-TREND-2026-07.md) and the S3 block), the campaign
is a complete, harvested, committed dataset of the ADR-0001 null-action exploit — and the fix for
exactly this landed today as S4 (`tools/workflow/assay_acceptance.py`, `9e01612`): a CCMETA
`requires` glob list that excludes deliverable-less laps before ranking.

## Shortlist (of what exists)

**1. watchfire-autoheal-envelope** — the one worth Derek's read. *(local-qwen summary, edited:)*
Expands automated remediation beyond the current `phantom_in_flight`-only scope with four new
AUTO_HEAL_KINDS (resource_starvation, network_partition_recovery, temporary_service_unavailability,
configuration_drift_correction), each pre-validated against "obvious + undoable" safety criteria,
with tiered risk levels, audit trails, and human override. Biggest weakness: the hard part — the
criteria for "obvious" vs ambiguous — is named but not designed. **Caution:** it also asserts
"auto-heals should use metered compute, not sunk compute," which is the two-economies doctrine
backwards; treat as a seed, not a spec.

**2. watchfire-npu-classifier** — *(local-qwen summary, edited:)* the deferred ADR-0007 slice-1
learned detector: a lightweight NPU-inference classifier that activates only when the rules in
`hearth/health/gaps.py` can't resolve ambiguity, integrating with the blocked-on-ambiguity
protocol via a new `tools/npu_classifier/` module. Generic (file layout is guessed, no model or
feature design), but the deferred-mode framing and integration points match the ADR. Value is
future — the NPU slice stays deferred.

**3. belief-recency-decay** — not usable. The winning lap built a standalone toy belief store
(store/validate/clear in a `findings.json`) unrelated to recency decay and disconnected from the
real belief spine. Its only distinction: the sole real lap that actually won its assay.

## Recommendation

- **Do not frontier-read further** — items 1–2 above are the entire readable yield; both are
  ~1-page seeds, summarized here.
- **If any brief earns a re-pour, it's the two scheduler-lane ones** (scheduler-imagegen-inference,
  fleet-model-residency-planner) — and the re-pour should be the **first live use of the S4
  acceptance gate**: declare `requires: ["proposals/*.md"]` in CCMETA so empty laps cannot win
  again. Fleet dispatch stays on Derek's cue — this folds naturally into the H1b decision moment
  (see the strategy doc's human queue).

## Local-vs-frontier accounting (per the S7 acceptance)

Total corpus: 24.4 KB across 14 branches, extracted in one SSH pass. Read directly at frontier
tier — a 20-way local ranking was **not** performed because 18 of 20 plans had no rankable
content; ranking debris would fabricate signal (same rule as S5: thin data is the finding, not
padding). Local qwen (`local_generate`, omen-ollama backend, ledgered) drafted the two proposal
summaries above: 335/272 tokens in → 151/111 out, 30.9 s cold / 4.1 s warm.
