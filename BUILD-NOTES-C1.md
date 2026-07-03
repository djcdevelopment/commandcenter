# BUILD NOTES: C1

## Summary

Applied additive changes to `contracts/capacity-observation.v1.schema.json` to align with constitutional requirements by moving `model_residency` from a raw telemetry field to a derived classification. Added four new nullable raw fields to capture sensor-level facts, updated the schema description, and added documentation and tests.

## Changes

1. **Added raw fields in `observed.physical`**:
   - `model_loaded_at_start` (`["boolean", "null"]`)
   - `model_load_count` (`["integer", "null"]`)
   - `model_unload_count` (`["integer", "null"]`)
   - `model_load_s` (`["number", "null"]`)

2. **Updated `model_residency`**:
   - Kept existing enum: `[null, "cold_load", "warm_resident", "evicted_mid_run"]`
   - Added description: "DERIVED classification — computed by projection from the raw model_* fields; producers/collectors must never set this directly."

3. **Updated documentation**:
   - Added note in `docs/physical-telemetry-instrumentation-findings.md` clarifying that `model_residency` is now projection-derived and collectors should report raw `model_*` fields.

4. **Added tests**:
   - Created `tests/workflow/test_capacity_observation_schema.py` with structural checks:
     - Required keys assertion
     - Keys subset of properties
     - Enum membership validation
     - Type validation for new fields
     - Two test cases: one with raw fields and null `model_residency`, one with only raw fields

## Verification

- **Test suite**: All 139 tests passed (baseline 130 + 9 new tests).
- **Schema validity**: All existing fixtures remain valid; no breaking changes.
- **Additive-only**: No required list modified; `additionalProperties: false` preserved.
- **Constitutional compliance**: All changes follow Clause 1 (no authored truth), Clause 2 (no silent caps), D18 (deterministic, re-runnable), and Law 5 (additive, nullable-only).

## Next Steps

- The projection logic to compute `model_residency` from raw fields will be implemented in a future stream (C2).
- No changes to collectors or producers are required at this time.

## Notes

- The `model_residency` field was previously referenced in `docs/physical-telemetry-instrumentation-findings.md`, but this was expected per the stream's note and not a contradiction.
- The change is fully backward-compatible: existing `model_residency` values are preserved, and new raw fields are nullable.

This change ensures that raw telemetry is captured without thresholds, and classification is derived downstream, as required by the constitution.