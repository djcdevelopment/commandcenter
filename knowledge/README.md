# knowledge/ — the materialized belief layer

Two kinds of file live here, and the difference is the whole commit policy.

## Derived — regenerate, never hand-edit

Written by `python -m hearth.projection.rebuild` from the event corpus:

`associations.json` · `capabilities.json` · `capacity.json` · `capacity_estimates.json` ·
`coverage.json` · `experiment_candidates.json` · `experiment_results.json` · `findings.json` ·
`freshness_ack.json` · `known_bad_models.json` · `known_good_models.json` · `offload.json` ·
`policy.json` · `prediction_accuracy.json` · `projection_cursor.json` *(untracked — local cursor)*

Every one carries a `corpus_digest` and a `corpus_event_count`. Those two fields change on **every**
rebuild, because the corpus grows on every gateway call — so a rebuild always produces a diff, even
when no belief moved. That is the churn this policy exists to handle.

## Authored — owned by a lane, edited deliberately

`am4_catalog.json` · `candidate_worth.json` · `cloud_spend.json` · `corpus_regression_override.json` ·
`omen_catalog.json` · `operating-budget.json` · `policy_audit.ndjson` · `policy_overrides.json`

A rebuild does not touch these. `omen_catalog.json` is receipts-only (ADR-0045 P1): entries are added
from a load receipt, not from a spec sheet.

## Commit policy (proposed 2026-09-04 — reversible, formalizes what two lanes already do)

More than one lane rebuilds these files; on 2026-09-03 two separate lanes committed projection
refreshes (`c737782`, `9ce8374`). Without a rule the derived files churn against each other in every
unrelated commit.

1. **`runs/**/events.jsonl` is append-only source of truth.** Any lane commits its own appends
   freely — appends do not conflict. Do not rewrite or re-chunk someone else's history; the
   month/period chunking (`events-YYYYMM*.jsonl`, `41ba74a`) is the archive and `events.jsonl` is the
   live current-period file.
2. **Derived files ship in a dedicated `chore(knowledge):` commit** — no code, no docs. Mixing them
   buries a real belief change inside an unrelated diff. The `runs/` corpus append the digest was
   computed from may ride along in that same commit: a `corpus_digest` and the events it hashes are
   more honest together than split across two commits.
3. **Say in the subject what moved**, so the log is readable without opening the JSON:
   `chore(knowledge): gate 2 opens for omen-swap (capability_count 1 -> 2)` when a belief changed;
   `chore(knowledge): refresh projections (digest only)` when the diff is nothing but
   `corpus_digest` / `corpus_event_count`.
4. **Generated HTML views** (`HEARTH-CALL-MIX.html` and siblings) ship in the same commit as the
   belief change they illustrate. They are views — regenerate them, never hand-edit.
5. **Never hand-edit a derived file to make a number look right.** If a projection is wrong, the
   corpus or the projector is wrong. `corpus_regression_override.json` is the authored escape hatch
   and it leaves a record.

Reading the layer without rebuilding: the door's `query_offload`, `query_capabilities`,
`query_findings`, `query_capacity` return the materialized file plus its mtime.
