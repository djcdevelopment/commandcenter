# BUILD NOTES - C1

## Summary

This change implements the constitutional review's Δ2 verdict by moving the `model_residency` classification from a producer-reported field to a projection-derived field. The raw telemetry fields are now explicitly defined in the `capacity-observation.v1.schema.json` contract, and `model_residency` is marked as derived.

## Changes

### 1. Schema Update: `contracts/capacity-observation.v1.schema.json`

- **Added new raw fields** under `observed.physical`:
  - `model_loaded_at_start` (`["boolean", "null"]`)
  - `model_load_count` (`["integer", "null"]`)
  - `model_unload_count` (`["integer", "null"]`)
  - `model_load_s` (`["number", "null"]`)

- **Updated `model_residency`**:
  - Kept existing enum: `[null, "cold_load", "warm_resident", "evicted_mid_run"]`
  - Added description: "DERIVED classification — computed by projection from the raw model_* fields; producers/collectors must never set this directly."

- **Preserved**: `additionalProperties: false`, no required list modifications, additive-only changes.

### 2. Documentation Update: `docs/physical-telemetry-instrumentation-findings.md`

- Added a new section under "Status" explaining that `model_residency` is now projection-derived.
- Explicitly listed the four raw fields that collectors must report.
- Clarified that `model_residency` must never be set directly by producers or collectors.

### 3. Test Suite Addition: `tests/workflow/test_capacity_observation_schema.py`

- Implemented structural checks following the house pattern:
  - Verified required keys
  - Checked that all properties are present
  - Validated enum membership
  - Tested type constraints (boolean, integer, number, null)
  - Validated document structure with both raw fields and null residency
  - Validated document structure with only raw fields

## Verification

- **Test Suite**: All 139 tests pass (including the new test file).
- **Schema Validity**: The schema remains valid and backward-compatible.
- **Constitutional Compliance**: All changes adhere to Clause 1 (no organizational truth authored), Clause 2 (only projections derived), Clause 18 (determinism), and Law 5 (additive, nullable-only).

## Before/After Schema Comparison

**Before**:
```json
"model_residency": {
  "enum": [null, "cold_load", "warm_resident", "evicted_mid_run"],
  "type": "string"
}
```

**After**:
```json
"model_residency": {
  "description": "DERIVED classification -- computed by projection from the raw model_* fields; producers/collectors must never set this directly.",
  "enum": [null, "cold_load", "warm_resident", "evicted_mid_run"],
  "type": "string"
},
"model_loaded_at_start": {
  "description": "Was the target model already resident when the run began?",
  "type": ["boolean", "null"]
},
"model_load_count": {
  "description": "Number of times the model was loaded during the run.",
  "type": ["integer", "null"]
},
"model_unload_count": {
  "description": "Number of times the model was unloaded during the run.",
  "type": ["integer", "null"]
},
"model_load_s": {
  "description": "Total seconds spent loading the model during the run.",
  "type": ["number", "null"]
}
```

## Next Steps

- The projection engine will be updated in a subsequent stream to compute `model_residency` from the raw fields.
- No collector changes are needed at this time; the schema update ensures future data will be correctly structured.

---

> **Author**: Agent Builder
> **Date**: 2026-07-02
> **Stream**: C1
> **Status**: Complete and Verified