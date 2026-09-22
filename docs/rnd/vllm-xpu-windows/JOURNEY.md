# The vLLM Windows Port on Arc Pro B70

On the evening of September 19, 2026, Derek brought a native Windows port of vLLM to the project, hoping to reopen a capability question we had previously parked. The port turned out to be completely CUDA-only, demanding an NVIDIA Blackwell GPU that we did not have. But it carried the necessary Windows build shims and POSIX workarounds. By combining those shims with upstream vLLM's pure-Python XPU platform and a custom-built, out-of-tree SYCL kernel package, we assembled a native XPU stack on Windows anyway. We pushed the production MoE model to 82.4 tokens per second on a single card, proving the capability, before hitting a hard wall: the compiled CUTLASS-SYCL kernels fundamentally fault the device under the Windows graphics compiler.

## Cast

OMEN is the workstation, and the two Intel Arc Pro B70s are its compute engines. 

Production is the llama.cpp Vulkan seat that normally runs the hardware, handling the live inference requests behind the door; it is the baseline we measure against, and the thing that must be restored after every test. 

The tenancy window is the strict operational recipe we use to borrow the cards from production without causing an outage. 

AM4 is the Linux box in the next room, holding the validated compiler toolchain we need for testing. 

The wall is the CUTLASS-SYCL kernels—specifically flash attention, the MoE grouped GEMM, and the gated-delta kernel—which crash the Windows drivers on contact. 

HEARTH is the door, the gateway that manages routing and lifecycle for the seats. 

Derek intuits the direction, finds the software, and sets the pace for the project. Claude holds the state, writes the code, executes the builds, and runs the forensics.

## Act I — the port that wasn't

The SYCL-vs-Vulkan program had parked the vLLM question earlier that day under a strict condition: we would only reopen it if a native Windows build path existed. We had no Windows wheel for XPU, the WSL2 bridge was kernel-dead, and XCCL did not exist on our platform. Then Derek found `SystemPanic/vllm-windows`. 

A desk check immediately revealed what the port actually supported: it was explicitly built for CUDA 13 and Blackwell GPUs on Windows. It utilized NCCL for multi-GPU, contained no oneAPI or SYCL code, and its tracker's single Intel-GPU question had sat unanswered since January 2026. Its pre-compiled Python wheel could not even see a B70.

But it was exactly what we needed. The repository provided the Windows runtime work: a `setup.py` that no longer refused non-Linux operating systems, the MSVC build configurations, `winloop` in place of `uvloop`, spawn-only workers, and the necessary POSIX shims. Upstream vLLM's XPU platform was already pure Python, relying on `torch-xpu`, `triton-xpu`, and a single out-of-tree SYCL kernel package named `vllm-project/vllm-xpu-kernels`. Until now, nobody had combined the two.

OMEN already had the rest of the puzzle. We had the oneAPI 2026.1 toolkit with its compilers and math libraries, alongside torch-xpu Windows wheels that successfully enumerated both B70 cards. 

We established a hard boundary immediately. The `torch.distributed.is_xccl_available()` function returned False on the Windows torch-xpu wheel. Intel's oneCCL does not ship for Windows, and the toolkit has no `ccl` component. Without XCCL, dual-B70 tensor parallelism was entirely off the table natively. While `gloo` was present, the only honest dual-card shape we could pursue was one independent vLLM instance per card behind the door.

## Act II — the stack that was

Building the kernel package was a fight against the Windows C++ ecosystem. Over nine build attempts, the compiler threw loud refusals, each fixed one at a time. The CMake configuration fought the GNU-like driver; backslash paths broke Python scripts; and GNU include flags were silently interpreted as secondary source files by MSVC. The oneAPI toolkit shipped with no `ze_loader.lib`, forcing us to vendor the Level Zero Windows SDK 1.33.1 just to link the binaries. When we enabled sixteen concurrent jobs for the device compile of the chunk-prefill attention templates, the compiler instantly ran out of memory, forcing us to restrict the build to six jobs.

Eventually, all five kernel extensions built, installed, and imported. The production model, Qwen3-30B-A3B-GPTQ-Int4, loaded successfully. We secured a tenancy window, stopping the production llama.cpp instance, freeing up 31,906 MiB on both cards, and launched the seat on card 1. 

