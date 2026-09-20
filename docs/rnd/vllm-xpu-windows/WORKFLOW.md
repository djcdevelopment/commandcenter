# /rnd workflow — vLLM on the B70s, natively on Windows

Opened 2026-09-19 (session cc-e0fdf649, plan `~/.claude/plans/federated-waddling-willow.md`, approved by
Derek). Register: `docs/rnd-log.md`. Results accumulate in **Results** at the end; `WORKFLOW.html` beside
this file is hand-written from it — update both.

Reopens the vLLM question the SYCL-vs-Vulkan program parked (`docs/rnd/sycl-vs-vulkan/WORKFLOW.md`, "vLLM
— parked": *OMEN boots Linux natively, or a B70 returns to AM4*) under an **amended reopen condition: a
native Windows build path exists.** Same scope rules as that program: Windows-only, a winner may replace
anything (production `omen-arc` included), open-ended until the ladder completes, written verdict either way.

## What Derek found, and what it actually is

`SystemPanic/vllm-windows` — a native Windows port of vLLM (v0.29.0, wheel
`vllm-0.29.0+cu132-cp312-win_amd64.whl`). Desk check (L0): it is **CUDA-only** — "CUDA 13 + Blackwell GPU
support on Windows", NCCL for multi-GPU, no XPU / oneAPI / SYCL anywhere, and the one Intel-GPU question on
its tracker (#31, Jan 2026) is unanswered. Its wheel cannot see a B70.

What it *is* for us: the Windows build/runtime work (setup.py no longer refuses non-Linux; MSVC build;
`winloop` for uvloop; spawn-only workers; POSIX shims). Upstream vLLM's XPU platform is pure Python +
torch-xpu + triton-xpu + **one** out-of-tree SYCL kernel package (`vllm-project/vllm-xpu-kernels`; vLLM
compiles nothing for XPU since RFC #33214). Nobody had combined the two. OMEN already had the rest:
oneAPI 2026.1 (icx/icpx, dnnl, mkl) at `E:\omen\tensor\native\toolchain\oneapi`, and torch-xpu Windows
wheels that see both B70s.

**Hard boundary (verified L1):** `torch.distributed.is_xccl_available()` is **False** on the Windows
torch-xpu wheel — oneCCL does not ship for Windows and the toolkit has no `ccl` component. So dual-B70
**tensor parallel is off the table natively**; gloo is present. The honest dual-card shape is one vLLM
instance per card behind the door. (vllm#41663: TP=2 on dual B70 is unreliable even on Linux.)

## The ladder

| Lap | Physical step | Gate | Status |
| --- | --- | --- | --- |
| L0 | Desk: what the port supports; what a native XPU build needs | written above | **done** 2026-09-19 |
| L1 | venv + torch 2.13.0+xpu + triton-xpu 3.7.2 on Windows | 2 devices; a Triton kernel *executes* on xpu:0 | **pass** |
| L2 | `vllm-xpu-kernels` 0.1.14.1 built with icx on Windows — basic kernels | `_C.pyd` imports; norm/act/rotary match torch | **pass** (9 attempts, 78-line patch) |
| L2-full | + `_moe_C`, `_xpu_C`, `_vllm_fa2_C` (SYCL-TLA attention/grouped-GEMM + static oneDNN) | extensions import; FA2 varlen + fused MoE run | **built + import** (9 more attempts); **FA2 kernel faults the device** — see L4 |
| L3 | `vllm serve` a dense model on one B70, Triton attention, eager | correct completion over `/v1/chat/completions` | **pass** — Qwen3-0.6B, T=0 deterministic |
| L4 | production model family (Qwen3-30B-A3B int4 AWQ/GPTQ) on one card; 27B int4 | lap-8 bodies correct at 16k/30k; T=0 determinism | **FUNCTIONAL on the TLA-free stack** (`moe_wna16` + Triton MoE + Triton attention + oneDNN int4): correct, T=0 deterministic, 599 tok/s aggregate @64 streams, ~2.4k prompt tok/s @12k, needle found at 6k/12k |
| L5 | jobs/h shape (SAT-L1, 8×16k): one instance, then two (one per card) | vs production 3,358 jobs/h / 105 tok/s | next — plus XPU graphs / torch.compile (eager today) |
| L6 | stretch: `--pipeline-parallel-size 2` over gloo | the 27B at depth on both cards under vLLM | only if L5 says so |
| L7 | verdict: D1 production engine / D2 concurrency rung `omen-vllm` (:8097, pin-only stanza) / D3 depth stays llama.cpp SYCL | ADR + memory | — |

Lab tree: `E:\work\vllm-xpu-win\` — `run.cmd <cmd>` (oneAPI + MSVC + venv env), `build-kernels.cmd
<log> basic|full`, `serve.cmd <log> <model> <port> [args]`, `kill-seat.ps1 -Port N`, `probes\` (logs and
drivers). Clones on lab branches: `kernels` (branch `windows`, commit 6162726 on top of 0.1.14.1) and
`vllm-src` (branch `windows-xpu`, 372c057 on top of the fork's v0.29.0).

## Baselines the laps compare against

Production `omen-arc`: Qwen3-30B-A3B Q4_K_M, llama.cpp Vulkan knee fork, dual layer-split `-np 8`,
105 tok/s single-stream, 3,358 jobs/h at 8×16k. SYCL llama.cpp (same program): 104.9 tok/s parity on the
MoE dual shape; 27B dense prefill 614 tok/s at 119k. Continuous batching has no llama.cpp baseline at
>8 streams — the L3 sample below is the first such figure on this hardware.

## Results (filled lap by lap)

**L0/L1 (2026-09-19 ~21:30Z).** Port is CUDA-only (above). `E:\work\vllm-xpu-win\.venv`: torch
2.13.0+xpu, triton-xpu 3.7.2 (Windows wheels from `download.pytorch.org/whl/xpu`), intel runtime pkgs
2026.0.0 beside the 2026.1 toolkit. Both B70s enumerate (31.2 GB, driver 1.15.39183). Triton needs a C
compiler on PATH to JIT its driver helper (`RuntimeError: Failed to find C compiler`) → every lab command runs
through `run.cmd` (calls `llamacpp-knee\sycl-env.cmd`, sets `CC=cl`). Triton add-kernel exact on xpu:0
(13.5 s first JIT); fp16 4096³ matmul 37 TFLOPS on one card. `xccl False, gloo True`.

**L2 basic (21:45–22:05Z, 9 build attempts, each a loud refusal fixed one at a time):**
1. toolchain.cmake hard-codes `${CMPLR_ROOT}/bin/icpx` (no `.exe`) → not a compiler.
2. `icpx.exe` is the GNU-like driver; CMake's `Windows-IntelLLVM` module feeds MSVC flags (`-nologo -EHsc`)
   → use `icx.exe` for C++ too (llama.cpp's Windows SYCL recipe does the same).
3. Backslash Python path breaks `file(REAL_PATH)` → forward slashes from setup.py.
4. setup.py's `-DCMAKE_CXX_COMPILER=icpx` fought the toolchain's icx → cache reset → align on Windows.
5. GNU `-include sycl_first.h` ignored by the clang-cl-style driver, header became a second source file
   ("cannot specify -Fo when compiling multiple source files") → `/FI`.
6. `uint` (glibc `sys/types.h`) → typedef on `_WIN32` in the forced-include header.
7. `-lze_loader` ignored; the oneAPI toolkit ships **no** `ze_loader.lib`/`ze_api.h` → Level Zero Windows
   SDK 1.33.1 (`deps/level-zero-sdk`, `VLLM_LEVEL_ZERO_SDK_DIR`), linked as a library; `pthread/m/dl`
   dropped; `/usr/include` guarded. The `_C` target never received `VLLM_XPU_LINK_LIBRARIES` (Linux got
   ze_loader via the link *flag*) → passed explicitly on Windows.
Configure step fetched oneDNN rls-v3.13 and intel/sycl-tla (CUTLASS 4.2.1) cleanly on Windows. Build: 19
objects, `_C.pyd` links, installs as `vllm-xpu-kernels 0.1.15.dev0`. Probe (`probes\l2_ops.py`):
rms_norm / fused_add_rms_norm / silu_and_mul / rotary_embedding match torch (fp16 max err ≤ 0.008; bf16
0.029 = bf16 rounding on magnitude-4 values); rms_norm 4096×5120 fp16 166 µs. The package's own pytest is
not runnable on a partial build (imports `_moe_C`/`_xpu_C` unconditionally).

**L3 (22:05–22:25Z).** Dependencies: fork `requirements/common.txt` + `xpu.txt` minus torch/triton
(installed), `vllm_xpu_kernels` (local), `torchcodec`, `ray`, and **`auto_round_lib` (no Windows wheel,
native build fails — the AutoRound int4 path is out until that builds)**; plus the fork's `winloop` +
`portalocker`. `VLLM_TARGET_DEVICE=xpu pip install -e .` builds nothing native; Rust frontend skipped (no
cargo; optional). Refusals, one per launch: `_moe_C` hard import in `platforms/xpu.py` (→ optional);
`import uvloop` stray line in the deprecated `api_server` entrypoint (fork slip; `vllm serve` is its tested
path); **a directory named `vllm` in the working dir shadows the package** (renamed clone to `vllm-src`);
`fused_moe → xpu_moe → _moe_C` chain (→ optional); hung API-server parent keeps ZMQ :29550 when EngineCore
dies (→ `kill-seat.ps1` before each launch); free memory 15.26/31.16 GiB (production ArcServe holds the
rest of card 0) → utilization 0.4; FA2 backend selected by default, `_vllm_fa2_C.varlen_fwd` absent →
`--attention-backend TRITON_ATTN`. Engine facts: **distributed init picked gloo by itself** for
world_size=1; Level Zero memory query works through the SDK-linked `_C`; model 1.12 GiB, KV cache 10.51 GiB
= 98,304 tokens at 4k ctx.

Serving `Qwen/Qwen3-0.6B --enforce-eager --max-model-len 4096 --gpu-memory-utilization 0.4 --dtype
float16 --attention-backend TRITON_ATTN` on :8097 (`probes\l3_chat.py`, `l3_conc.py`; half of card 0,
production up on the other half, eager, Triton attention — a floor regime):

| streams | completion tokens | wall | aggregate tok/s | per-stream mean |
| --- | --- | --- | --- | --- |
| 1 | 256 | 4.2 s | 61 | 4.2 s |
| 8 | 1,926 | 5.8 s | 332 | 5.5 s |
| 32 | 8,090 | 4.1 s | 1,955 | 4.0 s |
| 64 | 16,262 | 4.1 s | 3,920 | 4.1 s |
| 128 | 32,666 | 4.4 s | 7,358 | 4.4 s |
| 256 | 65,476 | 4.8 s | 13,694 | 4.7 s |

Correct answer (Paris / Seine), T=0 identical over 3 runs. **Per-stream latency is flat from 1 to 256
streams** — the single stream is step-overhead-bound (eager Python, ~16 ms/step), and batching 256 costs
nothing on a 0.6B; 256 is the default `max_num_seqs`, so no knee was reached. This is the vLLM promise
(continuous batching + paged KV) running on a B70 under Windows; it says nothing yet about the 30B-A3B or
the 27B, which need L2-full.

**L2-full (2026-09-19 22:50Z → 2026-09-20 00:00Z, Derek's cue; build attempts 1–9):**
1. `-j16` → `LLVM ERROR: out of memory` in the SYCL device compile of the chunk-prefill attention
   templates (four `clang-cl -cc1 -triple spir64_gen` died at once at 700/1402). The default build
   instantiates **all 216 chunk-prefill + 384 paged-decode variants**; `chunk_prefill_default` /
   `paged_decode_default` (~13 + ~24, Qwen/Llama covered) drop the graph to 848 objects. `MAX_JOBS=6`
   holds 76 GB free. (⚠ a killed `-j16` ninja kept draining an in-flight device compile for minutes;
   two later attempts ran underneath it — check for live `clang-cl.exe` before relaunching.)
2. `ssize_t` in `mem_alloc.cpp` (xpumem_allocator) → `BaseTsd.h` typedef on `_WIN32`.
3. Every incremental re-configure discarded the cmake cache: setup.py passes `shutil.which('icx')` =
   `...\icx.EXE` while the toolchain file says `.../icx.exe` → "changed compiler" → cache deleted →
   `VLLM_PYTHON_EXECUTABLE` gone. Fix: on Windows setup.py passes no compiler (toolchain file is the
   authority). Cost: one near-full recompile when the command lines changed spelling.
4. `xpumem_allocator.pyd`: `LNK1104 python312.lib` — it drops `Py_LIMITED_API`, so `pyconfig.h`
   auto-links the versioned lib via `#pragma comment(lib)`; add `Python_LIBRARY_DIRS`.
5. `_vllm_fa2_C.pyd` / `mhc_kernels_xe_2.dll`: `LNK2019 cutlass_chunk_prefill_xe2`,
   `launch_mhc_post_opt` — the SHARED `*_xe_2` TLA libraries export nothing (no `dllexport`), so their
   import libs are empty → `CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON`. MHC additionally: the `extern`
   declaration lacks the definition's `__restrict` qualifiers, which **MSVC mangles** (Itanium doesn't).
6. Install step: `Access is denied: _C.pyd` — the L3 seat still had it loaded (`kill-seat.ps1`).
Result: `_C` 38 MB, `_moe_C` 55 MB, `_xpu_C` 86 MB, `_vllm_fa2_C`, `xpumem_allocator` — **all five build,
install and import** (kernels branch `windows` 5725348). ⚠ The `*_xe_2.dll`, `grouped_gemm_xe_default.dll`
and the compiler's `libmmd.dll` must be copied beside the `.pyd`s by hand — setup.py's extra-library
install looks for `.so` names. Registered: `is_xe2_arch()` true, `int4_gemm_w4a16`, `_vllm_fa2_C.varlen_fwd`,
`_moe_C.topk_softmax`; `FA2_AVAILABLE True`.

**L4 (00:00–00:20Z) — the production model loads; the TLA attention kernel is the wall.**
`Qwen/Qwen3-30B-A3B-GPTQ-Int4` (official Qwen int4, 15.77 GiB, pulled in 3.6 min to `E:\work\models\hf`).
Production holds ~14 GB on *each* card (layer split), so this ran in a **tenancy window**: `arc-maintenance.stop`
sentinel → `schtasks /Run /TN ArcServeRestart` (stop-only with the sentinel) → both cards 31,906 MiB free →
lap → sentinel removed → `ArcServeRestart` again → `llama-swap` + `llama-server` back, `/health` 200 (window
≈ 20 min). Seat on card 1 (`ONEAPI_DEVICE_SELECTOR=level_zero:1`), `--max-model-len 16384 --max-num-seqs 64`.
Every path engaged as designed: `XPUwNa16LinearKernel` for the GPTQ linears, `'XPU' WNA16 MoE backend` for the
experts, FlashAttention 2; **model loaded 15.68 GiB in 111 s** (17 s warm); profiling passed; KV 11.02 GiB =
120,384 tokens at 0.9 (7.35× 16k) / 69,312 at 0.75. Then the first real forward: `RuntimeError: level_zero
backend failed with error: 40 (UR_RESULT_ERROR_OUT_OF_RESOURCES)` — at 0.9 **and** 0.75, in the V2 runner's
`warmup_kernels` (surfacing in `int4_gemm_w4a16`) and in the V1 runner's `profile_run` (surfacing in the fused
MoE) — different sites, so the error surfaces at whichever op synchronises next. Isolation: `int4_gemm_w4a16`
standalone is clean at M = 1…8192; **`flash_attn_varlen_func` standalone → `UR_RESULT_ERROR_DEVICE_LOST`
(error 20)** — the SYCL-TLA attention kernel faults the B70. Same on the 2026.1 SYCL runtime pips (torch's wheel
pins 2026.0; the venv now carries 2026.1, harmless). No TDR/WHEA event logged; both cards re-enumerate at full
memory. Leading suspect: the AOT `spir64_gen` image (`-device pvc,bmg,bmg-g21-a0,bmg-g31-a0`, 2026.1 IGC) vs
driver 32.0.101.8974; the basic and oneDNN kernels don't use the 2D-block-IO / DPAS SPIR-V extensions the TLA
kernels link with. **Next probe:** JIT-only rebuild of the attention lib (`VLLM_XPU_AOT_DEVICES=""`,
`VLLM_XPU_XE2_AOT_DEVICES=""`), ~10–15 min; failing that, a driver update (Derek's call) or the
`TRITON_ATTN` backend with the int4 + MoE kernels (which may themselves be clean — untested in isolation).

**L4b (2026-09-20 13:00–13:35Z, under `/goal`) — ⭐⭐ THE PRODUCTION MODEL RUNS ON A B70 UNDER WINDOWS.**
JIT-only rebuild of the TLA libraries (`VLLM_XPU_AOT_DEVICES=none`, kernels `windows` f0525c5; ~25 min; .pyds 8×
smaller) → `flash_attn_varlen_func` still `DEVICE_LOST`, and the fused-MoE grouped GEMM standalone → error 40. **AOT
theory refuted** (clean sample); `SYCL_UR_USE_LEVEL_ZERO_V2=0`, `UR_L0_V2_FORCE_DISABLE_COPY_OFFLOAD=1`,
`UR_L0_USE_IMMEDIATE_COMMANDLISTS=0` change nothing; driver 32.0.101.8974 is Aug 2026 (IGC current). Verdict for now:
the CUTLASS-SYCL (`intel/sycl-tla`) kernels fault on this Windows driver — upstream never claimed Windows.
**Routed around it — a TLA-free stack:** `--quantization moe_wna16 --moe-backend triton --attention-backend
TRITON_ATTN` = oneDNN int4 linears (`XPUwNa16LinearKernel`, proven) + vLLM's Triton WNA16 MoE experts + Triton
attention (proven at L3). Refusals on the way: `auto_gptq` hard-codes `may_have_bias=True` so the Triton MoE refuses
("expert bias is not supported") → the `moe_wna16` method passes `False`; XPU's `supported_quantization` lacked
`moe_wna16` (one line); the vLLM banner's block glyphs crash a cp1252 console (`PYTHONUTF8=1` in serve.cmd);
**a real upstream bug** — `MoeWNA16Config.get_quant_method` builds a fresh `AutoGPTQConfig` per layer, so the
loader's `maybe_update_config` (fills `modules_in_block_to_quantize` from the checkpoint) and the model's
`packed_modules_mapping` never reach it → every fused linear reads as unquantized → `'QKVParallelLinear' object has
no attribute 'data'`; fixed by caching one inner config and delegating the hooks (vllm-src `windows-xpu` 183cf79).
Result on card 1 (`--enforce-eager --max-model-len 16384 --max-num-seqs 64 --gpu-memory-utilization 0.75`,
tenancy window as in L4): model 15.64 GiB in 19 s, KV 75,632 tokens; Paris/Seine correct; **T=0 identical ×3**;
needle at 6,342 and 11,840 prompt tokens FOUND, **~2,000–2,440 prompt tok/s incl. decode** (production's Vulkan
dual-card prefill at 16k is 609 on the dense 27B and knees hard from there — this is one card, eager). Decode:

| streams | completion tokens | wall | aggregate tok/s | per-stream |
| --- | --- | --- | --- | --- |
| 1 | 256 | 13.5 s | 18.9 | 18.9 tok/s |
| 8 | 2,048 | 23.5 s | 87 | 10.9 |
| 32 | 8,192 | 27.8 s | 294 | 9.2 |
| 64 | 16,384 | 27.3 s | 599 | 9.4 |

Single-stream 19 tok/s is the eager + all-Triton floor (production: 105 dual-card Vulkan); the aggregate curve is
still linear at 64 (`max_num_seqs`), so the concurrency use case is functional today and the ceiling is unmeasured.
Regime: one B70, eager, Triton everything, fp16, first-shape JIT included in the 1-stream number.

**Uncertainty list (not sampled):** XPU graphs (`VLLM_XPU_ENABLE_XPU_GRAPH=1`) / `torch.compile` on Windows (the single-stream lever); `max_num_seqs` > 64; the SAT-L1 jobs/h shape; two instances (one per card); the 27B dense int4 (`gptq` linears only, no MoE); why the TLA kernels fault (IGC on Windows vs Linux compute-runtime); MoE grouped-GEMM and paged-decode kernels in
isolation (only FA2 varlen was isolated); the 30B on `TRITON_ATTN` with int4 + MoE kernels; a driver newer
than 32.0.101.8974; the 27B dense int4; FA2 vs Triton attention on Xe2; `torch.compile` /
XPU graphs on Windows (`--enforce-eager` skipped inductor, which needs `cl` — present); the production
model family and any int4 format (AWQ/GPTQ through `_xpu_C`; AutoRound needs `auto_round_lib`); a full
card with production down; two instances at once; pipeline parallel over gloo; memory behaviour under
WDDM at high utilization (`gpu-mem-gate.ps1` before every real benchmark); `xpumem_allocator`
(`XPUMEM_ALLOCATOR_ENABLED`, off in the basic build — sleep mode / KV offload need it).

**Re-run the slice:** `E:\work\vllm-xpu-win\serve.cmd l3_serveN Qwen/Qwen3-0.6B 8097 --enforce-eager
--max-model-len 4096 --gpu-memory-utilization 0.4 --dtype float16 --attention-backend TRITON_ATTN`, then
`.venv\Scripts\python probes\l3_chat.py 8097` and `probes\l3_conc.py 8097 <streams> 256`. Stop with
`powershell -File kill-seat.ps1 -Port 8097`.
