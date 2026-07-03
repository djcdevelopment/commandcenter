# AM4 SYCL Context Ladder - 2026-06-29

## Purpose

First Linux-native AM4 benchmark artifact for Hermes planning work.

Goal:

- prove whether SYCL multi-card works on Ubuntu AM4;
- compare stable placement modes across a real context ladder, not a shallow smoke;
- record which modes are already unstable so future runs do not repeat avoidable dead ends.

## Rig And Preconditions

- Host: AM4 / Ubuntu
- GPUs: 2x Intel Battlemage G31 / Arc Pro B70, exposed as `SYCL0` and `SYCL1`
- Model: `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`
- Backend: `~/baseline/llama.cpp/build-sycl/bin/llama-bench`
- KV: `q8_0` for K and V
- `ngl=99`
- Prompt tokens: `512`
- Gen tokens: `32`
- Repetitions: `1`
- Timeout per case: `420s`

Operational prep used for this run:

- `comfyui.service` and `am4bot.service` were stopped and disabled because both had `Restart=always` and were reclaiming the B70 render nodes.
- Both render nodes were confirmed idle before the ladder.
- `am4-hermes-facade.service` remained up; `am4-hermes-backend.service` remained down.

## Test Spec

Command surface:

```bash
CONTEXTS="0 8192 16384 32768 65536 131072" \
MODES="single0 single1 layer" \
REPETITIONS=1 \
TIMEOUT_SEC=420 \
~/am4-fleet-node/scripts/context-ladder-probe.sh
```

Stable-mode mapping:

- `single0` -> `-dev SYCL0 -sm none`
- `single1` -> `-dev SYCL1 -sm none`
- `layer` -> `-dev SYCL0/SYCL1 -sm layer -ts 1/1`

Bounded unstable-mode checks run separately:

- `row0` -> `-dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 0`
- `row1` -> `-dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 1`
- `tensor` -> `-dev SYCL0/SYCL1 -sm tensor -ts 1/1`

## Results

### Stable Ladder

| depth | single0 pp t/s | single0 tg t/s | single1 pp t/s | single1 tg t/s | layer pp t/s | layer tg t/s | notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 0 | 1205.85 | 86.92 | 1187.19 | 87.14 | 1146.01 | 84.30 | layer is slightly slower than either single-card run |
| 8192 | 612.12 | 29.12 | 604.13 | 29.09 | 607.23 | 28.64 | practical parity; layer still slightly behind |
| 16384 | 405.44 | 17.65 | 398.36 | 17.67 | 405.73 | 17.48 | layer works but does not improve one-stream throughput |
| 32768 | 241.36 | 9.91 | 237.13 | 9.90 | 240.64 | 9.86 | parity again |
| 65536 | 132.31 | 5.28 | 130.11 | 5.28 | 130.71 | 4.89 | layer drops below single-card at deeper context |
| 131072 | timeout | timeout | timeout | timeout | timeout | timeout | no measured throughput inside 420s budget |

### Unstable Modes

| mode | case | result |
| --- | --- | --- |
| `row0` | `-sm row -mg 0` at `d=16384` | segfault (`rc=139`) |
| `row1` | `-sm row -mg 1` at `d=16384` | segfault (`rc=139`) |
| `tensor` | `-sm tensor` at `d=16384` | timed out, then process terminated with segfault in bounded run |

## Findings

1. Linux SYCL multi-card `layer` mode works on AM4.
2. For a one-stream long-context workload, `layer` is not better than single-card. It is essentially parity through `32k` and slightly worse at `64k`.
3. The hoped-for `row` placement path is currently unusable in stock llama.cpp SYCL on this stack because both `main_gpu` variants segfaulted.
4. `tensor` is also not ready to treat as a service candidate.
5. `131072` context needs a different benchmark method than the current `llama-bench` ladder if we want a useful decision inside a bounded run budget.

## Interpretation

What this run does prove:

- Ubuntu removed the Windows-specific SYCL multi-card failure mode enough for `layer` split to run.

What it does not prove:

- that multi-card is the right steady-state Hermes placement on this machine;
- that KV-on-one-card / weights-on-another is currently expressible or stable through stock llama.cpp SYCL;
- that `131072` or `262144` is serviceable at acceptable latency.

Current engineering conclusion:

- treat single-card SYCL as the first reliable Hermes backend candidate;
- keep multi-card `layer` as a verified-but-not-yet-beneficial mode;
- treat `row` and `tensor` as debugging targets, not deployment candidates.

## Follow-On Bench Plan

1. Run a service-style benchmark at `131072` using `llama-server`, not only `llama-bench`.
2. Capture TTFT and real generation latency for a Hermes-like prompt path.
3. Add a no-spill observer during the run so placement claims are backed by telemetry.
4. Revisit `row` only if we are willing to debug llama.cpp SYCL or patch lower in the stack.