The model loaded 15.68 GiB in 111 seconds. The KV cache allocated successfully. And then, on the first real forward pass, the Level Zero backend failed with a `UR_RESULT_ERROR_OUT_OF_RESOURCES` error. 

We isolated the faults. The oneDNN `int4_gemm_w4a16` kernel ran perfectly at every size we tested. But the `flash_attn_varlen_func` standalone immediately threw a `UR_RESULT_ERROR_DEVICE_LOST` error. The SYCL-TLA attention kernel was faulting the B70. The fused MoE grouped GEMM did exactly the same thing. 

We routed entirely around the faults, constructing a TLA-free stack. We set the quantization to `moe_wna16`, the MoE backend to Triton, and the attention backend to `TRITON_ATTN`. This combination relied on the proven oneDNN int4 linears, vLLM's Triton WNA16 MoE experts, and Triton unified attention. 

It worked. The production model ran on a single B70 under Windows. 

The initial numbers on this eager, Triton-only setup were functional but slow. On one B70, utilizing Qwen3-30B-A3B-GPTQ-Int4 with fp16 activations, a single stream achieved 18.9 tokens per second. We profiled the decode step and found the GPU was idle 85 percent of the time. The engine was spending roughly 6 milliseconds on the device and 40 milliseconds on the CPU per step, bogged down by roughly 380 kernel launches. The process was entirely launch-bound.

We engaged XPU graphs to capture the execution, and they worked natively on Windows. However, capturing all 86 batch sizes exhausted the Level Zero resources, literally returning another out-of-resources error. We adjusted the compilation config to capture only sizes 1, 2, 4, and 8, letting larger batches fall through to the eager path. A captured MoE at a batch size of 32 or higher is actually slower than the eager path due to the static worst-case grid. 

To improve the compute side, we swept the kernel tables directly on the card. The default configurations were built for other architectures, and the only donor table we had from a Strix Halo was worse than the defaults. After a 35-minute sweep of 504 configurations, we found that a block size of 16 won at every dimension on the Xe2 architecture, netting a 2.64x to 2.80x speedup at larger batch sizes.

With the TLA-free stack, XPU graphs for sizes 1 through 8, and the swept B70 MoE table, we measured the final performance on one B70 running Qwen3-30B-A3B-GPTQ-Int4 with fp16 activations:

| Streams | Eager | Graphs ≤8 | + B70 MoE table |
| --- | --- | --- | --- |
| 1 | 18.9 | 71.5 | 82.4 |
| 8 | 87 | 286 | 330 |
| 32 | 294 | 287 | 681 |
| 64 | 599 | 602 | 1,196 |

The single stream delivered 82.4 tokens per second, fully T=0 deterministic. We found the needle at 11,840 tokens, achieving roughly 2.4k prompt tokens per second at 12k depth. For comparison, our production dual-card Vulkan llama.cpp seat runs at 105 single-stream at an `-np 8` split. On a single card, vLLM had reached 78 percent of the dual-card production speed, and delivered an aggregate throughput of 1,196 tokens per second at 64 streams that no llama.cpp seat possesses.

## Act III — the 27B at depth

With the 30B concurrency proven, we turned to the fleet's depth worker: the SergiioB Qwen3.8-27B-GPTQ-Int4-sym-G128-MTP-BF16 checkpoint. 

This model is not a classic dense transformer. Out of its 64 layers, 48 are gated-delta-net linear attention layers, and only 16 are full-attention. Because the KV cache only stores data for the 16 full layers, it requires just 64 KiB per token. A 64k context uses 4.0 GiB of memory, and a 128k context uses 8.0 GiB. By setting the GPU memory utilization to 0.93, a full 128k context fits on a single 32 GB card. 

The stack immediately hit another wall. On XPU, the gated-delta-net layer hard-routes to the SYCL-TLA gated-delta kernel, which faults on this Windows driver exactly like FlashAttention 2 did. The GPU died silently, and the subsequent Triton launch for the gated RMSNorm took an access violation crash. We patched the engine to route the GDN layers to `forward_cuda`, forcing it onto the FLA Triton path. 

Running the model on one B70, utilizing the 64k context config with the GDN routed to FLA Triton, we established the baseline. Prefill was heavily attention-bound, spending 65 percent of the device time in the Triton attention prefill kernel. The kernel was processing just 2 query tokens per program for this GQA-6 model. 

