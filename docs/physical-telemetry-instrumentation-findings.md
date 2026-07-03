# Physical Telemetry Instrumentation Findings

## Status

- `hardware_profile_id`: High priority, already implemented.
- `model_residency`: High priority, already implemented.
- `model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s`: High priority, now implemented.

### §4 Priorities Table

| Field | Priority | Status | Notes |
|-------|----------|--------|-------|
| `model_residency` | High | **Projection-derived** | Now computed from raw model_* fields; collectors report only raw facts. |
| `model_loaded_at_start` | High | Implemented | Boolean flag indicating model presence at run start. |
| `model_load_count` | High | Implemented | Count of model loads during the run. |
| `model_unload_count` | High | Implemented | Count of model unloads during the run. |
| `model_load_s` | High | Implemented | Total seconds spent loading the model. |

## Notes

- **Cold-load latency tax** — `model_residency=cold_load` correlates with elevated `ttft_s` / lower early-window `tokens_per_s` versus `warm_resident` on the same builder+model.
- `model_residency` is now a projection-derived classification. Collectors must report only the raw `model_*` fields. The derived state is computed downstream by projection.

---

*Last updated: 2026-07-02T07:00:00Z*