# Physical Telemetry Instrumentation Findings

## Status

- **Model residency tracking**: The `model_residency` field is now a derived classification computed by projection from raw telemetry fields (`model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s`). Collectors must report only the raw fields; direct setting of `model_residency` is prohibited.

- **Raw telemetry fields**: The following fields are now the canonical source of truth for model residency state:
  - `model_loaded_at_start` (boolean|null): Was the model resident at run start?
  - `model_load_count` (integer|null): Number of model loads during the run.
  - `model_unload_count` (integer|null): Number of model unloads during the run.
  - `model_load_s` (number|null): Total time spent loading the model.

- **Status field list**: `model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s`, `model_residency` (derived).

## §4 Priorities Table

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| model_residency | string | projection | derived from raw model_* fields; never set by collector |
| model_loaded_at_start | boolean | sensor | raw fact: model present at run start |
| model_load_count | integer | sensor | raw fact: number of loads |
| model_unload_count | integer | sensor | raw fact: number of unloads |
| model_load_s | number | sensor | raw fact: total load time in seconds |

> **Note**: This update aligns with the constitutional review Δ2 verdict, which mandates that classification be derived downstream from raw telemetry. The `model_residency` field is now strictly derived and must not be authored by producers.

---

*Last updated: 2026-07-03*