We overrode the attention prefill tiling, sweeping the configuration until we found a block size of 64, a tile size of 32, 8 warps, and 1 stage. This tuning yielded a 3.06x speedup on the kernel itself. 

With the tuned tiling on one B70 running the 27B model:
At 13.6k context, prefill accelerated from 183 to 463 tokens per second.
At 63.5k context, prefill measured 161 tokens per second, and decode ran at 6.0 tokens per second. The needle was found successfully.

The 128k point requires roughly 20 to 25 minutes of prefill time at the O(n²) trend, and has not yet been run.

## Act IV — the wall, and what it is not

Every CUTLASS-SYCL kernel in the `intel/sycl-tla` package faulted on Windows. Flash attention, the MoE grouped GEMM, and the gated-delta kernel all threw device loss or resource errors, whether compiled just-in-time or ahead-of-time, on both the 2026.0 and 2026.1 SYCL runtimes, under Windows drivers 8974 and 9030.

We proved this is not a capability gap. A short ctypes probe confirmed the B70 driver correctly advertises every OpenCL and SYCL extension the kernels link against, including 2D block IO, matrix multiply accumulate, and split work group barriers. 

The delta is strictly in the compiler line. The CUTLASS-SYCL kernels are validated on Linux using the Intel Graphics Compiler versions 2.11 through 2.38, running on LLVM 17 or newer. Windows, however, ships an unpublished compiler line embedded in the driver. The `igc-default64.dll` on Windows is based on clang 14, and the fallback is clang 16. Neither exposes a 2.x version number. 

We initially theorized that ahead-of-time compilation might bypass the issue, but this was refuted. The toolkit's `ocloc` compiler is built from the exact same Windows driver lineage, meaning our AOT tests were still running Windows codegen. No Linux-compiler codegen has ever been executed on this hardware from Windows.

To isolate the compiler, we designed an experiment ladder. Experiment 1 was to install the newest Windows driver, 9030, and re-test. If it failed, Experiment 3 would be the discriminator: dump the FA2 kernel SPIR-V on Windows, move it to the AM4 Linux box, compile it ahead-of-time for the Battlemage architecture using the validated Linux IGC 2.34.4 profile, ship the native image back to OMEN, and load it under the Windows runtime. 

## Act V — drivers day

On September 21, Derek finalized the installation of the Windows Arc driver 32.0.101.9030 with a system reboot. 

The reboot triggered an immediate scare. Derek logged in to find all his saved profiles and passwords missing. The DPAPI logs showed 839 "Master key access failed" events during the first boot. We initially theorized the TPM had been cleared, but a cross-boot comparison proved the TPM and SecureBoot events were standard regime behavior, firing identically on every boot of the board. The system self-healed on a second user restart, successfully re-keying the master in place and restoring access to all cryptographic stores without any data loss. 

With the system stable, we verified the state of Experiment 1. The E1 test had failed the night before, but the system had not yet been rebooted. We checked `setupapi.dev.log` and confirmed that the 9030 kernel mode driver had received a "Restart verified" status and gone live at 18:49 the previous evening, well before our probes compiled and ran. The negative verdict on the 9030 driver stood; the newest Windows IGC line does not compile these kernels correctly either.

Derek installed the NPU driver 32.0.100.5540. We ran a strict reopen test for the NPU engine history, feeding the 71-input expert graph to the new driver. Both the old 4778 driver and the new 5540 driver lowered the graph to the identical 235 operations and returned the exact same 2030.80 µs estimate. 

He then installed the Platform Performance Package 26.08.100.3. The installer blocked for 17 minutes on a modal warning about OEM policies, which we could read programmatically but could not click due to UIPI restrictions until Derek intervened. The package installed successfully, upgrading the Intel Dynamic Tuning Technology components.

We spent the remaining R&D slot on Experiment 2: attempting to disable compiler optimizations to see if the kernels would run. The registry knobs for the Intel Graphics Compiler were entirely ignored by the 9030 driver. However, by running the seat with `SYCL_DUMP_IMAGES=1`, the runtime successfully dumped 543 SPIR-V images into the working directory. By checking the timestamps, we isolated a cohort of 156 images, each roughly 460 KB, representing the exact FA2 instantiations. 

We now have the raw SPIR-V input required for Experiment 3 on the Linux box.

