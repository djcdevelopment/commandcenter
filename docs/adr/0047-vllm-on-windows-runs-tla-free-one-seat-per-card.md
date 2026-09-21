# 0047 — vLLM serves the B70s natively on Windows on a TLA-free kernel set, one seat per card, graphs for small decode batches; the CUTLASS-SYCL kernels are a compiler-line problem, not a capability one

**Status:** Accepted (2026-09-20) — the shape that survived nine R&D laps (session cc-e0fdf649,
`docs/rnd/vllm-xpu-windows/`). Production (`omen-arc`, llama.cpp Vulkan) is **unchanged**; this records how
the vLLM seat is built and borrowed, so the next lap does not re-derive it. Execution state per decision
below.

**Companion to:** `docs/adr#0034` (omen-arc is the door default; sunk cost), `docs/adr#0040`/`#0045`
(llama-swap owns the serving lifecycle), `docs/adr#0043` (the rung goes cold when idle), `docs/adr#0044` (a
rate is not a scalar — every figure here names its regime), `docs/rnd/sycl-vs-vulkan/WORKFLOW.md` (the program
that parked vLLM; reopened under an amended condition).

## Context

The SYCL-vs-Vulkan program parked vLLM on 2026-09-19 with a written reopen condition (OMEN boots Linux, or a
B70 returns to AM4): no Windows wheel, the WSL2 dual-Arc bridge kernel-dead, no XCCL path. The same evening
Derek found `SystemPanic/vllm-windows`, a native Windows port. It is CUDA-only. But its Windows build/runtime
shims, combined with upstream's pure-Python XPU platform and `vllm-project/vllm-xpu-kernels` compiled here
with oneAPI 2026.1 `icx`, serve models from a B70 under Windows 11 — the amended reopen condition ("a native
Windows build path exists") is met.

What held, measured on one Arc Pro B70 (all figures: eager unless stated, fp16 activations, the model named):

- **Qwen3-30B-A3B-GPTQ-Int4** (production's family): correct, T=0 deterministic; **82.4 tok/s single-stream,
  330 @8 streams, 681 @32, 1,196 @64**; ~2.4k prompt tok/s at 12k depth. Production's dual-card Vulkan seat
  does 105 single-stream at `-np 8`. The vLLM seat's single stream is 78 % of it on one card; its aggregate
  has no llama.cpp counterpart.
- **Qwen3.8-27B** (hybrid: 48 gated-delta-net + 16 full-attention layers, KV 64 KiB/token): runs at
  **128k context on one card**; 463 tok/s prefill at 13.6k, 161 tok/s prefill / 6.0 tok/s decode at 64k,
  needle found. The dual-card llama.cpp SYCL seat does 736 / ~9 at that depth.

What did not hold, and why it matters for the shape: every `intel/sycl-tla` (CUTLASS-SYCL) kernel — flash
attention, the MoE grouped GEMM, the gated-delta kernel — faults the device (`UR_RESULT_ERROR_DEVICE_LOST`
standalone; `OUT_OF_RESOURCES` surfacing at whichever op syncs next) under Windows Arc drivers 32.0.101.8974
**and 9030**, JIT or AOT, on both the 2026.0 and 2026.1 SYCL runtimes. The B70 advertises every extension
those kernels link against (`cl_intel_subgroup_2d_block_io`, `cl_intel_subgroup_matrix_multiply_accumulate`,
split barrier). Linux validates the same kernels on IGC 2.11–2.38 (LLVM 17+); the Windows driver ships a
clang-14 `igc-default64` and a clang-16 `igc-fallback64`, and the toolkit's `ocloc` is Windows driver-lineage
too — so no Linux-compiler codegen has yet run on this hardware. Windows torch-xpu wheels ship no XCCL
(oneCCL is not built for Windows), so dual-card tensor parallel does not exist natively.

## Decisions

1. **The vLLM seat on Windows is TLA-free.** `--quantization moe_wna16 --moe-backend triton
   --attention-backend TRITON_ATTN`: oneDNN int4 linears (`XPUwNa16LinearKernel`), vLLM's Triton WNA16 MoE
   experts, Triton unified attention; gated-delta layers on the FLA Triton path (`forward_cuda`), never
   `_xpu_C.gdn_attention`. The CUTLASS-SYCL extensions build and import (kept for the day they run) but are
   not on any served path. — **LANDED** (vllm-src branch `windows-xpu`, kernels branch `windows`).
2. **One seat per card.** No XCCL, no TP. Two seats behind a two-upstream door stanza is the dual-card
   shape; pipeline-parallel over gloo is a stretch, not a plan. — **DECIDED, NOT YET EXECUTED** (one seat
   proven; the second seat and the stanza are open).
3. **XPU graphs for decode batches 1–8, eager above; never capture large sizes.** `--compilation-config
   {"cudagraph_mode":"FULL_DECODE_ONLY","cudagraph_capture_sizes":[1,2,4,8]}` with
   `VLLM_XPU_ENABLE_XPU_GRAPH=1`. Capturing all 86 sizes exhausts Level Zero; a captured MoE at batch ≥ 32 is
   slower than eager (static worst-case grid); compile-without-graphs loses T=0 determinism and graphs
   restore it. — **LANDED** (`E:\work\vllm-xpu-win\serve-30b.cmd`).
4. **Kernel tables are swept on the card, never borrowed.** The B70 `int4_w4a16` MoE table
   (`BLOCK_SIZE_M=16` at every M; 2.6–2.8× at M ≥ 32) and the attention prefill tiling
   (`VLLM_XPU_ATTN_PREFILL=64,32,8,1`, 3.06×) came from sweeps; the only donor table (Strix Halo) was worse
   than the defaults. New shapes get a sweep (`probes\l6_moe_tune.py`, `probes\l7_attn_sweep.py`). — **LANDED**.
5. **A lab seat borrows the cards through the tenancy window, and the window is a recipe.** Write
   `hearth\var\arc-maintenance.stop`; `schtasks /Run /TN ArcServeRestart` (stop-only with the sentinel);
   confirm both cards at 31,906 MiB free; run the lap; delete the sentinel; `ArcServeRestart` again;
   `/health` 200 before the lap is closed. Five windows this session, no incident. Production is never stopped
   by killing a process. — **LANDED** (documented in every lap doc; memory).
6. **The CUTLASS-SYCL question is decided by the Linux-delta ladder, not by more Windows rebuilds.** E1
   (driver 9030) is spent and negative. The discriminating experiment is E3: the kernel's SPIR-V dumped on
   Windows, `ocloc compile -spirv_input -device bmg-g31` on AM4 with the validated Linux profile (IGC 2.34.4 +
   compute-runtime 26.18 + Level Zero 1.28.2), the native image loaded under the Windows runtime. If it runs,
   the fix is a compiler and "compile on Linux, run on Windows" becomes a build step; if it faults, the Windows
   runtime/KMD is the wall and the CUTLASS path is closed on this OS. — **OPEN** (needs the `.deb` install on
   AM4 — Derek's cue).
7. **Production stays on llama.cpp until the vLLM seat clears the saturation program's own bar.** ≥ 3,358
   jobs/h at the SAT-L1 8×16k shape, ≥ 105 tok/s single-stream, T=0 correct, a soak with zero TDR/WHEA. The
   aggregate numbers above are decode-only samples, not that shape. — **OPEN**.

## Consequences

- The fleet gains a second engine family on the B70s that runs natively on the OS the box actually boots —
  no WSL2, no dual-boot. ADR-0027 gate-2 (model/builder variation) has a new engine to vary.
- Every number in this record names its regime (one card, eager/graphs, fp16, context, streams); a dual-card
  llama.cpp figure and a one-card vLLM figure are never compared without saying so.
- The lab tree (`E:\work\vllm-xpu-win\`) is the truth for launch recipes; the docs in
  `docs/rnd/vllm-xpu-windows/` are the truth for what was measured and what was not.
- Two upstream contributions are owed and not Windows-specific: the `MoeWNA16Config` loader-hook fix and the
  B70 MoE config JSON (`vllm-project/vllm`); the Windows build patch set belongs in `vllm-xpu-kernels`.
- OMEN's Arc driver is now 32.0.101.9030 (installed for E1; `vkdevices.py` map unchanged; old driver in the
  store). Any figure from the SYCL/Vulkan programs dated before 2026-09-20 was taken on 8974.
