# BUILD-NOTES-C1 — capacity-observation raw model fields

Curator pass (landed locally from the approved STREAM C1 prompt). The fleet `pour-c1`
winner did touch the right files (schema + a schema test) but wrapped them in the
destructive `knowledge/*.json` rewrite and `retro.md` junk shared by the whole wave, so
this was re-authored cleanly from the stream; the fleet branch informed the test shape.

## Mission

The constitutional review's Δ2 verdict: `observed.physical.model_residency` is an authored
warm/cold *classification* hiding inside telemetry. Producers should record RAW facts; the
classification should be projected downstream. Fix the contract now, before any producer
populates the field.

## Phase 0

- `grep -rn model_residency fixtures/ tools/ tests/` → no hits. Nothing populates it yet, so
  the change is free (no producer/fixture to migrate). Confirmed the field had only
  `type` + `enum` (no `description`).

## Change: `observed.physical` block, before → after

Before — `model_residency` was the only model field, an unexplained enum:

```json
"model_residency": {
  "type": ["string", "null"],
  "enum": [null, "cold_load", "warm_resident", "evicted_mid_run"]
}
```

After — four RAW sensor facts added (additive, all nullable), and `model_residency` kept but
annotated as DERIVED:

```json
"model_loaded_at_start": { "type": ["boolean", "null"], "description": "Raw sensor fact: was the target model already resident when the run began. Never a classification." },
"model_load_count":      { "type": ["integer", "null"], "description": "Raw sensor fact: number of model loads during the run." },
"model_unload_count":    { "type": ["integer", "null"], "description": "Raw sensor fact: number of model unloads/evictions during the run." },
"model_load_s":          { "type": ["number",  "null"], "description": "Raw sensor fact: total seconds spent loading the model during the run." },
"model_residency": {
  "type": ["string", "null"],
  "description": "DERIVED classification — computed by projection from the raw model_* fields; producers/collectors must never set this directly.",
  "enum": [null, "cold_load", "warm_resident", "evicted_mid_run"]
}
```

Additive-only, verified:
- No `required` list touched (top-level or nested — `observed.physical` has none).
- `additionalProperties: false` preserved on `observed.physical`.
- `model_residency` kept (removal would be breaking); enum unchanged including the `null` member.
- The six existing observation fixtures stay valid unmodified (suite green, no fixture edited).

## Docs

`docs/physical-telemetry-instrumentation-findings.md` — appended a dated note at the end of
the Status section (2026-07-03) referencing the §4 priorities-table `model_residency` row and
the Status field list: `model_residency` is now projection-derived and the four raw `model_*`
fields are what collectors report.

## Tests

New `tests/workflow/test_capacity_observation_schema.py` — house structural-check style
(jsonschema NOT imported; hand-rolled `required ⊆ keys ⊆ properties` + enum + declared-type
checks). Cases:
- raw facts set with `model_residency: null` → valid;
- only raw facts (no residency) → valid;
- the four new fields exist with the exact nullable types;
- `model_residency` carries the DERIVED description and its enum (incl. null) is preserved.
Plus teeth so the validator can't pass vacuously: bogus residency value, unknown physical
key (additionalProperties), wrong-typed raw fact, and `bool`-as-integer are each caught.

## Verify

- `python -c "import json; json.load(open('contracts/capacity-observation.v1.schema.json',encoding='utf-8'))"` → parses.
- `python -m unittest discover -s tests/workflow` → `Ran 141 tests … OK` (130 baseline + 11 new).

## Out of scope

- The projection that computes the residency classification (no evidence exists yet — the
  constitution forbids scaffolding beliefs ahead of evidence).
- The collector that reports the raw fields (stream C2).
