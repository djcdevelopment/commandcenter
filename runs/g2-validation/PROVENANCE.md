# PROVENANCE — runs/g2-validation

The G2 validation lap: first non-mock physical telemetry captured by the collector
landed in C2 (`tools/telemetry/collect_physical.py`). Opens gate **G2**.

## How it was produced

Verbatim operator command from `BUILD-NOTES-C2.md` §"G2 validation gate", run from
the repo root on **omen** (this box, Hyper-V host + RTX 5070):

```
python -m tools.telemetry.collect_physical \
  --wrap "ollama run qwen3-coder:30b 'Write a Python function that reverses a singly linked list, with tests.'" \
  --out runs/g2-validation/physical.json --interval-s 2 --source nvidia
```

- Wrapped process: a real `ollama` generation against `qwen3-coder:30b` (the promoted
  `omen-worker-1` local worker). It ran to completion and emitted the code+tests.
- Collector sampled `nvidia-smi` on a 2s interval in a daemon thread while the
  generation ran, then exited with the wrapped command's return code (**0**).
- Interpreter: CPython 3.12.10, Windows. nvidia-smi driver `591.55`.

## Files

- `physical.json` — raw collector output (`hardware_profile_id` + `physical` summary).
- `artifacts/obs_g2_validation_001.json` — the run observation, `observed.physical`
  populated field-for-field from `physical.json`; `hardware_profile_id` lifted to the
  observation top level. This is the artifact the G2 gate checks.

## Field-by-field (every value is a measured sensor reading, none hand-authored)

| field | value | source |
|---|---|---|
| `hardware_profile_id` | `nvidia-geforce-rtx-5070\|591.55` | nvidia-smi `name`+`driver_version`, normalized |
| `gpu_temp_c_peak` | 35.0 | max of `temperature.gpu` samples |
| `gpu_temp_c_sustained_avg` | 33.285714… | mean of back-half `temperature.gpu` samples |
| `power_w_avg` | 64.613571… | mean of `power.draw` samples |
| `power_w_peak` | 70.24 | max of `power.draw` samples |
| `clock_mhz_avg` | 2723.928571… | mean of `clocks.sm` samples |
| `fan_rpm_avg` | null | honest null — nvidia-smi `fan.speed` is a percent, not RPM (BUILD-NOTES-C2 §extension) |
| `model_*` | null | collector cannot observe model residency; DERIVED `model_residency` never set here |

## Honest scope / notes

- This is a **`physical-telemetry-validation`** observation, not a build workflow — it
  does NOT re-earn the `build|ollama` capability (G3 still needs a real build lap on
  `omen-worker-1` when claudefarm1 returns).
- LLM-perf fields (`runtime_s`, `tokens_per_s`, `ttft_s`, `ram/vram_gb_peak`) are null:
  the collector measures GPU physical telemetry only, not generation throughput.
- The belief store was **not** re-projected as part of opening G2 — that is a separate
  curator/orchestrator step (and passes through the corpus/fixture guards). This run
  is evidence-in-`runs/` only; no `knowledge/*.json` was hand-edited.
