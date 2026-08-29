# Factory Frontier experiment cards — FF1–FF10

**Date:** 2026-08-29 · **Posture:** /rnd (sampling for edges; vertical slices) ·
**Status:** plan of record for the next research layer; extends, does not replace, the
rotation program (R-series) and the Level-Zero campaign (LZ1–LZ8b).
**Receipts:** `E:\work\battlemage\ff-probes\ff-receipts.jsonl` + `corpus/runs/` manifests ·
**Board:** ROTATION-PROGRAM.html side-lane rows · **Cards format:** identical to
[LZ-EXPERIMENT-CARDS.md](LZ-EXPERIMENT-CARDS.md) (Hypothesis / Moves / Rig / Kill-promote).

---

## 0 · Where this layer sits

Three campaigns now run against the same two B70s. They are **not** peers; they stack:

| Layer | Campaign | Unit of measurement | Question |
|---|---|---|---|
| Mechanism | LZ1–LZ8b | GB/s, tok/s, seconds | *What is the hardware physically doing?* |
| Serving topology | R0–R10 / W-phases | tok/s, commit GB, swap seconds | *Which model should be resident where?* |
| **Factory (this)** | **FF1–FF10** | **completed R&D work per occupied machine-hour** | *Which whole configuration produces the most finished autonomous work?* |

**The unit change is the entire point of this layer.** Every existing card measures
throughput. None measures *work*. A configuration can win every tok/s row and still lose
the factory, because it rebuilds context four times, burns a window on re-prefill, or
occupies both cards while producing one merged change.

### 0.1 · The load-bearing integration fact

`corpus/schema/bench-row.v1.json` already normalizes the configuration axes this brief
names — `model`, `model_quant`, `context_size`, `concurrency`, `n_gpu_layers`,
`split_mode`, `tensor_split`, `placement`, `topology`, `device_kind`, `device_count`,
`replica_id`, `engine_build`, `kv_type`, `flash_attn`, `threads`, `task_family`,
`workload`, `hw_id`, `card_identity`, `commit_headroom_bytes`, plus `confidence`, `valid`,
and `failure_class`. `corpus/schema/run-manifest.v1.json` carries hardware fingerprint,
engine build commit, device resolution, model SHA-256, flags, environment, and a
`telemetry` block with an explicit `complete` flag.

**Therefore: this campaign does not run a new throughput sweep.** The denominator largely
exists. What is missing is a numerator (work completed and its quality), an occupancy
measure, and a join. New machine-hours go to FF1–FF5 and the FF6 telemetry, not to
re-measuring tok/s across a config grid we have already crossed many times.

### 0.2 · Already answered — do not re-run

Reps from 2026-08-20→29 have closed or narrowed most of the configuration space. Re-running
these buys nothing; cite the receipt instead.

