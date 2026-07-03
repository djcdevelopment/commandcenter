# Physical Telemetry Instrumentation Findings

## Status

This document records the current state of physical telemetry instrumentation in the commandcenter system. It reflects the latest decisions from the constitutional review and operational priorities.

### §4 Priorities Table

| Field | Type | Source | Status |
|-------|------|--------|--------|
| model_residency | string | derived | **active** |
| model_loaded_at_start | boolean | sensor | active |
| model_load_count | integer | sensor | active |
| model_unload_count | integer | sensor | active |
| model_load_s | number | sensor | active |

> **Note**: As of 2026-07-02, `model_residency` is now a projection-derived classification. Collectors must report the four raw `model_*` fields instead of setting `model_residency` directly. The classification is computed downstream by the projection engine.

## Notes

- All telemetry fields are validated against their respective JSON schemas at ingestion.
- The `model_residency` field is no longer a direct observation but a derived state.
- The raw fields (`model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s`) are now the canonical source of truth for model loading behavior.

> **Last updated**: 2026-07-02T06:55:00Z