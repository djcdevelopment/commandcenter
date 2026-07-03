## BUILD-NOTES-C2.md

### What Changed
- Created `tools/telemetry/collect_physical.py`: a standalone collector that wraps a command, samples GPU telemetry (via nvidia-smi or mock), computes physical summaries, and emits a schema-valid output with hardware_profile_id.
- Created `tests/workflow/test_collect_physical.py`: comprehensive tests for summary math, mock data handling, and edge cases.

### Why
To collect physical telemetry (GPU temperature, power, clock) during command execution for capacity observations, as required by the `capacity-observation.v1` schema. This enables accurate hardware profiling and future capacity modeling.

### How to Verify
1. Run the full test suite: `python3 -m unittest discover -s tests/workflow` (passed with 171 tests).
2. On a real GPU node, run:
   ```bash
   python3 -m tools.telemetry.collect_physical --wrap "python -c 'import time; time.sleep(5)'" --out observation.json --source nvidia --interval-s 1
   ```
   Verify `observation.json` contains non-null, schema-valid `physical` fields and a `hardware_profile_id`.

### Notes
- Used `python3` interpreter (Python 3.12) as `python` was not found.
- `fan_rpm_avg` is always null for nvidia source due to unit mismatch (fan_pct vs RPM); this is a known extension point.
- Mock mode reads entire file as a series regardless of timing, as specified.
- All outputs strictly match schema properties (additionalProperties: false).

### Verification Command for Operator
```bash
python3 -m tools.telemetry.collect_physical --wrap "python -c 'pass'" --out observation.json --source nvidia --interval-s 1
```