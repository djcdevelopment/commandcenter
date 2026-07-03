# BUILD-NOTES-A2 — corpus regression guard

Curator pass (landed locally from the approved STREAM A2 prompt). The fleet `pour-a2` winner
was a false-positive: its only meaningful diff was `retro.md`, the actual `corpus_guard` work
was incomplete on the *losing* branch, yet it won the assay — so this was authored fresh from
the stream. (That mis-selection is itself the finding written up in
`ASSAY-ACCEPTANCE-GAP-2026-07-03.html`.)

## Mission

The rot test protects derived files from hand-edits. Nothing protected them from a
re-projection that sees LESS evidence than the previous run — the 2026-07-02 incident, where a
pipeline rerun over an incomplete corpus clobbered `knowledge/*.json` and the first real
capability was lost from the derived store. A2 adds that protection.

## What changed

- **New `tools/workflow/corpus_guard.py`** — `guard_write(path, new_doc, extract)` plus
  `CorpusRegressionError` and `make_extractor(count_key, watermark_key="evidence_watermark")`.
  It refuses a write whose watermark is older **or** whose primary count is smaller than the
  file on disk, unless an authored override permits it. Comparison rules:
  - watermark compared only when **both** docs carry one;
  - count compared only when **both** docs carry one;
  - equal-or-newer **and** equal-or-larger passes untouched (diff-clean reruns are never disturbed — D18).
- **New authored object `knowledge/corpus_regression_override.json`** (Clause 2) — inactive
  template `{active, reason, author, scope, created}`. The guard resolves this file (and the
  audit trail) relative to `path.parent`, never a hardcoded `knowledge/` path, so it works
  against the temp knowledge dirs the tests use. When a regression is permitted the guard
  appends a `corpus_regression_permitted` record to `policy_audit.ndjson` in that same
  directory, then deactivates the override — but only **after every file in its scope has been
  written once** (per-batch, tracked in a `.corpus_regression_progress.json` sidecar so
  multi-file projectors and the multi-step chain don't strand mid-run).
- **Wired `guard_write` into every per-output-file write** in the six projectors. The
  append-only `policy_audit.ndjson` write in `project_policy` is deliberately **not** guarded
  (it is an audit stream, not a snapshot).

## Extractor per file (primary count → guarded quantity)

| File | Projector | watermark | primary count |
|---|---|---|---|
| `findings.json` | project_findings | — | `observation_count` |
| `associations.json` | project_associations | `evidence_watermark` | `association_count` |
| `capabilities.json` | project_associations | `evidence_watermark` | `capability_count` |
| `coverage.json` | project_coverage | `evidence_watermark` | `observation_count` |
| `capacity_estimates.json` | project_capacity | — | `observation_count` |
| `prediction_accuracy.json` | project_capacity | — | `observation_count` |
| `experiment_candidates.json` | project_experiments | — | `source_findings` |
| `experiment_results.json` | project_experiments | — | `plan_count` |
| `policy.json` | project_policy | `evidence_watermark` | `source_findings` |
| `known_good_models.json` | project_capacity | — | — → **UNGUARDED** (see DECISION-NEEDED-A2.md) |
| `known_bad_models.json` | project_capacity | — | — → **UNGUARDED** (see DECISION-NEEDED-A2.md) |

Files without an `evidence_watermark` key simply resolve that side to `None`, so only their
count is guarded. `known_good/bad_models.json` carry neither and are written unguarded; the
proposed `len(entries)` extractor and the rationale for holding off are in DECISION-NEEDED-A2.md.

## How to verify

- `python -m unittest tests.workflow.test_corpus_guard` → `Ran 7 tests … OK`.
- `python -m unittest discover -s tests/workflow` → `Ran 148 tests … OK` (141 baseline + 7 new).
- The simulated incident: `test_incident_regression_blocked_findings` projects the full fixture
  corpus, then re-projects a 3-file subset — the guard raises and `findings.json` is unchanged.
  `test_incident_regression_blocked_policy` does the same through `materialize_policy`'s
  findings-path signature.

## For a reviewer

- **`knowledge/*.json` was intentionally NOT re-projected** by this stream. The guard is
  code-only; running the projectors over the repo corpus is exactly the operation that caused
  the incident, and the hand-restored knowledge store is left as-is. The guard changes only
  *whether* a projector may overwrite, never *what* it computes.
- In normal operation there is no active override, so no audit record and no progress sidecar
  are ever produced — zero footprint until someone authors a regression.
- Opens gate `G0` (`tools/workflow/corpus_guard.py` now exists).

## Out of scope

- Schema changes; changing what any projector computes.
- Guarding `known_good/bad_models.json` (decision deferred — DECISION-NEEDED-A2.md).