## What we learned

A "native port" is a claim about a backend, not an ecosystem. You have to read the repository's documentation before attempting to install the wheel. The Windows port was entirely CUDA-based, and the only useful pieces were its build shims.

On this specific stack, an access violation inside a Triton launch means the previous kernel faulted the GPU. When the gated-delta kernel silently killed the device, the downstream RMSNorm took the crash. You must isolate the operation immediately preceding the crash to find the actual fault.

The decode step on Windows Level-Zero eager execution is entirely launch-bound. XPU graphs are the lever to fix this, but they only work for the batch sizes you explicitly capture. Because a captured MoE loses to eager execution at batch sizes of 32 or higher, you must selectively capture small sizes and let the rest fall through.

Kernel tables tuned for other silicon architectures are consistently worse than the defaults on Xe2. You must sweep the configurations directly on the card. The donor table from Strix Halo failed, but finding the `BLOCK_SIZE_M=16` configuration locally yielded massive gains.

Ask the driver what it advertises before theorizing about what it lacks. A twenty-line ctypes script definitively showed every needed OpenCL extension was present. The gap is in the codegen, not the capability.

You must know exactly which compiler produced the binary before calling an experiment decisive. Because the toolkit's `ocloc` compiler shares the Windows driver lineage, our initial AOT tests proved absolutely nothing about Linux IGC codegen. 

Elevated setup steps require explicit run-as verbs, and the UAC prompt will land on the desktop of the user, not the terminal of the operator. Do not hand a user a command to type when the automation can trigger the prompt natively.

A tenancy window is a strict operational recipe, not a judgment call. Write the sentinel, restart the service to stop it, run the lap, delete the sentinel, restart the service to restore it, and verify a 200 health check before closing the lap. We executed five windows with zero incidents using this method.

Loader bugs often present as unquantized layers. If a quantization config rebuilds itself per layer, it never sees the loader's hooks, and the symptom surfaces as a missing data attribute three frames away.

Compiling the engine without XPU graphs costs you determinism, and enabling graphs restores it. You must pick the execution mode per use case and rigorously re-check T=0 identity whenever that mode changes.

Background loops that wait on a log file must also monitor the process itself. If an engine core crashes, it will never write the expected line, and the grep loop will hang indefinitely.

A gate you promised but did not run is merely a hypothesis. We outlined a script to check for WDDM memory spill before every benchmark, but never actually executed it. The numbers are accurate, but they remain unchecked for spill.

## Where it rests

The machine currently runs the Windows Arc driver 32.0.101.9030. The production llama.cpp instance is fully restored and serving traffic.

The 30B seat is proven. On one B70 using native Windows, the TLA-free stack, and XPU graphs for sizes 1 through 8, it achieves 82.4 tokens per second single-stream and 1,196 tokens per second at 64 streams, with deterministic accuracy.

The 27B seat is proven at 64k. On one B70 using the GDN-to-FLA routing and tuned attention tiling, it achieves 161 tokens per second prefill and 6.0 tokens per second decode, finding the needle in the haystack. The 128k point is modeled to fit on a single card but remains untested.

The wall is proven. Experiment 1 demonstrated that updating the Windows driver does not fix the CUTLASS-SYCL kernel faults. 

Experiment 3 is staged and untested. We have the dumped SPIR-V images, but we have not yet run the cross-compilation on the Linux machine.

## Next episode

The immediate next step is Experiment 3 on AM4, which requires installing the eight runtime packages to establish the validated Linux profile, compiling the dumped SPIR-V, and loading it natively on OMEN. This will take roughly an hour.

For the 27B model, we need to run XPU graphs with the V2 runner now that the GDN layers are safely routed to FLA, which should take 20 minutes. We also need to test MTP speculative decoding using the checkpoint's own head, which requires 30 minutes, and finally execute the 128k context point, requiring roughly 25 minutes.

For the 30B model, we need to measure the saturation jobs-per-hour shape at 8x16k and test running two independent seats simultaneously, which requires a 30-minute tenancy window.

Finally, we need to establish the door stanza for the vLLM seat, pinning it to the gateway and running a local generate proof, alongside submitting the two non-Windows-specific bug fixes back to the upstream vLLM repository. 

The thesis of the whole estate is that the B70 does more than Windows was letting it, and this program exists to prove it.