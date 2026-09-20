# CAPACITY-REVIEW

## Scope
Native-only helper `normalize_am4_native` in `native_capacity.py` (pure stdlib).
It normalizes the authenticated facade payload from
`GET /oxen/ready?alias=am4-dense-27b` into a readiness dict. No service, runner,
GPU, or global-config changes are made here.

## Test results (verified, not assumptions)
`python3 -m unittest test_native_capacity.py` -> **Ran 11 tests, OK** (exit 0).
Covered: valid ready, ready False, absent row, malformed payload, stale
(-5s) timestamp, future (+30s) timestamp, wrong model, wrong context_length
including bool, wrong slot count, two aliases for one physical resource
(parallel_slots stays 1), and conflicting duplicate rows (fail closed).

## Assumptions (not verified against a live service)
- The facade payload shape is exactly as given in the brief; no live call was
  made (no network/services allowed).
- `observed_at` is a timezone-aware ISO8601 string; age window is [-5,+30]s.
- GPU placement is **explicitly unknown**: `gpu_placed` is always `None` and the
  success `reason` states that HTTP readiness does not prove GPU placement.
- Multiple aliases for the same physical AM4 resource never raise
  `parallel_slots` above 1; only the single selected `am4-dense-27b` row counts.

## Integration
Integration (wiring this helper into the facade/runner path) is **Codex's
responsibility**, not part of this leaf deliverable.

## Deliverables
- `native_capacity.py`
- `test_native_capacity.py`
- `CAPACITY-REVIEW.md`
