# The Linux delta — why the CUTLASS-SYCL kernels run on a B70 under Linux and fault under Windows

R&D lap opened 2026-09-20 on Derek's question: Battlemage's driver work lands on Linux first; what does the leading-edge
Linux stack for a B70 decide differently, and which of those decisions can be ported to the Windows build — for vLLM,
llama.cpp SYCL, and everything under them. Every kernel that faults here (`_vllm_fa2_C` flash attention, the MoE
grouped GEMM, `_xpu_C.gdn_attention`) is a `intel/sycl-tla` (CUTLASS-SYCL) kernel; every non-CUTLASS kernel (oneDNN
int4/fp8 GEMMs, Triton, the basic SYCL ops, XPU graphs) runs.

## What the validated Linux stack is (from the sources, not the docs)

| layer | Linux, validated for these kernels | Windows, this box |
| --- | --- | --- |
| IGC (graphics compiler) | **2.11.7 → 2.34.4 (kernels CI ladder), 2.38.2 (vLLM's Dockerfile.xpu)**; 2.36+ on LLVM 17, 2.41 on LLVM 22 | `igc-default64.dll` **clang 14.0.5**-based; `igc-fallback64.dll` clang 16.0.6-based; both stamped 32.0.101.8974; no 2.x number exposed |
| compute-runtime (NEO) | 25.18 → 26.27 | driver 32.0.101.8974 (Aug 2026); the toolkit's `ocloc` reports `32.0.101.8857 (26.24)` → the Windows runtime line is ≈ CR 26.2x, i.e. current |
| Level Zero loader | 1.21.9 → 1.32.0 | 1.32.0 (System32 `ze_loader.dll`), SDK 1.33.1 vendored |
| oneAPI base | 2026.0 (`intel/deep-learning-essentials`) | 2026.1 toolkit; venv runtime pips 2026.1 |
| OpenCL device extensions the kernels link against (`SPV_INTEL_2d_block_io`, `subgroup_matrix_multiply_accumulate`, `split_barrier`) | present | **present** — `cl_intel_subgroup_2d_block_io`, `cl_intel_subgroup_matrix_multiply_accumulate` (+`_tf32`), `cl_intel_split_work_group_barrier`, `cl_intel_subgroup_buffer_prefetch`, `cl_intel_subgroup_extended_block_read`, `cl_intel_bfloat16_conversions` all advertised by the B70 (`probes\l8_cl_ext.py`); `_2d_block_io_v2` absent |
| SYCL aspects | — | `ext_intel_matrix`, `ext_intel_esimd`, `ext_oneapi_graph` present (`sycl-ls --verbose`) |

Sources: `E:\work\vllm-xpu-win\kernels\build_script\gpu_runtime_packages.json`, `kernels\Dockerfile.xpu`,
`vllm-src\docker\Dockerfile.xpu`, the driver store `iigd_dch.inf_amd64_85a415bebe1aa6a6`, `probes\l8_cl_ext.py`.

## What this lap established (2026-09-20, 17:15–18:00Z)

1. **Not a capability gap.** The Windows driver advertises every extension the TLA kernels need. The fault is
   behavioural: codegen in the Windows IGC line, or execution in the Windows runtime/KMD.
2. **The AOT build did not exonerate IGC.** Lap 3's `spir64_gen` images were produced by the toolkit's `ocloc`, which
   is the *same Windows driver-lineage compiler* (`32.0.101.8857`, clang-14 default). AOT and JIT faulting identically
   says nothing about Linux IGC 2.3x codegen — that has never been run against this hardware from Windows.
3. **IGC/NEO debug knobs are not honoured as environment variables on Windows** (`IGC_ShaderDumpEnable=1` leaves no
   dump; `NEO_PrintDebugSettings=1` prints nothing), with the driver's persistent kernel cache cleared
   (`%LOCALAPPDATA%\NEO`, 228 MB / 1,069 entries, removed). On Windows they are registry values under
   `HKLM\SOFTWARE\Intel\IGFX\IGC` (and `...\IGFX` for NEO) — admin.
4. The compiler pair: NEO loads `igc64.dll` (stub) → `igc-default64.dll`, with `igc-fallback64.dll` (newer LLVM)
   beside it; no public NEO debug variable selects the fallback (`debug_variables_base.inl` has
   `RebuildPrecompiledKernels`, `ForceCompilerUsePlatform`, `GpuFaultCheckThreshold`, `WddmResidencyLogger` —
   useful, none selects the DLL).
5. Newer Windows drivers exist: **32.0.101.8992 (Sept 2026), 32.0.101.9030**. Their IGC line is unknown until
   installed; the Sept release notes are game-fix notes.

## The experiment ladder (each needs a cue; ordered by cost-to-verdict)

E1 is spent (negative). The discriminating experiment is now **E3**: Linux IGC's ISA under the Windows runtime. Mechanism
that keeps the runtime constant: dump the kernel SPIR-V from the Windows build (`SYCL_DUMP_IMAGES=1`), `ocloc compile
-spirv_input -device bmg-g31` on AM4 with the validated profile (IGC 2.34.4 + CR 26.18 `.deb`s; `icpx` 2026.0 already
there), bring the native zebin back and load it under Windows Level Zero (`zeModuleCreate` NATIVE in a small harness,
or planted into DPC++'s persistent cache). AM4: 24 cores, 30 GB RAM, 22 GB free.


| # | experiment | needs | verdict in | what it decides |
| --- | --- | --- | --- | --- |
| E1 | **driver 32.0.101.9030** → `l4_fa2.py`, `l4_moe.py` | **DONE 2026-09-20 18:40Z — NEGATIVE.** Installed (Intel installer, UAC; the "Intel Graphics Software" panel component failed, irrelevant; no reboot); `vkdevices.py` map unchanged (B70s at Vulkan 1,2); production restored, health 200. FA2 still `DEVICE_LOST`, MoE still `OUT_OF_RESOURCES`; B70 still advertises `2d_block_io` | 3 min | the newest Windows IGC line does not compile these kernels correctly either — the delta is deeper than "lagging by a release" |
| E2 | registry `HKLM\SOFTWARE\Intel\IGFX\IGC`: `ShaderDumpEnable=1`, `DisableIGCOptimizations=1`, `Decompose2DBlockFuncsMode`, `TotalGRFNum=256`; NEO `GpuFaultCheckThreshold`, `WddmResidencyLogger` | **PARTIAL 2026-09-21.** `ShaderDumpEnable=1` + `DumpToCustomDir` + `ShaderDumpPidDisable=1` (set/reverted via RunAs) → **no dump** on 9030's IGC even though it JIT'd FA2 (fresh 448 KB NEO cache entry after a clear). The installed package is JIT-only SPIR-V (no `spir64_gen`/`.ze_info` in the `*_xe_2.dll`s). `SYCL_DUMP_IMAGES=1` on `l4_fa2.py` instead dumped **543 SPIR-V images (33 MB)** — the 19:54:44 cohort of **156 is `attn_kernels_xe_2.dll`** — filed at `E:\work\vllm-xpu-win\probes\e2_igc_dump\` with a README. The optimisation-off flags are still untested (minutes each, RunAs) | minutes per flag | codegen-vs-execution: a dump of the faulting kernel's ISA + whether any optimisation-off setting makes it run (then bisect) |
| E3 | Linux-lineage codegen on this hardware: AOT the TLA libs on **AM4** (Ubuntu, IGC 2.3x + ocloc from the Linux compute-runtime) for `bmg-g31`, ship the native image to OMEN, run under the Windows runtime | **INPUT IN HAND (E2's 156 FA2 SPIR-V images).** Remaining: the 8 `.deb`s on AM4 (Derek's cue), `ocloc compile -spirv_input -device bmg-g31` over the cohort, a `zeModuleCreate` NATIVE harness on OMEN (`UR_LOG_LEVEL_ZERO=level:info` printed nothing through `run.cmd` on 09-21 — verify it takes before relying on it). Recipe in the E2 README | ~1 h | isolates the compiler: Linux IGC's ISA under the Windows runtime |
| E4 | the reverse: run the Windows-JIT'd kernels' ISA through the Linux IGC's disassembler (`iga64`) from E2's dump and diff against a Linux dump | E2 + AM4 | ~1 h | the exact instruction/message that differs |
| E5 | port decisions, not just versions: the Linux vLLM-XPU container's runtime env (`vllm-src\docker\Dockerfile.xpu` sets none beyond paths) and the Triton-XPU Windows CI settings (Intel runs Triton CI on Windows and tracks `igc64.dll` — intel-xpu-backend-for-triton #5440) | desk | — | whether Intel's own Windows validation of the *Triton* path has flags we lack |

llama.cpp SYCL is the control: its kernels (oneDNN + hand-written SYCL, no CUTLASS) run on this driver, which is why the
sibling program never met this wall; the only Linux-first SYCL feature it leans on is oneDNN/XMX flash attention, and
that works here. The delta is specifically CUTLASS-SYCL's 2D-block-IO/DPAS code paths under the Windows IGC.
