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
| L2-full | + `_moe_C`, `_xpu_C`, `_vllm_fa2_C` (SYCL-TLA attention/grouped-GEMM + static oneDNN) | extensions import; FA2 varlen + fused MoE run | **pending — 30–60 min build, needs Derek's cue** |
| L3 | `vllm serve` a dense model on one B70, Triton attention, eager | correct completion over `/v1/chat/completions` | **pass** — Qwen3-0.6B, T=0 deterministic |
| L4 | production model family (Qwen3-30B-A3B int4 AWQ/GPTQ) on one card; 27B int4 | lap-8 bodies correct at 16k/30k; T=0 determinism | blocked on L2-full (MoE + int4 paths need `_moe_C`/`_xpu_C`) |
| L5 | jobs/h shape (SAT-L1, 8×16k): one instance, then two (one per card) | vs production 3,358 jobs/h / 105 tok/s | after L4 |
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

**Uncertainty list (not sampled):** the full kernel build on Windows (SYCL-TLA attention/grouped-GEMM,
static oneDNN — each may have its own MSVC walls); FA2 vs Triton attention on Xe2; `torch.compile` /
XPU graphs on Windows (`--enforce-eager` skipped inductor, which needs `cl` — present); the production
model family and any int4 format (AWQ/GPTQ through `_xpu_C`; AutoRound needs `auto_round_lib`); a full
card with production down; two instances at once; pipeline parallel over gloo; memory behaviour under
WDDM at high utilization (`gpu-mem-gate.ps1` before every real benchmark); `xpumem_allocator`
(`XPUMEM_ALLOCATOR_ENABLED`, off in the basic build — sleep mode / KV offload need it).

**Re-run the slice:** `E:\work\vllm-xpu-win\serve.cmd l3_serveN Qwen/Qwen3-0.6B 8097 --enforce-eager
--max-model-len 4096 --gpu-memory-utilization 0.4 --dtype float16 --attention-backend TRITON_ATTN`, then
`.venv\Scripts\python probes\l3_chat.py 8097` and `probes\l3_conc.py 8097 <streams> 256`. Stop with
`powershell -File kill-seat.ps1 -Port 8097`.
