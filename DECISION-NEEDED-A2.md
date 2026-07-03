# DECISION-NEEDED-A2 — extractor for known_good/bad_models.json

This is a decision request, not a blocker. Stream A2 is complete and landed; this records one
open choice for Derek.

## The gap

`corpus_guard.guard_write` blocks a projection write when its evidence **watermark** is older or
its **primary count** is smaller than the file on disk. Every guarded knowledge file exposes at
least one of those two quantities. Two files do not:

- `knowledge/known_good_models.json` — shape `{contract_version, entries: [...]}`
- `knowledge/known_bad_models.json` — shape `{contract_version, entries: [...]}`

Neither carries an `evidence_watermark` nor a count field. Per the stream's instruction ("do NOT
invent one"), both are currently written **unguarded** in `project_capacity.materialize_knowledge`
(the other two capacity files — `capacity_estimates.json` and `prediction_accuracy.json` — are
guarded on `observation_count`).

## Why leaving them unguarded is defensible for now

- They are *classifications derived from* `capacity_estimates` (which IS guarded on
  `observation_count`). If the estimates can't regress unnoticed, the classifications built from
  them can't silently regress from a shrunken corpus either — the upstream guard covers the same
  incident at the source.
- Inventing a count field on these files would be a schema/shape change; A2's mandate is
  "only whether projectors may overwrite," not "what they compute."

## Proposed extractor (for the decision)

If you want them guarded directly, the natural monotone quantity is entry count:

```python
make_extractor(None)                       # would give no count (entries has no dedicated key)
# proposed instead — a small extractor that reads len(entries):
lambda doc: (None, len(doc.get("entries", [])))
```

Wiring that into the two unguarded branches of `project_capacity` is a ~4-line change. It would
add count-regression protection (watermark stays N/A for these files). Left out for now because
`entries` count is not necessarily monotone the way `observation_count` is — a model correctly
dropping out of `known_good` on new adverse evidence would *legitimately* shrink the list, and
the guard would then false-positive block a healthy projection.

## Decision requested

Pick one:
1. **Leave unguarded** (current state) — rely on the upstream `capacity_estimates` guard. Recommended.
2. **Guard on `len(entries)`** — accept that a legitimate shrink now requires an authored override.

No code waits on this; option 1 is already in effect.
