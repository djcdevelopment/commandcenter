# BUILD-NOTES-C2 — Physical telemetry collector (curated synthesis)

## What shipped

- `tools/telemetry/collect_physical.py` — standalone collector that wraps a command,
  samples GPU telemetry while it runs (nvidia-smi or mock source), and writes
  `{"hardware_profile_id": ..., "physical": {...}}` to `--out`, exiting with the
  wrapped command's exit code. Hang-proof daemon-thread sampling with a per-sample
  10s timeout; warn-once degradation to all-null when nvidia-smi is unavailable.
- `tests/workflow/test_collect_physical.py` — 15 tests covering summary math,
  profile-id normalization, mock end-to-end through the CLI, missing-source
  degradation, nvidia `[N/A]` tolerance, and a structural schema-subset check.
- No wiring into reference_runner or projectors — integration is later and gated (G2).

## Lap provenance

Both pour-c2 laps were rejected by review; this branch is a curator synthesis of
their verified-good parts. The assay returned no scoreboard and the tiebreak fell
back to list order — see POUR-STATUS.md.

- **Collector** = cc-builder-1's lap (`pour-c2/cc-builder-1/lap1`, commit `97d2388`)
  taken verbatim as the base (it was verified live on the RTX 5070: exit-code
  propagation on both sources, whole-file mock semantics, back-ceil(n/2) sustained
  math, schema-key subset including the four `model_*` nulls, hang-proof sampling,
  warn-once degradation), with EXACTLY the two fixes its review ordered:
  1. `_query_nvidia` now parses each numeric CSV column INDIVIDUALLY — an
     unparseable value nulls that field only and keeps the sample; the never-used
     `fan_pct` parse was dropped. A sample is discarded only when name/driver are
     empty AND all numeric fields are unparseable. `collect_nvidia` skips per-field
     nulls per series so the rest of the sample still contributes.
  2. Module docstring: `python3 -m` example changed to `python -m` (fleet
     interpreter ruling, 2026-07-02).
- **Tests** adapted from cc-builder-2's lap (`pour-c2/cc-builder-2/lap1`, commit
  `a384889`) as material — its summary-math series and expected values were reused —
  but rewritten against cc-builder-1's actual API (`summarize(temp_series,
  power_series, clock_series)` -> dict; `_normalize_profile_id(name, driver)`;
  `collect_mock(path)`; `_query_nvidia()`), and the missing required cases were
  added: mock CLI end-to-end with exit-code propagation, missing-source
  degradation with exactly-one-warning assertion, `[N/A]` tolerance, and the
  schema-subset structural check.
- Synthesis performed by the curator; BUILD-NOTES authored fresh (cc-builder-2's
  fan-unit extension note used as material below).

## Test run

```
Ran 177 tests in 2.121s
OK
```

Interpreter: `python` (CPython 3.12.10, Windows), run from the repo root as
`python -m unittest discover -s tests/workflow`. Baseline before this stream was
162; this stream adds 15.

Note for test readers: the missing-source test simulates nvidia-smi absence by
monkeypatching the module's `subprocess.run` for the `nvidia-smi` argv only — a
PATH trick cannot hide nvidia-smi on Windows because it lives in `System32`,
which `CreateProcess` searches regardless of `PATH`.

## Extension point: fan.speed is a PERCENT, not RPM

nvidia-smi's `fan.speed` reports a percentage of maximum fan speed, not RPM. The
schema field is `fan_rpm_avg`; emitting the percent there would be a unit lie, so
the collector ALWAYS emits `fan_rpm_avg: null` from the nvidia source (honest null
beats a fabricated reading — instrumentation findings §5). Extension point: either
add a nullable `fan_pct_avg` field to `observed.physical` (additive schema change,
separate stream) or find a per-GPU max-RPM calibration to convert; until then the
column is not even parsed.

## nvidia [N/A] tolerance

On some GPUs nvidia-smi emits `[N/A]` for individual columns (fan.speed is the
common case; others can do it too). Each numeric column is parsed individually:
an unparseable value nulls THAT field only, the sample is kept, and per-series
appends skip the nulled field. A row is discarded only when it carries no
information at all (empty name/driver AND all numerics unparseable). This keeps
partial telemetry flowing instead of dropping whole samples over one bad column.

## G2 validation gate — verbatim operator command (omen, RTX 5070)

Run from the repo root on the omen node (create `runs/g2-validation/` first if it
does not exist). Wrap a real inference dispatch; example wrapping an ollama
generation against qwen3-coder:30b:

```
python -m tools.telemetry.collect_physical --wrap "ollama run qwen3-coder:30b 'Write a Python function that reverses a singly linked list, with tests.'" --out runs/g2-validation/physical.json --interval-s 2 --source nvidia
```

Expected: the wrapped generation runs to completion; `runs/g2-validation/physical.json`
contains a normalized `hardware_profile_id` (e.g. `nvidia-geforce-rtx-5070|<driver>`)
and non-null `gpu_temp_c_peak`, `gpu_temp_c_sustained_avg`, `power_w_avg`,
`power_w_peak`, `clock_mhz_avg`; `fan_rpm_avg` and all `model_*` fields null; the
collector's exit code equals ollama's. Attach the output to that run's observation
to open gate G2.

## For the reviewer

- Emitted physical keys are asserted (in tests) to be a subset of the
  `observed.physical` properties in `contracts/capacity-observation.v1.schema.json`
  with declared-nullable types; `model_residency` is never emitted (it is
  projection-derived).
- Files touched: `tools/telemetry/collect_physical.py` (new),
  `tests/workflow/test_collect_physical.py` (new), this file. No `__init__.py` in
  `tools/telemetry/` (namespace package); nothing under `knowledge/`, `fixtures/`,
  or `runs/` touched. Stdlib only.