| Brief item | Status | Authority |
|---|---|---|
| Single vs dual B70; replica-per-card | **Measured** — 1.85× symmetric two-server scaling; replica topologies executed | OMEN-LIMIT-TEST-2026-08 Stage 5 |
| Heterogeneous co-residency | **Measured** — 30B@card1 + 27B@card2 held solo rates (99.2 / 21.6 tok/s) | W0 probe P5 |
| Quantization + context ladder, 3.8 family | **Measured** — `do_not_promote` (12/15); operating-point inversion reproduced in 2 harnesses | qwen38 campaign; `corpus/backfills/qwen38-campaign-full-*` |
| Context value at depth | **Partly measured** — 27B-vs-MoE jobs/hour 0.687× / 2.63× / 5.49× at 512 / 8K / 32K | campaign backfill jsonl |
| Memory placement ladder (`-ngl` blocks) | **Measured** — 4.86 / 7.41 / 9.24 / 11.66 / 16.38 / 27.70 tok/s at 0/16/24/32/40/48 blocks; 60.4 GB at full | Flash-Next placement ladder |
| Load/swap vs simultaneous residency | **Measured** — KV save 1.74 s, restore 1.19 s, ~3 s round trip vs ~100 s re-prefill; swaps drain | W0 P2/P3/P7 → ADR-0040 |
| Weight-load cost | **Measured** — 30B 7.9–8.3 s, 27B 8.5–12 s, Flash mmap 15.5→38.9 s by placement | 44 launch logs |
| Prefix/KV reuse value | **Measured** — ~306× prefix-miss penalty (39.75 s → 0.13 s at 26K); 157× for 27B+MTP | ROTATION-PROGRAM receipts |
| Host-RAM expert residency | **Measured (endpoint)** — `-ot exps=CPU` runs beside production at zero pagefile tax; file-backed experts never become commit | R0 / LZ1 Stage 0 |
| Flash prefill "cliff" | **Refuted** — 512-tok prefill 32.6–52.5 tok/s co-resident; 11.7 was a first-eval + 22-token artifact | LZ1 Stage 0 |
| Wire bandwidth | **Frozen** — ~13 GB/s H2D per B70 (3 estimators agree); D2D symmetric ~6.7 GB/s | LZ3 |
| B70↔B70 P2P | **Dead by silicon** — `canAccessPeer=0` | LZ brief Q1 |
| iGPU participation in serving | **Retracted** — CPU experts (23–24 tg) beat the hop-taxed iGPU hybrid at idle; whole 30B-A3B on iGPU = 13 tok/s | LZ8/LZ8b, commit `ff1d926` |
| NPU as router LLM | **IGNORE** — single-digit tok/s class + ~1–2 min compile | LZ brief; NPU-20/21 are compile-admission probes only |
| SYCL as production backend | **IGNORE**, with named re-check triggers | LZ brief Q6 — see FF7 |
| Unbuffered weight loads ([#26014](https://github.com/ggml-org/llama.cpp/pull/26014)) | **Parked** — E: is drive-bound at ~3.0 GB/s | LZ4a |

### 0.2b · Two axes we have *not* crossed, surfaced 2026-08-29

Reviewing [discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593) against
our own corpus exposed two axes that are in the schema but unpopulated. Both are cheap and both
can move a FF10 row.

| Axis | Our state | Why it matters |
|---|---|---|
| **KV cache quantization** | ~~`kv_type` is `"f16"` in all 1638 corpus rows~~ → **throughput half MEASURED 2026-08-29 (FF4)**; ceiling half still open. | 27593 reports q4_0 KV fitting **131072** context on a *single* 32 GB B70 (f16 49152, q8_0 65536). Our 131072/262144 quarantine is a **dual-split thermal** limit, so single-card + quantized KV remains untested. **Result so far: KV quant is not a speed lever** (see FF4) — its entire case is the ceiling, and that needs a window. |
| **SYCL built with F16** | Never built. Our SYCL verdict rests on third-party numbers whose build flags we did not control. | See FF7 — `-DGGML_SYCL_F16=ON` defaults **OFF**, and is reported worth 3.72× on prefill. |

**Standing controls (inherited verbatim from the LZ cards, every FF cell):** env vars latch
at backend registration — set before process start; `--no-repack` on every `-ot` run; unique
prompts or `cache_prompt:false`; never time rep 1; timing from server-internal `timings`,
never wall clock; High Performance power plan + fixed `--threads` for CPU-involved cells;
record HAGS state once; TDR (WDDM 2 s) is the first suspect on device-lost; every receipt
row records co-residency + BF6 render-queue state; `ZE_AFFINITY_MASK` is never set.

**Amendment 2026-08-29 (b) — assert placement, never assume it.** Every FF cell claiming a
multi-card placement must capture the per-device `model buffer size` lines and **verify both
cards are non-zero before any timing is trusted**. Learned the hard way: `llama-bench`'s `-ts`
takes **slash**-separated values (`-ts 1/1`). Passing `-ts 1,1` parses as the single value `1`,
loads the entire 17524.42 MiB model onto `Vulkan0`, leaves `Vulkan1` empty — and emits **no
warning at all** while returning entirely plausible numbers. That is the silent-fallback failure
mode in its purest form, and it mislabeled a full lap of cells before a verbose load log caught
it. LZ7 already mandates counting `buffer type overridden` lines to validate `-ot` placement;
this is the same rule for `-ts`, and it would have caught the error at cell 1 instead of cell 4.

**Amendment 2026-08-29 — the noise floor is the repeat spread, not the sample stddev.**
Measured in FF4: `llama-bench`'s stddev over 3 *consecutive* samples is 0.01–0.13 (≈0.03–0.4%),
while the **repeat-to-repeat spread of the identical condition minutes apart is 1.6%** on
prefill. Consecutive-sample stddev understates real noise by ~50×. **Every FF cell runs its
grid at least twice and uses the between-repeat spread as the noise floor.** Reporting
confidence from back-to-back samples manufactures false positives — the exact failure §3's
confidence contract exists to prevent. This is why FF6's +34.4% is trustworthy (far above
1.6%) and FF4's prefill "differences" are correctly called flat.

### 0.3 · Three concerns stated once, then executed around

1. **The numerator is an instrument that does not exist yet.** Everything downstream of
   FF1 is a comparison, and a comparison across a noisy work-slice is noise. FF1 is
   therefore gated on a *measured replay variance bound* before any FF5 config comparison is
   believed. A slice never observed replaying consistently is a hypothesis, not a benchmark.
2. **Power telemetry is known-degraded and degrades silently.** `corpus/verdict.py:186-190`
   records the Z890 case: the bus-4 B70 reported 206 samples against bus-9's 824 because
   IGCL power telemetry kept dropping out on one slot, and nothing in b70tools' own verdict
   noticed — it silently degraded every power and thermal number attributed to the quieter
   card. Every FF6 saturation figure **gates on `verdict.py`'s `symmetry_check` ratio** and
   records it. A missing power sample is `null`, never `0`.
3. **The SYCL ask splits in two.** Q6 already answers *SYCL as a production backend*:
   IGNORE, on four independent receipts including our exact shape (dual-B70 MoE layer split
   ignores `--tensor-split` → single 25.4 GB alloc → OOM). That verdict is not re-litigated
   here; it is re-checked only by its own cheap canary. *SYCL as an attribution oracle* is a
   different question that the IGNORE does not answer — an instrument does not need to be
   fast to be informative. FF7 does the oracle half only.

### 0.4 · The denominator: what "occupied machine-hour" means

OMEN is not a benchmark rig. It simultaneously carries production serving, the BF6 render
lane, and campaign windows, and `hearth/media/occupancy.py` establishes that **media and
compute occupancy are independent dimensions**. A configuration that finishes work while
leaving a card free for renders is worth strictly more than one that does not.

Occupancy is therefore recorded as a vector, never a scalar:

```
occupancy = { b70_0_s, b70_1_s, cpu_core_s, ddr5_peak_gb,
              commit_peak_gb, render_lane_blocked_s, wall_s }
```

`useful work per occupied machine-hour` is reported against **each** axis, and the Pareto
set in FF10 is computed over the vector. Collapsing this to "GPU-hours" is what produces a
false universal winner.

### 0.5 · Upstream lane — the two live threads this campaign feeds

**Citation rule (house):** always full URLs. A bare `#27652` auto-links into commandcenter's
own issue space and 404s.

| Thread | What it is | What FF owes it |
|---|---|---|
| [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652) — *vulkan: add `GGML_VK_MMV_MAX_COLS` override for mul_mat_vec dispatch* (ours, **open**) | Replaces the hardcoded `static constexpr uint32_t mul_mat_vec_max_cols = 8;` with a runtime env override. Measured: Mistral-Small-24B on Arc Pro B70 at ub=12 — MMV cols=12 **5.8 s/pass**, cols=16 **3.1 s/pass**, matmul cols=8 **40.7 s/pass**. | **The maintainer's stated blocker is our FF8 rung-2 deliverable**: the default stays 8 and *no per-vendor defaults are proposed without measurement data*. FF6's surface is exactly that data. |
| [Discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593) — B70 SYCL tuning report (third-party, open) | Single Arc Pro B70, **Linux** (xe kernel 7.0.0-28-generic, Level Zero 1.15.39122), Qwen3.8-27B-Q8_0. | Generates four hypotheses for FF4/FF6/FF7 and one cross-lane lead for R7 — **all Linux-sourced, none yet true on our box**. |

**What 27593 actually reports** (verified against the source, 2026-08-29 — it is *not* a
prefix-cache patch, despite the title's suggestion):

- `-DGGML_SYCL_F16=ON` (**defaults OFF**): pp2048 **389.2 → 1446.8 tok/s = 3.72×**. Full build
  line: `-DGGML_SYCL=ON -DGGML_SYCL_TARGET=INTEL -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=bmg_g21 -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx`
- Vulkan on the same box: prompt processing **2.2× slower** than that SYCL build.
- `-ub 2048`: **+35%** prefill. `-fa 1`: **+6%** prefill.
- KV `q4_0` → **131072** context on one 32 GB card (f16 → 49152, q8_0 → 65536).
- MTP via `--spec-type draft-mtp`, **no separate draft model**: 50.92 t/s (3.2×) at n-max 6;
  33.53 t/s (2.1×) at n-max 3.

⚠ **The platform gap is the whole story, and we have direct precedent for it.** Every number
above is Linux. LZ3 already established that Linux-class Arc figures do not transfer to this
box — the Linux-class 20–28 GB/s wire did not materialize on Windows 8974, where we measured
~13 GB/s across three independent estimators. Treat 27593 as a **hypothesis generator with a
named platform gap**, never as a result. Nothing from it enters a FF row without a Windows
re-measurement, and any cell that reproduces there is a finding in its own right.

⚠ **Do not compare 27593's SYCL numbers to our Vulkan corpus directly.** Their 1446.8 pp2048 is
27B *dense* Q8_0 on *one* card; our production control is 30B-A3B *MoE* Q4_K_M dual-split at
pp512 = **2399 tok/s** (depth 0), 469 (depth 8192), decode 112.5 — different model, quant,
depth, prompt length, and card count. The only sound comparison in 27593 is its **internal**
Vulkan-vs-SYCL delta on one unchanged box.

**Cross-lane, MTP — 27593 corroborates a number we already own, and it is not the blocker.**
27593's `--spec-type draft-mtp` result (3.2× at n-max 6, no separate draft model) lands almost
exactly on our own measurement: [ADR-0038](adr/0038-a-verdict-cites-only-evidence-from-the-configuration-it-promotes.md)
records MTP roughly tripling the 27B dual-production shape, **510 → 1591 jobs/hour (~3.1×)**.
Two platforms, two backends, same model family, same answer — so the *mechanism* is settled
and needs no further measurement.

⚠ **Do not read this as unblocking MTP-on.** The blocker is ADR-0038's own rule — every
deterministic assay row feeding that scorecard was measured **MTP-off**, so a verdict may not
cite it for an MTP-on configuration. Unblocking is a **quality re-run on the MTP-on config**,
not a throughput question. What 27593 adds is confidence that the re-run is worth scheduling.

**The separate R7 (Flash) question is now ANSWERED — 2026-08-29, zero GPU.** The check was a
GGUF metadata read; it took minutes and it closes a registered blocker with a definite
architectural reason.

| Model | Arch | Blocks | `nextn` tensors | Reading |
|---|---|---|---|---|
| Flash-Next UD-IQ4_XS | `qwen4exp` | 48 (512 experts, 10 used) | **0 of 1224** | **No built-in MTP head.** |
| Qwen3.8-27B Q4_K_M | `qwen35` | 64 | 0 of 851 | Base carries none either… |
| `mtp-Qwen3.8-27B-Q4_0.gguf` | `qwen35` | **65** | 4 of 18 (`blk.64.nextn.*`) | …because the 27B's head ships as a **separate sidecar** (base 64 + 1 nextn layer). |

⚠ **A regex nearly produced a false positive here.** Flash scores 192 "MTP-ish" tensor hits —
every one is `hc_attn_{down,inject,norm,up}`, a `qwen4exp` architecture tensor present on all
48 blocks. Match on **`nextn`**, not on a loose pattern, or Flash looks MTP-capable when it
is not.

**Consequences, in order of value:**

1. **27593's "no separate draft model required" does not transfer to Flash.**
   `common/speculative.cpp:1293-1300` documents three modes and the no-sidecar one —
   *"neither (qwen35 / qwen35moe): a single trained MTP head"* — is a **qwen35-family**
   property. Flash is `qwen4exp`. The claim was true and simply not about our model.
2. **W3's failure is now fully explained.** The community sidecar refused with `hc_attn_norm`
   missing because a `qwen4exp` draft target requires `hc_attn_*` on every block and a
   qwen35-layout sidecar has none. That was a layout mismatch, not a bad artifact.
3. **Two of R7's three registered paths are dead.** *"cafe as a 2nd binary"* — dead, the
   mismatch is tensor layout, not the binary. *"extract the head ourselves"* — dead, there is
   no head in Flash's weights to extract. Only *"wait for fork-native"* survives, and it is
   now precisely stated: **waiting for someone to publish a `qwen4exp`-layout MTP draft
   carrying `hc_attn_*`.** That is an ecosystem dependency, not a local task — so R7 should
   stop consuming window time and become a watch item.

Receipts: `E:\work\battlemage\ff-probes\ff-receipts.jsonl`, probe `FF-MTP`.

---

## FF1 — the replayable long-horizon R&D slice *(keystone; everything else depends on it)*

**Hypothesis:** a long-horizon autonomous R&D task can be made replayable enough that the
same work, run under different configurations, differs by less than the effect sizes we
intend to measure. If replay variance swamps configuration effects, the entire factory
question is unanswerable by this method and we should learn that in one afternoon rather
than after ten windows.

**Moves:** whether FF3/FF5/FF10 mean anything at all. This is the gate on the campaign.

**Design — the slice is a *re-run of a probe whose true answer we already hold.*** Do not
invent a synthetic task. Pick a completed LZ/R probe with a full receipt trail, strip the
repo back to its pre-probe commit, and hand the agent the original card's hypothesis. This
solves the scoring-oracle problem that normally sinks agentic benchmarks: **we already know
the correct verdict, the correct failure, and the evidence a correct answer must cite.**

Two candidate slices, both carrying a genuine failed hypothesis and recovery:

- **Slice A — LZ4a (unbuffered weight loads).** True verdict: gate FAILED, E: is drive-bound
  at ~3.0 GB/s, the [#26014](https://github.com/ggml-org/llama.cpp/pull/26014) port is PARKED. Contains a real dead end reached honestly.
  Requires: planning, a read ladder, measurement, a negative result, and a park decision.
- **Slice B — LZ8→LZ8b (iGPU expert venue).** True verdict: mechanism real but quant-gated,
  then RETRACTED at idle when CPU experts beat the hop-taxed hybrid. Contains a
  **reversal** — the agent must first find a positive, then overturn it. Harder, and the
  better test of continuity.

Run Slice A first (cheaper, unambiguous); promote Slice B once A's variance is bounded.

**Scored dimensions** (all recorded per run, never collapsed to pass/fail):

| Class | Fields |
|---|---|
| Outcome | `verdict_match` (vs the known true verdict), `evidence_cited`, `artifact_quality`, `regressions_introduced` |
| Time | `wall_s`, `prefill_s`, `decode_s`, `human_intervention_s` |
| Cost | `tokens_in`, `tokens_out`, `b70_0_s`, `b70_1_s`, `cpu_core_s`, `commit_peak_gb`, `ddr5_peak_gb` |
| Process | `context_rebuilds`, `human_interventions`, `repeated_mistakes`, `rework_cycles`, `tests_run`, `tests_passed` |
| Stability | `device_lost_events`, `server_restarts`, `thermal_aborts` |

`verdict_match` is scored by a rubric written **before** any run and frozen with the slice —
same freeze discipline as the Promotion Gate. Rubric authorship is a human act, once.

**Rig:** slice repo state pinned by commit; the agent gets the card's Hypothesis and Moves
sections and nothing downstream of them; all receipts land in `corpus/runs/<run_id>/` with a
full `run-manifest.v1` so the config identity joins to `bench-row.v1` rows.

**Kill / promote:** run Slice A **5× on one fixed configuration** (the current production
default) before any comparison. Coefficient of variation on `wall_s` and on the rubric score
≤ 25% → the slice is an instrument; proceed to FF2–FF5. CV > 25% on the rubric score → the
task is not replayable at this granularity; **stop, and either constrain the slice further
or abandon the work-per-hour framing for a proxy metric.** Do not proceed to config
comparison on an uncalibrated instrument.

---

## FF2 — orientation tax

**Hypothesis:** the compute and wall-clock cost of reconstructing project state after a
context reset or handoff is large, measurable, and **asymmetric across workflow shapes** —
and it is currently invisible because every existing receipt measures a warm agent.

**Moves:** the context-length and workflow-shape decisions in FF5; whether "short context +
frequent reset" is cheap in practice or only cheap in memory.

**Definition (frozen):**

```
orientation_tax = (tokens + wall_s + b70_s) consumed between a context reset
                  and the first subsequent action that advances the slice's
                  scored state, minus the same measured for a warm agent at
                  the identical slice position.
```

The subtraction is what makes it a tax rather than a startup cost. "Advances the scored
state" is defined by the FF1 rubric, so this metric inherits FF1's calibration and cannot be
computed before FF1 passes.

**Rig:** instrument the slice harness to force a reset at three fixed slice positions
(early / mid / late — after the failed hypothesis is the interesting one). Measure the tax at
each. Vary the external-memory scaffold: none / plain notes file / structured state doc.

**Kill / promote:** tax measurably grows with slice position → continuity has compounding
value and FF3 is worth running in full. Tax flat across positions → context reset is a fixed
cost, structured external memory is sufficient, and the very-long-context arm of FF5 can be
cut to a single confirmatory cell.

---

## FF3 — continuity dividend

**Hypothesis:** retaining experiments, failures, decisions, code history, and evidence
*directly in context* produces measurable improvement over reconstructing them from
artifacts — and the improvement concentrates in the **recovery** phase after a failed
hypothesis, not in the initial build.

**Moves:** the central FF5 comparison; the context-length-versus-cost frontier in FF10.

**Definition (frozen):** the paired difference in FF1 rubric score and in `rework_cycles`
between an agent that carried the failure in-context and one that re-derived it from
artifacts, at identical slice position and identical model/quant/placement. **Paired on the
slice, not averaged across slices** — the effect is expected to be smaller than
cross-slice variance.

**Rig:** the same slice run twice per configuration, differing only in whether the
failed-hypothesis segment remains in context at the recovery point. Slice B (the reversal) is
the discriminating case; Slice A may show nothing, and a null on A is not a null on B.

**Kill / promote:** dividend positive and larger than FF1's replay CV → long-context arms
earn their prefill and memory cost, and FF10 gets a real "context length worth paying for"
answer. Dividend inside the noise band → **structured external memory is as good as
context**, which is the single most decision-changing negative result available in this
campaign — it would collapse the FF5 space to shapes 1, 2, and 4 and cut the memory budget
for the whole lab.

---

## FF4 — prefill amortization

**Hypothesis:** there is a break-even context depth beyond which the prefill and KV cost of
ingesting more context is not repaid by the autonomous execution it enables — and, given the
~306× prefix-miss penalty, that break-even moves sharply depending on whether the context is
*reused across turns* or re-ingested.

**Moves:** "best context length before marginal capability stops paying" (FF10); the KV
manifest / rotation design's caller model.

**Definition (frozen):**

```
prefill_amortization = scored_slice_progress_after_ingest / (prefill_s + kv_bytes_cost)
```

reported per context point, with `kv_bytes_cost` expressed in the same occupancy vector as
FF1 (commit GB-seconds), not as a bare byte count.

**KV quantization is a first-class axis here, and we have never varied it** (§0.2b: all 1638
corpus rows are `kv_type: f16`). 27593 reports q4_0 KV reaching **131072** on a single 32 GB
card where f16 stopped at 49152 and q8_0 at 65536 — i.e. the KV dtype, not the card, was the
context ceiling. Since `kv_type` is already a `bench-row.v1` field, adding it costs a flag and
a re-run, not a schema change. ⚠ Quantized KV trades accuracy for depth: pair every KV-quant
cell with the FF1 rubric score, or the extra context will look free when it is not.

**Rig:** the FF1 slice at context points **8K / 32K / 64K / 128K**, crossed with
`kv_type` ∈ {f16, q8_0, q4_0} — plus 256K **only if a config admits it without spilling** (R0: commit is 96.9 GB of a 135.3 GB limit with
production up; and depths 131072/262144 are thermally quarantined on dual-split, so a 256K
dual-split cell is a *thermal* refusal, not a capability datum). Each point measured twice:
cold-prefix and warm-prefix, so the 306× reuse factor enters the amortization explicitly.

**Reuse note:** the throughput half of this curve is largely already in the corpus — the
27B-vs-MoE jobs/hour ladder (0.687× / 2.63× / 5.49× at 512 / 8K / 32K) and the qwen38
operating-point inversion. FF4 supplies only the numerator; join, don't re-measure.

**Kill / promote:** a knee appears below 128K → publish it as the lab's default context and
size the KV manifest to it. Monotonic improvement to the memory ceiling → context is
capacity-limited, not value-limited, and the interesting lever moves to FF9's hierarchy.

### FF4 first result — KV quantization is not a speed lever *(2026-08-29, Lap 0, co-resident)*

30B-A3B Q4_K_M, **single-B70** (see amendment b — this cell carried the same `-ts` mislabel;
all three arms shared the identical placement, so the comparison stands), `-ub 512`,
depth 8192, `-r 3` × 2 repeats:

| test | f16 | q8_0 | q4_0 | reading |
|---|---|---|---|---|
| pp512 @ d8192 | 461.4 | 461.1–468.5 | 465.9–472.6 | **flat** — within-type repeat spread (1.6%) exceeds every between-type gap |
| tg128 @ d8192 | **33.23** | 31.68 (**−4.7%**) | 32.28 (**−2.9%**) | real, reproducible to 0.4% |

- **Quantized KV costs decode and buys no prefill.** Adopting it for speed would be a
  mistake; its entire value proposition is the **memory/context ceiling**, which a co-resident
  cell structurally cannot test. The 27593 ceiling claim is still open and needs a window.
- ⚠ **Non-monotonic in bit-width: q4_0 decode *beats* q8_0 by +1.9%, reproducibly.** The q8_0
  dequant path costs more than q4_0's on Vulkan/Battlemage. That is a mechanism observation,
  not a benchmark artifact, and it is a **candidate upstream note** — nobody would predict the
  wider type being slower.
- Also banked: decode falls **122 → 33 tok/s** from depth 0 to 8192 (both co-resident).

Receipts: `ff-receipts.jsonl`, probe `FF4-kv`.

---

## FF5 — workflow architecture bake-off *(the headline comparison)*

**Hypothesis:** workflow shape dominates model choice for long-horizon autonomous work. A
mid-tier model in the right workflow finishes more scored slices per occupied machine-hour
than the best model in a naive one.

**Moves:** everything in FF10's "best long-horizon autonomous configuration" row; the seat
design for R8/R9.

**Arms** (each run against the frozen FF1 slice, identical rubric):

| # | Shape | Notes |
|---|---|---|
| 1 | Short context + frequent checkpoint/reset | Pays FF2's tax repeatedly; cheapest per-turn |
| 2 | Medium context + structured external memory | The FF3-null-hypothesis arm |
| 3 | Very long continuous context | Pays prefill + KV once; FF4 decides if it repays |
| 4 | Planner → builder → reviewer handoffs | Three orientation taxes; parallelism possible |
| 5 | Long-lived primary + specialized secondaries | The rotation program's natural shape |
| 6 | Empirical hybrid | Authored only after 1–5 report; not pre-specified |

**Config cross:** do **not** cross all six arms against the full config space. Cross them
against **three** configurations chosen from existing receipts as the representative corners
— current production default (30B-A3B dual-split), the single-card 27B depth specialist, and
the best Flash-lite hybrid rung LZ7 promotes. Nine-plus arms × three configs is the entire
window budget; a full cross is not affordable and would not change a decision.

**Kill / promote:** an arm wins on scored work per occupied machine-hour by more than FF1's
CV → promote it as the lab's default autonomous shape and record the margin. All arms inside
the noise band → workflow shape does *not* dominate at this slice length, and the honest
finding is that the slice is too short to discriminate — lengthen it before concluding
indifference.

---

## FF6 — prefill saturation surface + the saturation oracle

**Hypothesis:** prefill fails to drive the B70s into the sustained high-power regime that
generative image/video workloads demonstrably reach, and the gap is a *software dispatch*
property — insufficient exposed parallel work per submission — rather than a hardware
ceiling. Making the reference regime explicit turns "the GPU seems idle" into a measured
duty cycle.

**Moves:** whether the prefill investigation is chasing a real headroom or a physical
ceiling; which upstream contribution in FF8 is worth authoring.

**The oracle:** the BF6 render lane already drives these exact cards through sustained
high-compute operation on the same host, the same driver, and the same telemetry path. It is
the reference for what a saturated B70 looks like on this box — no new workload needs
building. Capture a render-lane telemetry trace once and freeze it as the saturation
reference profile.

**New metrics (frozen definitions):**

```
time_to_saturation   = seconds from prefill start until board power crosses
                       (reference_p50_power × 0.9) for 3 consecutive samples
saturation_duty_cycle = fraction of prefill wall time spent above that line
```

Both are `null` — not `0` — whenever the telemetry gate below fails.

**Telemetry gate (mandatory, per §0.3.2):** every FF6 row records `verdict.py`'s
`symmetry_check` sample-count ratio between the two identical cards. Ratio below a frozen
floor → the row's power-derived fields are `null` and the row is marked
`partially_scored`. Thermal and clock figures attributed to the quieter card inherit the
same flag. This is the one place the campaign is most likely to fool itself.

**Surface axes:** prompt length × `-b`/`-ub` × concurrency × dense-vs-MoE × single-vs-dual
B70 × stock-vs-tuned dispatch. Correlate PP tok/s against board power, clocks, memory
behavior, kernel/dispatch timing, submission gaps, and CPU utilization **where each is
actually available** — and record which were `null` per row rather than dropping the row.

**Two named candidate points from 27593, pre-checked against our own corpus:**

- **`-ub 2048` (+35% prefill claimed).** Our production control runs `n_ubatch: 512` with
  `n_batch: 2048` — so this is a **genuinely untested delta on our box**, and it sits on the
  axis FF6 already sweeps. Promote it to an early cell; it is one flag.
- **`-fa 1` (+6% prefill claimed).** ⚠ **Already on for us** — `flash_attn: true` in the
  production control rows. There is no gain to collect here; do not schedule a cell for it.

The difference between those two is the reason the corpus is checked before the sweep is
designed: one is free headroom, the other is a number we already banked.

### FF6 result — the `-ub 2048` win is a **single-card artifact** *(2026-08-29, corrected)*

⚠ **Read the correction first (§ standing controls, amendment b).** The initial FF6 run passed
`-ts 1,1`, which `llama-bench` parsed as the single value `1`, loading the whole model on one
card with no warning. Those numbers are valid **as single-B70 results**; the placement label was
wrong. The corrected dual-split run reverses the conclusion.

30B-A3B Q4_K_M, `GGML_VK_VISIBLE_DEVICES=1,2`, `-r 3`, co-resident, **3 repeats**, placement
asserted (`Vulkan0` 8975.63 + `Vulkan1` 8548.79 MiB, both non-zero, `tensor_split 1.00/1.00`):

| test | ub 512 | ub 2048 | Δ | noise floor | call |
|---|---|---|---|---|---|
| pp512 | 2378.06 | 2383.44 | +0.2% | 1.50% | within noise |
| **pp2048** | **2529.25** | **2447.36** | **−3.2%** | 0.64% | **REAL — a loss** |
| tg128 | 105.10 | 104.06 | −1.0% | 1.20% | within noise |

**Production recommendation RETRACTED. Do not raise `-ub` on the serving config.**

**Why the reversal:** dual-split already captures what `-ub 2048` bought on one card. At
`ub 512`, pp2048 is **2529.25 dual vs 1783.43 single (+42%)** — the micro-batching penalty that
a larger ubatch repaired on a single card **does not exist across two**.

**Single vs dual at `ub 512` — layer split trades decode for prefill:**

| test | single-B70 | dual-split | Δ |
|---|---|---|---|
| pp512 | 2373.57 | 2378.06 | ~0% |
| pp2048 | 1783.43 | **2529.25** | **+42%** |
| tg128 | **121.90** | 105.10 | **−14%** |

That is a real, reproducible tradeoff and it **answers FF10's "best use of dual B70s" row**,
which the mislabel had only raised as a question.

**The irony is worth recording.** 27593 measured a *single card* — so the `-ts` mistake
accidentally reproduced their exact topology, and reproduced their number almost exactly
(**+34.4% vs their +35%**). Their claim is correct *for their configuration*; it simply does
not transfer to our dual-split production shape. The cards' platform-gap caution was aimed at
Linux-vs-Windows. **The axis that actually mattered here was card count.**

**Feeds FF8 rung 3:** the correct ubatch depends on **placement** as well as prompt shape, so
no per-architecture constant can be right — which is a stronger argument for shape-aware
selection than the original (wrong) reading was.

Receipts: `ff-receipts.jsonl`, probes `FF6-ub` (single-card), `FF-CORRECTION`,
`FF6-ub-dualsplit`.

### FF6c — the crossover surface: **the optimum is `ub 1024`** *(2026-08-29)*

Prompt × ubatch × placement, prefill-only (decode measured unaffected by ubatch), 2 repeats,
placement asserted on both arms (`layer 1.00/1.00` / `none 0.00`). **Prefill tok/s:**

| | ub 256 | ub 512 | ub 1024 | ub 2048 | best |
|---|---|---|---|---|---|
| **dual-split** pp512 | 2085.0 | 2344.8 | **2380.0** | 2356.3 | ub1024 |
| **dual-split** pp2048 | 2012.6 | 2541.7 | **2709.2** | 2462.9 | ub1024 |
| **dual-split** pp8192 | 1067.0 | 1227.3 | **1242.8** | 1089.1 | ub1024 |
| single-card pp512 | 1700.5 | **2386.3** | 2370.8 | 2311.8 | ub512 |
| single-card pp2048 | 1304.4 | 1756.0 | 2125.6 | **2334.3** | ub2048 |
| single-card pp8192 | 658.4 | 824.6 | 963.8 | **1042.9** | ub2048 |

**`ub 1024` wins at every prompt length on production's shape** — and it is a value neither our
earlier cells nor 27593 ever tested. Against production's current `ub 512`: **pp2048 +6.6%**
(floor 1.02%, REAL), pp8192 +1.3% (floor 0.06%, REAL), pp512 +1.5% (inside a 2.71% floor).
Against the `ub 2048` I wrongly recommended: **+10.0% at pp2048 and +14.1% at pp8192.**

**Placement changes the optimum's shape-dependence.** On a single card the best ubatch *moves
with prompt length* (512 → 2048 → 2048). On dual-split it is *pinned* at 1024 across all three.

**Mechanism — dual-split and large ubatch are substitutes.** Both supply work per dispatch, so
the dual/single ratio shrinks monotonically as ubatch grows:

| | ub 256 | ub 512 | ub 1024 | ub 2048 |
|---|---|---|---|---|
| pp2048 | 1.54× | 1.45× | 1.27× | 1.06× |
| pp8192 | 1.62× | 1.49× | 1.29× | 1.04× |

Once the pipe is full from either source the other stops paying — and past the optimum, doing
both *regresses*.

**A second, independent signal says 2048 crosses a regime boundary.** The compute-buffer base is
**exactly** linear from ub512→ub1024 (V1 316.80 → 633.59, precisely 2×) but **superlinear** to
ub2048 (→ 1587.19, 2.5×, 25% above linear), while the context slope stays exactly
`ub × 7.629e-6 MiB/token` at all three values. Throughput and allocation agree independently
that 2048 is past a threshold — stronger than either alone.

⚠ **Production recommendation: `-ub 512` → `1024`.** Measured cost at `c=131072`: V0 612 → 1224
MiB, V1 829 → 1658 MiB (+0.60 / +0.81 GiB); totals 16.2 / 15.7 GiB against a 32558 MiB card,
~16 GiB spare. Buffers at ub1024 are **measured** at two contexts, not interpolated. **The one
remaining gap is `-np 2`**, still untested because `llama-bench` has no `-np` — that is what
stands between this evidence and a config edit.

**Feeds FF8 rung 3 in exactly the form 27652's maintainer asked for:** the crossover is not a
per-vendor constant but a function of prompt length **and** placement *on one vendor's card*. No
architecture-keyed default can express *"ub1024 always on two cards, but 512→2048 by prompt
length on one."*

Receipts: `ff-receipts.jsonl`, probes `FF6c-surface`, `FF6c-buffers`.

### FF5b — VRAM was never the constraint *(2026-08-29)*

Compute buffer, dual-split, measured at two contexts:

| n_ctx | ub | Vulkan0 | Vulkan1 | Host |
|---|---|---|---|---|
| 2048 | 512 | 108.04 | 324.80 | 16.05 |
| 2048 | 2048 | 432.16 | 1619.19 | 64.22 |
| 18432 | 512 | 172.04 | 388.80 | 80.05 |
| 18432 | 2048 | 688.16 | 1875.19 | 320.22 |

Fits an exact two-term model per card: `compute(ub, ctx) = base(ub) + ub × 7.629e-6 MiB/token`.
**The context slope scales with ubatch** (64 MiB per 16384 tok at ub 512; 256 MiB at ub 2048 —
exactly 4×), so the ubatch penalty *grows with context* and cannot be read off a short-context
run. KV is linear in context (192 MiB @ 2048 → 1728 @ 18432, ratio exactly 9).

Extrapolated to production's `-c 131072`: ubatch delta **+1.79 GiB (V0) / +2.74 GiB (V1)**;
totals ub512 15.6 / 14.9 GiB vs ub2048 17.4 / 17.6 GiB — **both well inside the 32558 MiB card
with ~14 GiB spare either way.** So memory never was the blocker; throughput was.

⚠ Caveats: (1) a **fit extrapolated 7× beyond the measured range** — it reproduces both points
exactly, but it is not a measurement at 131072; (2) `llama-bench` has no `-np`, so the `-np 2`
interaction is **untested** — compute buffer is sized by total ubatch so it should not multiply,
but that is reasoning, not evidence; (3) Windows Vulkan reports a **per-process** budget, so
these are what a server *would* allocate, not an observation of live free VRAM.

⚠ `GGML_VK_PERF_LOGGER=1` **crashes the qwen38 fork** (known). Kernel-timing attribution on
Flash-family models needs a different instrument or a fork fix; do not plan a cell that
silently depends on it.

**Kill / promote:** duty cycle rises materially with exposed work (larger ub, higher
concurrency, dual-card) → the headroom is real and FF8's ladder is the payoff path. Duty
cycle pinned low across the whole surface while the render oracle saturates the same cards →
the bottleneck is submission-side, and the credible upstream target is dispatch/submission
batching, not kernel tuning. Duty cycle already high while tok/s is low → the cards *are*
saturated and prefill is bound by something other than compute; stop hunting dispatch.

---

## FF7 — Vulkan ↔ SYCL attribution, and the F16 confound

**Hypothesis (revised 2026-08-29):** the "brutally bad SYCL on Battlemage" evidence our Q6
IGNORE rests on was very likely measured **with `GGML_SYCL_F16` at its OFF default**, making
it a confounded comparison rather than a backend verdict. Where a Vulkan↔SYCL prefill gap
survives a matched-flag build, it is attributable to a specific mechanism (dispatch shape,
XMX engagement, memory residency), and naming that mechanism is worth more than the ratio.

**Moves:** the confidence behind Q6's IGNORE; whether FF8's ladder has a target; whether an
upstream Vulkan issue can cite a concrete unused capability rather than a benchmark delta.

**The arithmetic that motivated the revision.** Q6 cites Gemma-4-26B on B50/B70 at **SYCL 351
pp vs Vulkan 1169**. [Discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593)
reports `-DGGML_SYCL_F16=ON` worth **3.72×** on prefill on this exact card class. 351 × 3.72
≈ **1306** — the same class as the 1169 it supposedly lost to. Different model and box, so
this is *suggestive, not proof*; but it is a coherent explanation of the entire reported gap,
and it means the receipt we treated as decisive may be measuring a build flag.

**What does and does not change.** Q6's IGNORE for SYCL **as a production backend still
stands on independent grounds** that F16 cannot touch: our exact shape (dual-B70 MoE layer
split) ignores `--tensor-split` and attempts a single 25.4 GB allocation → OOM
([#22885](https://github.com/ggml-org/llama.cpp/issues/22885), closed not-planned), and
Battlemage **Windows** correctness produced garbled output where Vulkan was correct
([#20169](https://github.com/ggml-org/llama.cpp/issues/20169), closed not-planned). What
changes is our *confidence in the throughput half* of that verdict, and therefore **Q6's
re-check triggers are amended**: add *"a matched-flag (`GGML_SYCL_F16=ON`) head-to-head has
never been run on our box."*

**Rig — cheapest decisive cell first:**

1. **Build cell.** SYCL build with the 27593 line, adapted to Windows/oneAPI:
   `-DGGML_SYCL=ON -DGGML_SYCL_TARGET=INTEL -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=bmg_g21`
   (`icx`/`icpx`). ⚠ This is a **toolchain acquisition**, not a runtime flag — budget it as a
   build-lane task, not a window. If the Windows oneAPI build does not produce a working
   binary in one sitting, **stop and record that**: "SYCL is not buildable here today" is
   itself the answer to the production question and closes the card.
2. **Matched head-to-head.** Dense model, **one** B70, small context, identical prompts,
   matched `-b`/`-ub`, Vulkan vs SYCL-F16. Correctness gate first — greedy output compared
   against the Vulkan baseline, per the [#20169](https://github.com/ggml-org/llama.cpp/issues/20169) garbling risk. A fast wrong answer is not a
   datum.
3. **Attribution.** Compare PP tok/s **and** the FF6 saturation pair. SYCL reaching a higher
   duty cycle at equal or lower tok/s is the attribution signal: it indicates Vulkan is
   leaving submission-side work unused, not arithmetic.

**Kill / promote:** SYCL-F16 fails to build or fails the correctness gate on Windows →
**Q6's IGNORE is reconfirmed on stronger grounds than before**; record and close. Matched
build shows Vulkan competitive → the "brutal" receipts were a flag artifact; say so, and the
oracle lane closes having removed a bad assumption from our own record. A reproducible
duty-cycle gap with a nameable mechanism → author the upstream issue with the render-lane
oracle trace attached as the "this hardware can do this" control.

---

## FF8 — dispatch selection: from constant to measurement

**Hypothesis:** [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652)'s finding — that
a static Vulkan dispatch threshold (`static constexpr uint32_t mul_mat_vec_max_cols = 8;`)
leaves large performance on the table and that the correct crossover is architecture-dependent
— generalizes into a ladder, and each rung is independently shippable upstream.

**Moves:** which upstream contribution to author next; whether the lab ships a B70 constant
(bad) or removes an assumption (good).

**The ladder** — the point is that each rung *removes an assumption* rather than adding a
special case:

| Rung | Change | Status | Upstream character |
|---|---|---|---|
| 0 | Static threshold `= 8` | upstream today | The assumption under test |
| 1 | Runtime override (`GGML_VK_MMV_MAX_COLS`) | **already authored — [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652), open** | Trivially safe; makes the constant *measurable by users* |
| 2 | Architecture-aware selection | **next target; blocker named by the maintainer** | Encodes that one constant is provably wrong cross-vendor |
| 3 | Shape-aware selection | not started | Threshold depends on matrix shape, not just device |
| 4 | Measured / autotuned dispatch | not started | One-time probe at load; no constant survives |

**Rung 1 is already shipped as a proposal — the campaign's job is rung 2's evidence.** The
maintainer's position on 27652 is explicit: the default stays 8, and **no per-vendor defaults
are proposed without measurement data**. That is not an objection, it is a specification. FF6's
saturation surface — prompt length × `-b`/`-ub` × concurrency × dense-vs-MoE × single-vs-dual
card × stock-vs-tuned, with the crossover located per architecture — is precisely the dataset
that unblocks rung 2. Publishing it is worth more than another B70 number.

**Standing rule:** cross-vendor evidence already indicates a universal threshold is unlikely
to be correct (ftoleedo validated on RDNA3.5, per the knee campaign). **Prefer discovering
and removing a bad assumption over adding a B70-specific hack.** A patch that helps only our
cards is a lab hack; a patch that makes the threshold measurable helps every vendor and is
far likelier to land.

⚠ Register discipline for the 0cc4m lane is unchanged: reply only when addressed, short,
Derek-typed. Never ghost-write PR prose.

**Kill / promote:** the crossover measurably differs across ≥2 architectures → post that data
to 27652 as the per-vendor evidence the maintainer asked for, and rung 2 becomes authorable.
Crossover turns out to be uniform outside the B70 → the finding is narrower than we believed;
**say so in the existing PR rather than escalating** — a narrowed claim, volunteered, is worth
more to that review than a defended one.

---

## FF9 — MoE memory hierarchy: prove or kill cheaply

**Hypothesis (to be killed if it does not pay):** B70 VRAM = hot tier → 128 GB DDR5 = warm
expert tier → NVMe = cold/backing tier is a *useful* hierarchy for MoE, and CPU-driven
routing/prefetch/residency management can hide enough DDR5/PCIe latency to make
host-resident experts worth serving from.

**Moves:** whether Flash-family models are servable beside production at a useful operating
point; the Flash-lite seat design.

**Do not restart this from zero — most of it is already carded or measured:**

- The two endpoints exist: `-ot exps=CPU` (+6.3 GB, ~10.6 tok/s, zero pagefile tax,
  file-backed experts never become commit) and full residency (60.4 GB, 27.7 tok/s).
- The frontier between them is **LZ7** (rungs 0/25/50/75/100% experts on CPU) — already
  designed, windowed, and gated on LZ1 freezing the prefill config.
- The decode-side ceiling for any experts-from-host design is **LZ6** (copy-then-execute DMA
  vs BAR execute-in-place).
- The wire is frozen: ~13 GB/s H2D per B70; D2D symmetric ~6.7 GB/s; P2P dead by silicon.

**FF9 therefore adds exactly one thing LZ6/LZ7 do not cover: the *prefetch* question.** LZ7
measures static placement; FF9 asks whether CPU-side prediction of expert residency can beat
static placement at the same memory budget.

**Rig:** at LZ7's best static rung, add a CPU-side expert-residency predictor driven by
observed routing, and compare against that rung at identical commit GB. Keep CPU work in
scheduling, prefetch, batching, and memory movement — **not** GEMM. The 10-of-512
near-uniform routing already noted in LZ7 is the strongest prior *against* this working:
uniform routing gives a predictor nothing to exploit.

**Kill / promote:** predictor beats static placement by more than the LZ7 rung spacing at
equal commit → a residency manager is worth building. Within noise, or routing measured
near-uniform → **kill the tier idea explicitly and record it**; the hierarchy is then a static
placement question LZ7 already answers.

### FF9 status — DOWNGRADED *(2026-08-29)*

⚠ **Correction to this card's own claim.** It said routing entropy is "a cheap read" that could
kill FF9 before any window. **It is not cheap on this build.** The routing tensor is emitted
only through the graph callback (`src/llama-graph.cpp:2058-2060`, cb names `ffn_moe_argsort` /
`ffn_moe_topk`), there is no env-gated expert dump in the Vulkan or CPU path, and
**`llama-eval-callback` is not built** in our pin (`build/bin` has only bench, cli, server).
Measuring entropy is a **build-lane task**, not a Lap-0 read.

**What *is* free is an analytical bound, and it is unfavorable.** Under uniform routing the
distinct-expert working set per layer saturates fast:

| model | experts | used | 50 tokens | 100 tokens | 500 tokens |
|---|---|---|---|---|---|
| 30B-A3B (`qwen3moe`) | 128 | 8 | **96.0%** | 99.8% | 100% |
| Flash-Next (`qwen4exp`) | 512 | 10 | 62.7% | **86.1%** | 100% |

A VRAM tier holding a *subset* is therefore missing almost constantly past a few dozen tokens
unless routing is both strongly skewed **and** temporally stable. Uniform is the worst case for
the tier, so this bounds rather than settles it — but it relocates the burden of proof onto FF9.

**It also converges with a call LZ7 already made** on the same reasoning: *"per-expert
(popularity) pinning is not `-ot`-reachable and has no expected edge at 10-of-512 near-uniform
routing — don't burn window time on it."* FF9's prefetch variant is that idea with a predictor
bolted on, and the arithmetic supports LZ7. It also explains why `-ot exps=CPU` performs as well
as it does: if nearly every expert is touched anyway there is no subset worth caching — you
stream them all or hold them all, which is exactly the two endpoints LZ7 measured.

**Decision: do not schedule the prefetch cell.** If revived, the prerequisite is building
`llama-eval-callback` and demonstrating skew large enough to keep the per-layer working set well
under the tier size. That is the gate, and it is a build task, not a window.

---

## FF10 — STOCK LAB vs TUNED LAB, and the Pareto set

**Hypothesis:** there is no universal winner, and the honest deliverable is a Pareto set over
the occupancy vector plus a stated rule for choosing among its members.

**Moves:** the lab's default configurations; what goes in `backends.toml`; what gets written
up publicly.

**Two matrices, identical axes, differing only in configuration source:**

- **STOCK LAB** — default/runtime-baseline: stock dispatch, stock batching, default
  placement, no tuned concurrency, no KV manifest reuse.
- **TUNED LAB** — best validated dispatch (FF8), batching (FF6), placement (LZ7/FF9),
  caching and KV reuse, backend, concurrency, and workflow shape (FF5).

The pair is what makes the campaign publishable: the delta between them is the measured
value of everything this lab has learned, stated as work per machine-hour rather than as
tok/s.

**Required Pareto answers** (each names a configuration *and* the axis it wins on):

- Best interactive configuration
- Best long-horizon autonomous configuration
- Best throughput configuration
- Best quality per GPU-hour
- Best single-B70 configuration
- Best use of dual B70s
- Best context length before marginal capability stops paying for its prefill/memory cost
- When a larger model beats a smaller one despite lower tok/s
- When multiple specialized agents beat one long persistent context
- Whether host RAM / iGPU / NPU add meaningful lab capacity *(prior: iGPU serving already
  retracted; NPU narrow to embeddings/triage — a positive here would be a surprise and
  should be treated as one)*
- Which optimizations exposed previously unused B70 compute
- Which remaining bottlenecks are credible upstream llama.cpp / Intel-runtime targets

**Kill / promote:** the Pareto set collapses to one member across every axis → say so
plainly; a genuine universal winner is a finding, not a failure. Members that differ only
inside FF1's CV are **not** distinct members and must be merged rather than reported as
choices.

---

## Receipts, invariants, and upstream readiness

**Rows.** Machine rows per trial appended to
`E:\work\battlemage\ff-probes\ff-receipts.jsonl` in the LZ convention
(`{ts, probe, cell/variant, rep, ...metrics, coresident}`), **plus** a full
`run-manifest.v1` per slice run under `corpus/runs/<run_id>/` so every factory row joins to
the existing `bench-row.v1` corpus on `hw_id` + `engine_build` + config axes. Verdict rows
per card on close, mirrored to the ROTATION-PROGRAM.html side lane and committed (docs →
master, house convention).

**Invariants to preserve for upstream credibility:** exact runtime/driver/build/model
versions (`run-manifest.v1` already carries engine `build_commit`, model `sha256`, device
`resolved`, and the hardware fingerprint); reproducible command lines verbatim; the telemetry
`complete` flag and the FF6 symmetry ratio; co-residency and render-queue state per row;
and the frozen definitions of orientation tax, continuity dividend, prefill amortization,
time-to-saturation, and saturation duty cycle. A metric whose definition moved mid-campaign
is not comparable and must be re-run or dropped.

**Null discipline (carried from the routing-harvest contract):** `null ≠ 0` anywhere in this
campaign. Unmeasured telemetry, absent power samples, and skipped cells propagate as `null`
with a `partially_scored` flag; they never become zeros in a mean. A card supported by two
of five measured dimensions is not equal-confidence to one supported by five.

**Stop rule (/rnd).** A card whose evidence cannot explain what was seen gets a "can't answer
why" row and the lap **stops** rather than repeating input.

---

## Sequencing

| Order | Card | Window tier | Gate |
|---|---|---|---|
| 1 | FF1 Slice A ×5 | Lap 0, co-resident | **Campaign gate** — CV ≤ 25% or stop |
| 2 | ~~FF9 routing-entropy read~~ | ⚠ **NOT cheap — reclassified 2026-08-29** | Needs `llama-eval-callback` built; **FF9 downgraded** on the analytical bound instead |
| 3 | ~~R7 `draft-mtp` head check~~ | ✅ **DONE 2026-08-29** | Flash has no head; 2 of 3 R7 paths dead (§0.5) |
| 4 | ~~`-ub 2048` cell~~ | ✅ **DONE 2026-08-29** | **+34.4% on pp2048**; shape effect → rung 3 (FF6) |
| 5 | ~~KV-quant ladder q8_0/q4_0~~ | ✅ **DONE 2026-08-29** | Not a speed lever; ceiling half still needs a window (FF4) |
| 5b | ~~`-ub 2048` VRAM/slot cost~~ | ✅ **DONE 2026-08-29** | VRAM never the blocker; the win was a single-card artifact — **change retracted** |
| 5c | **Dual-split decode/prefill tradeoff** | Lap 0, deliberate cell | −14% decode for +42% prefill measured in passing; FF10 needs it measured on purpose |
| 6 | FF6 oracle trace capture | Lap 0 (render lane already runs) | Telemetry symmetry gate |
| 7 | FF2 / FF4 | Lap 0 | Needs FF1 pass |
| 8 | FF3 (Slice B) | Lap 0 | Needs FF2 |
| 9 | FF7 SYCL-F16 build cell | **Build lane, not a window** | Stop-and-record if it won't build |
| 10 | FF5 bake-off | Windowed | The expensive one; needs FF1–FF4 |
| 11 | ~~FF9 prefetch~~ | ❌ **DESCHEDULED 2026-08-29** | Working set saturates (96% of experts in 50 tok on 30B-A3B); converges with LZ7's existing call |
| 12 | FF8 rung-2 evidence → 27652 | Follows FF6 | The maintainer's named blocker |
| 13 | FF10 synthesis | Desk work | Needs all above |

Steps 3–5 are new as of 2026-08-29 and are deliberately ahead of most of the campaign: each is
cheap, each was surfaced by reading the two upstream threads against our own corpus, and each
can change a decision before any window is spent.

FF1 first is not a formality. Every card from FF2 onward divides by it.
