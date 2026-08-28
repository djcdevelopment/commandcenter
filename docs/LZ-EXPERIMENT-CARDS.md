# LZ experiment cards — Level-Zero leverage probes

**Date:** 2026-08-28 · **Posture:** /rnd (sampling for edges; vertical slices; no tests) ·
**Source findings:** [LEVEL-ZERO-LEVERAGE-BRIEF.md](LEVEL-ZERO-LEVERAGE-BRIEF.md) (P1–P7 map
1:1 to LZ1–LZ7; renamed to avoid colliding with the rotation program's W0 probes P1–P7) ·
**Kit:** `campaign\lz-probes\` (rescued tools + runners) · **Receipts:**
`E:\work\battlemage\lz-probes\lz-receipts.jsonl` · **Design review:** methodology pass
2026-08-28 (op-offload targets GPU0 only via `ggml-backend.cpp:959-968`; prompt-cache and
mmap-cold identified as the top false-positive/false-negative pair; repack extra-buft trap).

**Standing controls (every card):** env vars latch at backend registration — set before
process start; `--no-repack` on every `-ot` run (repack extra buffer type can silently
break offload eligibility or corrupt offloaded layouts); unique prompts or
`cache_prompt:false` (server prompt cache is the #1 false positive); never time rep 1
(pipeline compile); timing from server-internal `timings`, never wall clock; High
Performance power plan + fixed `--threads` for CPU-involved cells; record HAGS state once,
never flip mid-campaign; TDR (WDDM 2 s) is the first suspect on device-lost; every receipt
row records co-residency + BF6 render-queue state; `ZE_AFFINITY_MASK` is never set
(deadlocks L0 init on driver 8974).

**Window tiers:** Lap 0 = co-resident with production (LZ3, LZ4a, LZ2, LZ1 Stage 0).
Windowed = production dark (LZ1 headline, LZ6, LZ7). LZ4b = build lane, gated on LZ4a.
LZ5 = its own afternoon, zero B70 involvement.

---

## LZ1 — deep-pack prefill in the op-offload regime

**Hypothesis:** the 11.7 tok/s Flash prefill receipt is a small-prompt artifact. It was
measured on a 22-token prompt — below `GGML_OP_OFFLOAD_MIN_BATCH` (default 32), the
threshold at which the qwen38 fork GPU-executes host-resident expert matmuls per micro-batch
(`ggml-vulkan.cpp:18680-18843`, `MUL_MAT_ID` via `ne[2]`). At deep packs the offload arms
and prefill becomes transfer-bound: ~64 GB expert sweep per ubatch over ONE Gen5 x8 link
(offload always targets GPU0 — first backend that accepts, `ggml-backend.cpp:959-968`), so
ceiling ≈ 13.3 GB/s → **~107 tok/s at ub=512, scaling ~linearly with ub** (expert traffic
per ubatch is constant). Hypothesis band after efficiency: **45–105 tok/s** vs 11.7.

**Moves:** prefill tok/s; the board's "deep packs unusable" claim; whether Flash deep-pack
`files=` service is viable at the +6.3 GB operating point.

**Rig:** `E:\work\llamacpp-qwen38\build\bin\llama-server.exe` with the proven flash-ot argv
+ `--no-repack`, port 18184, `GGML_VK_VISIBLE_DEVICES=1,2`:

```
-m E:\work\battlemage\models\qwen38-flash-next\UD-IQ4_XS\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf
--alias lz1-flash -ngl 99 -sm layer -ts 1,1 -fa on -fit off -ot ".ffn_.*_exps.=CPU" --no-repack
-c 16384 -np 1 --host 127.0.0.1 --port 18184 --slots
```

**Stage 0 — mechanism (Lap 0, co-resident; `campaign\lz-probes\lz1_stage0.ps1`):**

| Cell | Config | Signed prediction |
|---|---|---|
| A | 22-tok prompt, defaults | ≈11.7 tok/s (replicates the receipt) |
| B | 22-tok + `GGML_OP_OFFLOAD_MIN_BATCH=16` | **REGRESSES to ~3–5 tok/s** — offload arms, sweeps ~64 GB for 22 tokens. Down = engagement proven. |
| C | 512-tok, defaults, mmap | page-fault-capped: expect ~≤18 tok/s (mmap is permanently part-cold — 64–80 GB expert set > 62.3 GB standby cache). NOT a verdict on offload. |
| D | diagnostic, untimed: `GGML_SCHED_DEBUG=2` | `ffn_*_exps` MUL_MAT_ID ops on Vulkan0 with cause tag `1.off` — the literal ground truth |
| E | diagnostic, untimed: `GGML_VK_PERF_LOGGER=1` | MUL_MAT_ID GPU timings present during prefill → effective H2D GB/s (bytes/ubatch ÷ upload time) = LZ3's third estimator |

**Headline — windowed (`--load-mode none` is the truth condition):** production dark
(commit check first: baseline + ~75 GB experts-in-RAM; verify pagefile state *before* the
window). Grid: prompt {512, 2048, 8192} × ub {512, 2048} (`-ub`; `-b 2048` covers both),
1 warm-up + 3 reps each, unique prompts. Interpret with `t_ubatch = T_exp + t_attn(pos)` —
declining tok/s vs prompt length is attention O(n²), not offload failure. Then the two
context cells: best cell once under mmap-warm (the beside-production memory story), best
cell once co-resident (the co-residency-immunity endpoint — CPU-expert path measured
10.6→4.1 under contention; the DMA path should hold). Optional: run the grid via
`llama-bench` (supports `-ot`, `-ub`, env) and confirm the chosen point through
llama-server for receipt lineage. Free extra: log decode tok/s vs token index once under
mmap-cold — quantifies the mmap decode tax.

**Kill / promote:** Stage 0 cell B fails to regress AND D shows no `1.off` exps lines →
offload is not engaging for IQ4_XS MUL_MAT_ID on this driver — stop, file the "can't
answer why" row, next probe is `supports_op` tracing, not more throughput runs. Headline
≥45 tok/s at 8K → promote: Flash deep-pack service at +6.3 GB commit is real; feed R8/R9
seat design. 18–45 → hybrid placement (LZ7) becomes the lever. ≤18 → the cliff is real
physics; the board's claim stands; record and stop.

---

## LZ2 — RAM-disk KV + save-path decomposition

**Hypothesis:** KV save (1.74 s) is file-I/O-dominated: buffered ≤64 MiB WriteFile chunks
strictly serial with fenced D2H copies (`llama-context.cpp:2676`, `llama-mmap.cpp:152-167`,
fence per read `ggml-vulkan.cpp:16050`). File term ≈ 1.6 s on NVMe, ~0.55 s on a ~5 GB/s
RAM disk; D2H term ≈ 0.2 s at measured 14.1 GB/s. **Save → ~0.75–1.0 s on RAM disk;
residual = the floor a file-less patch (Phase-2 hydration hooks,
`llama_state_seq_*_data_ext`/ON_DEVICE) could reach.**

**Moves:** KV save_s / restore_s in the rotation swap budget; the build-vs-skip decision on
file-less KV parking.

**Rig (Lap 0):**
1. `lz2_filewrite.ps1 -TargetDir E:\work\battlemage\lz-probes\kv-nvme` — pure file term, 3
   reps (write 2.68 GB in 64 MiB buffered chunks + warm read-back; cold read needs
   elevation to flush cache — labeled warm).
2. `lz2_kv.ps1 -SavePath E:\work\battlemage\lz-probes\kv-nvme\ -LabelSuffix nvme` — real
   save/restore ×3 (exact W0 P2 argv: knee binary, 30B, card 1, 29K-token fill). In-place
   restores (no restart) — comparable to P3's 1.19 s, labeled.
3. RAM-disk rung: OSFMount `-a -t vm -m T: -s 8G -o format:ntfs` (**install needs UAC — if
   unavailable, decomposition-only fallback and the rung waits**). Verify raw ~4–5 GB/s
   write via `lz2_filewrite.ps1 -TargetDir T:\kv` BEFORE the timed runs (OSFMount `-t vm`
   is pageable; commit is ~97/135 GB — if it pages, numbers are NVMe in disguise; ImDisk
   AWEAlloc = plan B). Then `lz2_kv.ps1 -SavePath T:\kv\ -LabelSuffix ramdisk`.
4. Defender: bound once (one timed rep with vs without an exclusion on the target dir).

**Validation:** `residual = save_s − filewrite_s` computed per target; **residuals agree
±10% → decomposition valid**, residual = fenced-D2H + serialization = file-less floor.
Disagreement → hidden term; neither number predicts the patch; stop and write the row.

**Kill / promote:** RAM-disk save ≤1.0 s and residual ≤0.4 s → promote RAM-disk slot dir
into the rotation profile now, and the file-less patch buys ≤residual — probably not worth
building. Residual ≥0.8 s → the fence-per-chunk serialization dominates; a file-less patch
alone won't fix it either — the interesting patch becomes overlapping D2H with writes.

---

## LZ3 — bandwidth calibration

**Hypothesis:** the torch-measured 13.3 GB/s pinned H2D may undercount the wire (Linux
reports 20–28 GB/s on Gen5 x8 Arc); and the single-run D2D asymmetry (2.29 vs 5.05 GB/s by
direction) is a warm-up/order artifact, not real.

**Moves:** the constants under every LZ1/LZ6/LZ7 ceiling (a 2× wire correction doubles the
LZ1 hypothesis band).

**Rig (Lap 0):**
1. clpeak (official portable Windows release, github.com/krrishnarraj/clpeak)
   `--transfer-bandwidth` — map OpenCL device index → card **by name** (3 GPUs enumerate:
   2× B70 + iGPU). Two rows: production loaded-idle vs actively-decoding (drive decode via
   a door canary during the run). clpeak is OpenCL — treat as a wire cross-check, not a
   llama.cpp calibration.
2. `lz3_d2d.py` in `E:\work\xpu-train\.venv` — ABAB-interleaved 5 reps × {64 MiB, 256 MiB,
   1 GiB}, warm-up both directions first. If asymmetry survives → reproduce once via raw L0
   ctypes (ze_enum lineage) to rule out a torch artifact; if it still survives, it's real
   and LZ7 placement should source transfers from the fast direction's card.
3. Third estimator arrives free from LZ1 cell E (perf-logger effective H2D).

**Kill / promote:** three estimators within ~20% → freeze the constant, done. clpeak ≥20
GB/s while torch says 13 → the copy path, not the wire, is the limiter — raises the LZ1/LZ6
ceilings and adds a "fix the staging path" edge for later.

---

## LZ4 — Windows unbuffered weight loads

**Hypothesis:** the 8.2 s dio-steady load is buffered-`ReadFile`-bound (~2.3 GB/s;
`--direct-io` is a no-op at the Win32 file layer, `llama-mmap.cpp:86`). Upstream PR
ggml-org/llama.cpp#26014 (`FILE_FLAG_NO_BUFFERING` + aligned buffers) measured >9 GB/s —
but its headline came from ~0.5–1 GB reads; the fork's 4×1 MiB staging pipeline would cap
an unmodified port at ~2.5–4.5 GB/s (QD1 1 MiB unbuffered ≈ transfer + 50–100 µs/IO).

**Moves:** load_s 8.2 → hypothesis 2–4 s for the 19 GB dense; every swap in the rotation
cost model.

**LZ4a — assessment (Lap 0, no build): `lz4a_readladder.ps1`** — unbuffered sequential QD1
ladder, bs {1M, 16M, 64M, 256M, 1G}, 8 GB per point, on the 27B GGUF; buffered 64M baseline
last (cache-polluting). Environment checks: `manage-bde -status E:` (BitLocker decrypts on
CPU even for unbuffered reads), Defender exclusion state for the model dir.
**Decision gate: ≥6 GB/s at a feasible chunk size → LZ4b is worth 2–3 h; else park.** The
curve also names the staging size the port must adopt and predicts load_s for the sanity
check.

**LZ4b — port + measure (staged; after gate):** new engine clone per house pattern —
`git worktree`/clone base knee `e85caa8`+`242c3cd`, **manual port** of #26014 (touches
`llama-mmap.cpp/.h`, `llama-model-loader.cpp`; fork has the LOAD_MODE enum + staging
pipeline exactly where the PR lands — a clean cherry-pick will not apply). Crux: grow
staging buffers to the LZ4a-optimal chunk (likely 4×16–64 MiB); keep the PR's small-read
buffered fallback (thousands of tiny norm-tensor reads must not go unbuffered). Build with
the committed recipe (`campaign\qwen38\scripts\prepare-engine.ps1` VsDevCmd+Ninja+MSVC
lines), emit engine-receipt.v1, **pinned knee binary never touched**. Measure:
`kit\r2_ladder.ps1` against the new binary — 3× dio-cold for the 27B AND the 30B; plus the
MoE repeat-load rung (mmap vs dio, first vs second load — discussion #18758 says mmap wins
repeat loads for bigger-than-RAM MoE; don't blanket-apply the dense win). Record the
side-effect: unbuffered loads leave page cache cold, taxing the *next* mmap-based Flash
rotation — that's a rotation-cost-model input, not noise.

---

## LZ5 — OVMS seat pool: NPU embeddings + iGPU triage *(design-only; own afternoon)*

**Hypothesis:** one Windows-native OVMS instance (v2026.3) can serve an embedder on the NPU
(preview) and a 1–3B INT4-sym triage LLM on the iGPU — zero B70 VRAM, zero
`ZE_AFFINITY_MASK` (NPU/iGPU are separate L0 UMDs, verified; OpenVINO selects by name;
GPU plugin is OpenCL) — creating the missing embeddings lane and an R9-comparable routing
seat.

**Moves:** routing (R9 comparison row: Flash vs fx99 vs NPU/iGPU on identical work);
embeddings throughput for the belief/corpus layer (no lane exists today).

**Rig:**
1. Exports (RAM/CPU-heavy — never inside a measurement window):
   `optimum-cli export openvino -m <1-3B chat model> --weight-format int4 --sym --ratio 1.0 --group-size -1`
   (channel-wise symmetric — the NPU requirement; group-wise only for <1B) + a BGE-class /
   Qwen3-Embedding-0.6B embedder export.
2. OVMS v2026.3 Windows package; one `config.json`: embedder `target_device: NPU`, LLM
   `target_device: GPU.0`. `cache_dir` ON (GPU plugin compiles OpenCL kernels at first
   load; NPU compile ~1–2 min).
3. **Health gate asserts device identity by `FULL_DEVICE_NAME`** — never trust `GPU.x`
   ordering with 2 dGPUs present; the silent failure mode is the LLM landing on a B70 and
   fighting production for VRAM. Gate = real completion + real embedding round-trip
   (port-open ≠ model-ready; the NPU analog measured a 95.9 s model load).
4. Benchmark: R9 routing task on identical events; embeddings tok/s + latency; note DDR
   coupling (iGPU eats host bandwidth — never benchmark concurrently with LZ1/6/7; numbers
   taken while Flash decodes CPU-experts are contended numbers, label them).
5. Stretch: a servable on `GPU.1` (B70) **only against idle production**, watching
   production's canary during it — the canary delta IS the data point (OpenCL-vs-Vulkan
   coexistence; Stage 7a only proved L0-vs-Vulkan).

**Kill / promote:** embedder ≥ real-time corpus needs and LLM seat beats fx99 on the R9
task → promote into backends.toml as pin-only rungs (loud health gates, ADR for the seat
pool). NPU embeddings preview broken on driver 4778 → iGPU takes both seats; NPU parks
until a driver bump (4841 available).

---

## LZ6 — GPU-from-host execution ceilings *(windowed)*

**Hypothesis:** the copy-then-execute path (op-offload DMA streaming — the exact mechanism
LZ1 relies on) and the BAR execute-in-place path (`GGML_VK_PREFER_HOST_MEMORY`) have
different, measurable bandwidth bounds on driver 8974; expressed as **effective GB/s**
(bytes-touched/token × tok/s) they transfer directly to the 1.3–1.4 GB/token Flash experts
arithmetic and decide whether any experts-from-host decode design can beat the CPU path.

**Moves:** the Q2 decode hypothesis from arithmetic to measurement; the design basis for
LZ7 and any future selective host-visible experts patch.

**Rig:** phi-4 Q4_K_M (8.3 GB, 5.4 GB of it FFN) on ONE B70, ctx ≤2048 (KV term
negligible), `--no-repack`, greedy 32-token output compared against baseline per config
(wrong-layout copies produce plausible throughput and garbage text). Three rungs, all
runtime-env, no rebuild:

| Rung | Config | Measures | Prediction |
|---|---|---|---|
| 1 | `-ot "\.ffn_(up|down)\.weight=CPU"`, defaults | CPU-execute baseline | ~8–11 tok/s (DDR5-bound) |
| 2 | same + `GGML_OP_OFFLOAD_MIN_BATCH=1` | **decode-time DMA streaming (copy-then-execute)** | ~2.4 tok/s (5.4 GB ÷ 13.3 GB/s) |
| 3 | `GGML_VK_PREFER_HOST_MEMORY=1`, full model | BAR execute-in-place bound | BAR-read GB/s ÷ 8.3 GB |

Rung 3: keep ub small — device-lost here is WDDM TDR (2 s), not instability; PREFER_HOST
allocations count against the ~64 GB WDDM shared budget (watch "Shared GPU memory" per
process; `--load-mode none` malloc'd weights do NOT count).

**Kill / promote:** rung 2 ≥ rung 1 → streaming decode is real; a selective
experts-host-visible patch has a case; feed LZ7. Rung 2 ≪ prediction → per-op DMA overhead
dominates at batch 1; decode stays CPU-executed; LZ1's prefill win (if any) stands alone.

---

## LZ7 — hybrid expert ladder *(windowed; runs AFTER LZ1 freezes the prefill config)*

**Hypothesis:** between +6.3 GB/10.6 tok/s (all experts CPU) and 60.4 GB/27.7 tok/s (all
GPU) lies a frontier; decode should fit `t_token ≈ a + b·(fraction on CPU)` — 3 middle
rungs confirm linearity or expose curvature worth chasing.

**Moves:** the Flash-beside-production frontier (tok/s per GB commit, per-card VRAM).

**Rig:** proven Flash argv + `--no-repack`, mmap held fixed across ALL rungs (mixing
load-modes makes the commit axis incomparable). Pinning via escaped, range-explicit `-ot`
regexes — matching is unanchored `regex_search`, first-match-wins
(`llama-model-loader.cpp:1180-1201`); an unescaped `blk.4.` also matches `blk.41.`.
**Interleave even/odd blocks** so both cards stay symmetric under `-ts 1,1` (contiguous
ranges strip VRAM from one card only). Validate placement every rung by counting the
"buffer type overridden" lines (= 3 × CPU-pinned layer count). Example 50% rung (even
blocks CPU):

```
-ot "blk\.(0|2|4|6|8|10|12|14|16|18|20|22|24|26|28|30|32|34|36|38|40|42|44|46)\.ffn_.*_exps\.=CPU"
```

Rungs: 0 / 25 / 50 / 75 / 100% experts on CPU + ≤2 adaptive where curvature appears.
Measure per rung: decode (≥128 tokens, 3 reps, same canary prompts as the 10.6/27.7
endpoint receipts), prefill at the LZ1-frozen config, commit GB, **VRAM0 and VRAM1**
(frontier axes are tok/s vs (commit, VRAM0, VRAM1) — commit alone hides the lopsidedness).
Per-expert (popularity) pinning is not `-ot`-reachable and has no expected edge at
10-of-512 near-uniform routing — don't burn window time on it.

**Kill / promote:** a rung ≥15 tok/s decode at ≤20 GB commit with acceptable prefill →
promote as the Flash-lite serving shape (beats the 24–32-block mmap ladder rungs at a
fraction of the memory). Linear all the way with no sweet spot → the two endpoints were
already the whole story; record and stop.

---

## Receipts & conventions

Machine rows (per trial): `{ts, probe, cell/variant/target, rep, ...metrics, coresident}`
appended to `E:\work\battlemage\lz-probes\lz-receipts.jsonl`. Verdict rows (per card, on
close): `{ts, probe, result, detail}` — same file, mirrored to the board changelog and the
relevant side-lane row on ROTATION-PROGRAM.html, then committed (docs → master, house
convention). A card whose evidence can't explain what was seen gets a "can't answer why"
row and the lap STOPS rather than repeating input (/rnd rule).
