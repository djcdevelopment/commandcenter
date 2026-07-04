# BUILD-NOTES-Gc — confidence-curve calibration (curated synthesis)

## What shipped
- `tools/workflow/project_calibration.py` — `calibrate(observations)` walks the corpus in
  evidence-time order; at each observation it computes the finding + `confidence_score` that
  existed for that combo BEFORE the observation, scores known_good/known_bad predictions
  against the actual outcome, and buckets by prior `confidence_score`. Per bucket: `n`,
  `observed_rate`, `curve_implied_rate` (mean confidence, not midpoint), `bias`. Buckets below
  `CALIBRATION_MIN_BUCKET_N` (5) report `insufficient_evidence` — never extrapolated. The report
  embeds the corpus `evidence_watermark` (freshness marker, never wall-clock — D18).
- `tools/workflow/project_experiments.py` — `confidence_calibration` added to `EXPERIMENT_TYPES`;
  `synthesize_candidates` now takes the calibration inputs and calls `_calibration_candidates`,
  which proposes ONE corpus-wide calibration experiment when scored predictions
  `>= CALIBRATION_MIN_TOTAL_PREDICTIONS` (10) AND the latest report is not watermark-current.
- `tests/workflow/test_project_calibration.py` — 11 tests (hand-computed bias anchor, known_bad
  matching, insufficiency, determinism, candidate gating).

## Worth
The candidate's `worth` is the sentinel `AUTHORED-VALUE-REQUIRED: Derek must assign worth; see
BUILD-NOTES-Gc.md` — worth is an authored judgment (Clause 2), not something the projector invents.
**Derek: assign a worth value for the `confidence_calibration:corpus` candidate.**

## How to read the report
Run: `python -m tools.workflow.project_calibration fixtures/workflow/runs runs --out runs/calibration/calibration_report.json`.
Positive `bias` in a bucket = the curve UNDER-predicts reliability there (observed better than
`samples/(samples+2)` implied); negative = over-confident. Buckets marked `insufficient_evidence`
have too few predictions to judge — the honest current answer on a thin corpus.

## Provenance — the acceptance gap, third sighting
Neither fleet lap was landable, yet both graded ~B/70:
- **am4-worker-1** (assay winner, 70): bare `project_calibration.py` + a `retro.md`. No candidate
  wiring, no tests, no `EXPERIMENT_TYPES` change.
- **cc-builder-1** (64): wired `synthesize_candidates` to CALL `_calibration_candidates(...)` but
  never defined it and didn't import it — a `NameError` that broke 10 existing
  `test_project_experiments` tests. The behavior assay graded a lap that crashes the projector.

Synthesis took cc-builder-1's calibration module + wiring, wrote the missing
`_calibration_candidates` function per the DoD, and added the tests. The assay's inability to see a
`NameError` or missing tests is exactly what the finding #2 grade-cap + stream-scoped acceptance
patch (CONDUCTOR-FOLLOWUPS-2026-07-04.md) addresses.
