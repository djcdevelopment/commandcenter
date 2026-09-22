# CAPACITY-REVIEW — am4-dense-27b native capacity helper

## What was built
`native_capacity.py` is a pure-stdlib helper `normalize_am4_native(payload, observed_at, now=None)`
that fail-closed normalizes the authenticated facade response
`GET /oxen/ready?alias=am4-dense-27b`. It selects the `am4-dense-27b` row and requires
`ready is True`, `status == 200`, `context_length == 131072` (int, not bool),
`parallel_slots == 1` (int, not bool), `physical_resource == am4:127.0.0.1:18090`, and model
basename `Qwen3.8-27B-Q4_K_M.gguf`. It never raises on malformed input and never multiplies a
single physical AM4 slot by alias count. On success it returns `ready True`, `parallel_slots 1`,
`gpu_placed None`, and a reason stating that HTTP readiness does not prove GPU placement.

## Test results (observed, not assumed)
Command: `python3 -m unittest test_native_capacity.py -v`
Result: `Ran 10 tests ... OK` (exit 0). All 10 cases passed:
valid ready; ready False; absent row; malformed payload; stale timestamp; future timestamp;
wrong model; wrong context (bool); wrong slot count; two aliases for one physical resource.

## Assumptions (not verified against a live service)
- The facade payload shape is exactly as given in the brief; no live endpoint was contacted.
- `now` defaults to current UTC; tests pin `now` for determinism.
- GPU placement is intentionally left unknown (`gpu_placed None`); no GPU state is fabricated.

## Ownership
Integration (wiring this helper into the facade/runner) is Codex's responsibility. This leaf
changes no service, default runner, or global configuration.
