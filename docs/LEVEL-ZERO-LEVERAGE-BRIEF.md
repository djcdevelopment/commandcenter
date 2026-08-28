# Level-Zero / oneAPI leverage brief — OMEN all-Intel topology

**Date:** 2026-08-28 · **Status:** research reconnaissance, no production changes ·
**Scope:** how Level-Zero and the wider oneAPI stack can (and cannot) improve routing,
loading, caching, and queuing of local models on OMEN — Qwen3.8-Flash-Next (MoE, 88 GiB
UD-IQ4_XS, arch `qwen4exp`) and Qwen3.8-27B dense (19 GB Q4_K_M) — **around** the Vulkan
flagship, never instead of it.

**Constitution honored:** Vulkan/llama.cpp stays the flagship and the public-thesis lane.
Evidence before belief — every claim below is tagged. Loud fallbacks. Most published
Level-Zero work is Linux; Linux-only evidence is flagged explicitly.

**Method:** four parallel web-research passes (cross-GPU, expert streaming, load/KV paths,
NPU/OVMS/SYCL) + live read-only probes run on OMEN during this session (ctypes against
`ze_loader.dll`, torch-xpu 2.12.1 from `E:\work\xpu-train\.venv`, GGUF header parse, knee-tree
source reads). Production served throughout; serviceability re-proven through the door after
the probes (`routed_by: pinned:omen-arc`, real 1-token completion, 2026-08-28).

**Evidence tags:** `[MEASURED-HERE]` = run on OMEN this session (Windows 11, GPU driver
32.0.101.8974, NPU driver 32.0.100.4778). `[RECEIPT]` = prior rotation-program /
OMEN-LIMIT-TEST receipts. `[Windows]` / `[Linux-only]` / `[OS-unclear]` = sourced community
evidence. `[DERIVED]` = arithmetic from measured inputs. `[UNSOURCED]` = prior knowledge,
treat as hypothesis.

---

## 0. What this box told us directly (new measurements, 2026-08-28)

All `[MEASURED-HERE]`, co-resident with live production, no `ZE_AFFINITY_MASK` set anywhere.

| Fact | Value |
|---|---|
| Level-Zero drivers under one loader | **3** — driver 0: both Arc Pro B70 (devid `0xe223`); driver 1: Arrow Lake iGPU (`0x7d67`); driver 2: NPU "Intel(R) AI Boost" (`0xad1d`) |
| B70 queue groups | group 0 COMPUTE+COPY+COOP (1 queue) + **group 1 COPY-only (1 queue)** — one dedicated blitter lane per card |
| iGPU queue groups | 3 (incl. dedicated COPY and a METRICS-capable compute group) |
| NPU queue groups | 1 group COMPUTE+COPY, **2 queues** |
| **P2P between the B70s** | `zeDeviceCanAccessPeer` = **0 both directions**; `zeDeviceGetP2PProperties` flags = **0x0** (no ACCESS, no ATOMICS); rc=0 — an honest no, not an error |
| torch-xpu peer query | `torch.xpu.can_device_access_peer(0,1)` = False, `(1,0)` = False (torch 2.12.1+xpu) |
| H2D bandwidth (256 MiB ×5) | pageable **12.1**, pinned **13.3 GB/s** |
| D2H bandwidth | pageable 12.3, pinned **14.1 GB/s** |
| D2D staged (no P2P) | **2.29 GB/s** (0→1) / **5.05 GB/s** (1→0) — asymmetry unexplained, single run, needs repeat before quoting |
| zeInit across all 3 drivers, production live | clean, no deadlock — the landmine is specifically the `ZE_AFFINITY_MASK` env var (bare torch import hangs at 0 CPU with it set, OMEN-LIMIT-TEST 7a `[RECEIPT]`), not L0 use |
| Flash-Next geometry (GGUF header) | `qwen4exp`: 48 blocks, embd 2560, **512 experts, top-10 + 1 shared**, expert FFN 640, GQA 24/2, ctx 262144 |
| Tooling absent today | no oneAPI toolkit, no OpenVINO/OVMS, no RAM-disk driver installed — probe costs below include installs |

Derived expert arithmetic `[DERIVED]`: per-expert-per-layer = 3·2560·640 ≈ 4.92 M params.
Active expert weights/token = (10+1)·4.92 M·48 ≈ **2.6 B params ≈ 1.3–1.4 GB/token** at
~4.25 bpw (IQ4_XS; UD mix runs somewhat higher). Full routed expert set ≈ 121 B params ≈
**64–80 GB**. Consecutive-token expert overlap at 10-of-512 is ~2 % — decode streaming gets
essentially no reuse; **prefill batches get massive reuse** (a 512-token ubatch touches
nearly all 512 experts once per layer).

Context from the phase board `[RECEIPT]`: commit 96.9/135.3 GB with production up; Flash
48-block epoch (60.4 GB commit) does not fit beside production; standby cache 62.3 GB.

---

## Q1 — Cross-GPU communication: does L0 open a lane Vulkan lacks?

**Verdict: IGNORE (API level). The lane is closed by silicon, not by Vulkan.**

Findings:

- **P2P is formally absent on this box** — `[MEASURED-HERE]` above, both the SYCL-level and
  raw-L0 queries.
- **Root cause is the consumer Intel root complex, below any OS or API.** The decisive
  community thread ran 2× Arc Pro **B70** on separate CPU root ports of a consumer Intel
  host: the kernel refuses cross-root-port P2P; forcing the whitelist open makes
  `zeCommandListAppendMemoryCopy` return SUCCESS **with 100 % corrupted data** (all-`0xFF`
  = PCIe Unsupported Request) — consumer Intel root complexes do not route peer TLPs
  across root ports. Same cards work at ~28.6 GB/s/dir on AMD AM5 and behind a shared PCIe
  switch with ACS redirect off. `[Linux-only]`
  https://github.com/intel/compute-runtime/issues/935
- Intel contributor position: Arc dGPU pairs *can* P2P "if on same PCIe root" — which OMEN's
  two direct-CPU-lane slots are not. `[Linux-only]`
  https://github.com/intel/compute-runtime/issues/827
- Even where `canAccessPeer=1`, Battlemage peer copies have open corruption/DEVICE_LOST
  reports (4× B70, one root complex); workaround is host staging. `[Linux-only]`
  https://github.com/intel/compute-runtime/issues/942
- **The closest public analog to OMEN's topology** (consumer Arrow-Lake-class "Core Ultra
  250K", 2× Arc Pro B60 at Gen5 x8 direct CPU lanes, Fedora): llama.cpp SYCL
  `--split-mode tensor` (merged 2026-06) gained **zero decode** on a dense 27B and
  **regressed −22 %** on a 35B-A3B MoE at N=1 — mirroring our Vulkan −30 %/−72 % W3 canary
  in kind. The +78.6 % decode win that headlines the PR came from a Threadripper host
  (kernel-whitelisted P2P). `[Linux-only]`
  https://github.com/ggml-org/llama.cpp/pull/24152
- **oneCCL: right GPUs, wrong OS.** The tree has first-class Arc B-series flags
  (`CCL_ENABLE_ARCB`) and PCIe-specific collectives — and builds on **Linux only**; the
  oneAPI Base Toolkit system requirements list no Windows for oneCCL. Same for the whole
  Intel multi-Arc serving stack (vLLM XPU / LLM-Scaler / XCCL). `[Windows-verified absence]`
  https://github.com/uxlfoundation/oneCCL ·
  https://www.intel.com/content/www/us/en/developer/articles/system-requirements/oneapi-base-toolkit/2025.html ·
  https://github.com/intel/llm-scaler
- **Host-staged is the only lane, and it's slow in every API**: community L0 host-staged
  fallback ≈ 3 GB/s on comparable dual-B70 `[Linux-only]` (issue 935); ours measured
  2.3–5.1 GB/s `[MEASURED-HERE]`. Vulkan's multi-GPU path has staged through RAM since
  inception (https://github.com/ggml-org/llama.cpp/pull/5321). Switching APIs does not
  change which lane you are in.
- Implicit scaling is a multi-stack (PVC-class) mechanism; B70 is single-stack. Spanning
  both cards with one L0/SYCL context buys nothing and on current NEO costs host-RAM
  mirroring of allocations. `[Linux-only]`
  https://github.com/intel/compute-runtime/issues/968 ·
  https://intel.github.io/llvm/MultiTileCardWithLevelZero.html
- Windows exposes no P2P tooling surface at all: XPU Manager's `xpu-smi topology --p2p`
  matrix is Linux-only. `[Windows-verified absence]` https://github.com/intel/xpumanager/releases

**Meaning for the W3 tensor-split verdict:** the −30 %/−72 % loss is physics shared by every
runtime on this topology, not a Vulkan defect. The two levers that would actually open the
fast lane are platform-level, both out of scope for the Windows thesis box: Linux + both
cards behind a PCIe switch (ACS off), or an AMD host. Keep the board's "watch upstream
Vulkan TP, re-canary on engine bumps" stance; stop investigating L0 collectives.

---

## Q2 — Expert streaming: host-resident experts, GPU-executed

**Verdict: PROBE NOW — the prefill half already exists in our pinned binary and the
measured "prefill cliff" was taken below its trigger threshold. Decode via pure streaming is
parity-at-best with the CPU path, but wins co-residency immunity. FlashMoE and OpenVINO
oversized-model paths: IGNORE.**

Findings:

- **llama.cpp already GPU-executes host-resident weights for batches ≥ 32 tokens** ("offload
  large batches to GPU", merged 2024-03: weights in CPU buffers are streamed to the GPU per
  micro-batch and the matmul runs there): https://github.com/ggml-org/llama.cpp/pull/6083.
  **Verified in the knee tree** `[MEASURED-HERE]`: `ggml_backend_vk_device_offload_op` with
  `GGML_OP_OFFLOAD_MIN_BATCH` (default **32**) at
  `ggml\src\ggml-vulkan\ggml-vulkan.cpp:18656-18829`, `GGML_OP_MUL_MAT_ID` (the MoE expert
  matmul) explicitly handled via `ne[2]` in `ggml_vk_get_op_batch_size`, scheduler hook at
  `ggml\src\ggml-backend.cpp:959`, opt-out flag `--no-op-offload`.
- **The 11.7 tok/s Flash prefill receipt was measured on a 22-token prompt** —
  `flash-ot.err.log`: "prompt eval time = 1884.70 ms / 22 tokens". 22 < 32: the offload
  never armed. **The deep-pack prefill regime for the experts-on-host operating point is
  unmeasured**, and "prefill 11.7 = deep packs unusable" on the phase board is an
  extrapolation from the wrong regime. `[MEASURED-HERE]` (receipt re-read)
- Ceiling arithmetic `[DERIVED]`: a 512-token ubatch sweeps the ~64–80 GB expert set once →
  at measured 13.3 GB/s pinned H2D ≈ 4.8–6.0 s/ubatch ≈ **85–105 tok/s transfer-bound
  prefill ceiling** (≈ 45–55 at 50 % gather efficiency) vs 11.7 measured. Decode streams
  ~1.3–1.4 GB/token with ~no reuse → ≈ **10 tok/s/card ceiling** at measured bandwidth —
  parity with the CPU-executed 10.6 — rising toward ~20 dual-card if both stream disjoint
  layer ranges. The strategic decode win is **co-residency immunity**: PCIe DMA doesn't
  fight production's CPU threads, so the measured 10.6 → 4.1 co-resident collapse
  (CPU contention) should largely disappear. `[UNSOURCED hypothesis — probe it]`
- Caveat on the streaming source: the flash-ot receipt ran experts **file-backed via mmap**
  (loader even warned "tensor overrides to CPU are used with mmap enabled — consider
  --load-mode none"). File-backed experts cost +6.3 GB commit but stream at page-fault/NVMe
  speed on cold touch; `--load-mode none` puts experts in real RAM (fast streams, but
  ~64–80 GB commit — R0 says that doesn't fit beside production). Probe both; the mmap-warm
  standby-cache middle ground (62.3 GB standby) is also in play for repeat runs.
- The exact partial-offload upload path was overhauled upstream in 2026-03 (second queue +
  timeline semaphores, initially **crashed on Intel**, fixed in a follow-up; "mixed results"
  on Intel Xe) — a knee-tree rebase consideration, and a reason to re-measure rather than
  trust old numbers. `[OS-unclear]`
  https://github.com/ggml-org/llama.cpp/pull/19976 · https://github.com/ggml-org/llama.cpp/pull/20233
- **Windows hard limit for any host-resident design**: WDDM budgets GPU-visible shared
  system memory at ~50 % of RAM (~**64 GB** here), non-adjustable — the full expert set
  cannot be GPU-visible at once; hybrid (part of experts in the 64 GB combined VRAM, rest
  host) is the viable shape. `[Windows]`
  https://learn.microsoft.com/en-us/answers/questions/3866676/subject-urgent-request-ability-to-reduce-shared-gp
- L0 USM fundamentals check out (kernels can read `zeMemAllocHost` memory over PCIe; costs
  "potentially higher per-access", spec) and NEO's 4 GB-per-allocation default needs the
  relaxed-limits flag — but **no engine ships GPU-execute-from-host-DDR MoE experts today**,
  on any Intel stack: OpenVINO migrates weights to device (host spill is an open feature
  request, https://github.com/openvinotoolkit/model_server/issues/4263); vLLM
  XPU/LLM-Scaler serve all-in-VRAM `[Linux-only]`; ktransformers' Intel path is Linux-beta
  and CPU-executes experts.
  https://oneapi-src.github.io/level-zero-spec/level-zero/latest/core/PROG.html ·
  https://github.com/intel/compute-runtime/blob/master/programmers-guide/ALLOCATIONS_GREATER_THAN_4GB.md
- **IPEX-LLM FlashMoE: dead end.** Repo **archived** (verified `"archived": true`, last push
  2026-01-28), Linux-only tgz, and its experts are **CPU-executed** on many-channel Xeons
  (the "380 GB CPU memory" requirement is the tell); a community measurement got 2.99 tok/s
  decode on Qwen3-235B with an A770 — worse than our local numbers.
  https://github.com/intel/ipex-llm/blob/main/docs/mddocs/Quickstart/flashmoe_quickstart.md ·
  https://github.com/intel/ipex-llm/issues/13194
- Decode-side hot-expert caching exists only as proposals/out-of-tree CUDA work (+10–57 %
  decode claims): https://github.com/ggml-org/llama.cpp/issues/20757 ·
  https://github.com/ggml-org/llama.cpp/discussions/24528. Nothing to adopt; our `-ot`
  hybrid regex can approximate "pinned expert subset" today (layer-granular, not
  popularity-granular).
- Escape hatch already in the flagship: `GGML_VK_PREFER_HOST_MEMORY`
  (`ggml-vulkan.cpp:6204`) forces allocations into host memory — all-or-nothing, but it
  makes the "GPU executes from host-visible memory" ceiling directly measurable on this
  driver with zero new code.

---

## Q3 — Loading & rotation: copy engines, prefetch, sub-8-second loads

**Verdict: the 8.2 s load is disk-API-bound, not PCIe-bound — ADOPT the upstream Windows
unbuffered-I/O fix (cherry-pick probe) before any L0 machinery. L0 BCS prefetch sidecar:
PARK. RAM-disk weight staging: IGNORE.**

Findings (knee-tree code reads all `[MEASURED-HERE]`, paths under `E:\work\llamacpp-knee`):

- **`--direct-io` is a no-op at the Windows file layer.** The Win32 `llama_file` ctor marks
  `use_direct_io` unused (`src\llama-mmap.cpp:86-95`); only Linux gets `O_DIRECT`
  (`:184-199`). What the flag buys on Windows is *disabling mmap*, which engages the good
  path: a 4-buffer pinned staging pipeline with async Vulkan uploads
  (`src\llama-model-loader.cpp:1443-1454,1596-1648`) — but fed by plain **buffered
  `ReadFile`** in 1 MiB staging chunks (Windows `read_alignment()==1`,
  `llama-mmap.cpp:387-391`). That explains the 2.3 GB/s ceiling exactly. The mmap path has
  no async pipeline at all (`llama-model-loader.cpp:1556-1583`) — consistent with mmap-warm
  measuring *slower* (12.7–13.3 s) than dio-cold 8.2 s `[RECEIPT R2]`.
- **Upstream fix exists**: PR #26014 "Windows unbuffered model load"
  (`FILE_FLAG_NO_BUFFERING` + aligned buffers, open as of 2026-07) measured **1–3 GB/s →
  >9 GB/s** on a Gen5 NVMe. `[Windows]` https://github.com/ggml-org/llama.cpp/pull/26014.
  At even 6 GB/s the 19 GB dense load drops 8.2 s → ~3.2 s. Caveat noted in-PR: >512 MB
  single reads regress; our loader reads ≤64 MiB chunks. Related: Linux async O_DIRECT lane
  https://github.com/ggml-org/llama.cpp/pull/18012 `[Linux-only]`; parallel loading across
  GPUs https://github.com/ggml-org/llama.cpp/pull/20062.
- Counter-signal for the MoE side: for models bigger than RAM with CPU offload, mmap beats
  direct I/O on **repeat** loads (page cache); direct-io re-runs can be much slower.
  `[Linux-only]` https://github.com/ggml-org/llama.cpp/discussions/18758. Don't blanket-apply
  the dense model's dio win to 88 GiB Flash rotations.
- **PCIe has ~5× headroom over the load path** — 13.3 GB/s pinned H2D measured vs 2.3 GB/s
  achieved. The wire is never the bottleneck at load time; the file API is.
- **Copy engines**: each B70 exposes exactly one COPY-only queue group `[MEASURED-HERE]` —
  BMG carries one main BCS (no PVC-style link engines;
  `hw_info_bmg.cpp` `[OS-unclear — shared NEO source]`). An L0 sidecar owning the BCS while
  Vulkan owns compute is architecturally sound (Stage 7a proved L0+Vulkan co-residency at
  0.99 `[RECEIPT]`), and nobody ships "sidecar pre-stages the next GGUF" (closest prior art
  is vLLM sleep mode L1 — weights parked in CPU RAM, wake in seconds, `[Linux-only]`
  https://vllm-project.github.io/2025/10/26/sleep-mode.html). But there is nothing for the
  sidecar to feed today: llama.cpp only loads weights from a path/FILE*
  (`include\llama.h:512-514`), so a prefetcher can only warm page cache — and R2 already
  refuted page-cache warming as a win on this stack. **PARK until #26014-class loads
  disappoint; revive trigger = swap-latency budget still dominated by load_s after P4.**
- **RAM-disk weight staging: IGNORE.** 88 GiB cannot coexist with production's commit
  (96.9/135.3 GB live `[RECEIPT R0]`); 19 GB staging is feasible but buys less than #26014
  at far more memory risk.
- llama-swap already covers "don't rotate at all" shapes for the dense model (`groups`,
  `persist`, `preload` — https://github.com/mostlygeek/llama-swap/blob/main/config.example.yaml),
  proven in P5/R6 `[RECEIPT]`.

---

## Q4 — KV movement: faster or file-less save/restore

**Verdict: ADOPT a RAM-disk slot-save dir (cheap probe, fits R2b); BUILD-lite option for
Phase 2 = wrap the existing file-less state API. L0 copy engines unnecessary here.**

Findings:

- The save path is strictly serial: per KV chunk, a **fenced** Vulkan D2H copy into a temp
  buffer, then a buffered `WriteFile` (≤64 MiB chunks, no overlap, no unbuffered flag) —
  `tools\server\server-context.cpp:2475-2506` → `src\llama-context.cpp:2676-2678,3159-3169`
  → `src\llama-mmap.cpp:129-167`; Vulkan fence-per-read at `ggml-vulkan.cpp:16050-16060`.
  `[MEASURED-HERE]` (code read)
- Measured 1.66 GB/s save / ~2.3 GB/s restore `[RECEIPT P2/P3]` vs 14.1 GB/s pinned D2H
  `[MEASURED-HERE]`: the file term dominates, the PCIe term is ~0.2 s of the 1.74 s. A
  RAM-disk target removes the storage term; residual floor ≈ fenced D2H + serialization,
  plausibly **~0.5–1.0 s save / ~0.4–0.8 s restore** `[DERIVED]`.
- RAM-disk state of the art on Windows: OSFMount (free) benchmarks ~6.8 GB/s read /
  ~4.9 GB/s write `[Windows]`
  https://forums.passmark.com/osforensics-osfmount-osfclone/49961-osfmount-exfat-or-ntfs-ram-drive-speed ·
  https://whatsoftware.com/12-ram-disk-software-benchmarked-for-fastest-read-and-write-speed/ —
  an 8 GB volume costs 6 % of RAM and comfortably holds the 2.68 GB slot files (naming
  manifest still mandatory — the format carries no model identity `[RECEIPT P4]`).
- **True file-less parking already exists at the library level**:
  `llama_state_seq_get_data_ext/set_data_ext` + `LLAMA_STATE_SEQ_FLAGS_ON_DEVICE`
  (`include\llama.h:865-928`, flag `:907`) — state to a caller buffer or parked
  device-side, no file at all. The gap is purely that llama-server's `/slots` endpoint only
  exposes the `_save_file` variant (`server-context.cpp:2504`). A small server patch (or a
  door-side wrapper process) is the Phase-2-grade "KV hydration hooks" implementation path —
  aligning with the WRAP verdict already on the board.
- L0 copy engines offer nothing here: the D2H hop is already 8× faster than the file hop,
  and KV lives in Vulkan-owned VRAM a foreign L0 process cannot touch (cross-process VRAM
  is invisible on Windows Vulkan `[RECEIPT P1]`).

---

## Q5 — Queuing/routing seats: OVMS on NPU + iGPU (the registered side lane)

**Verdict: ADOPT — this is the one place L0 genuinely unlocks silicon Vulkan cannot see.
Design refined below; expectations calibrated (the NPU is an embeddings/triage seat, not a
fast router-LLM).**

Findings:

- **OVMS is Windows-native since v2025.0** (bare-metal binary, Win11; "functional parity
  with Linux" minus cloud storage/C-API/DAG) and current v2026.3.0 (2026-08-04) serves
  OpenAI-compatible `chat/completions` + `embeddings` and Cohere-compatible `rerank`; v2025.0
  explicitly added Battlemage GPU and Arrow Lake CPU/iGPU/NPU support. `[Windows]`
  https://github.com/openvinotoolkit/model_server/releases/tag/v2025.0 ·
  https://github.com/openvinotoolkit/model_server/releases
- **Per-servable device placement** (`target_device` per model in one `config.json`; for
  GenAI servables the graph carries `device`; `device=NPU` selects the stateful servable,
  anything else gets continuous batching) — so one instance can hold a 1–3B LLM on the iGPU
  and an embedder on the NPU. Composition, not a quoted guarantee — verify live.
  https://github.com/openvinotoolkit/model_server/blob/main/docs/accelerators.md ·
  https://github.com/openvinotoolkit/model_server/blob/main/docs/llm/reference.md
- **NPU reality (13-TOPS NPU3-class, first desktop NPU)**: LLM exports must be INT4
  **symmetric**, channel-wise for >1B; context is static-shape-managed (`MAX_PROMPT_LEN`
  default 1024, chunked prefill since OpenVINO 2025.3; OVMS 2026.3 caps NPU prompts at 8k);
  practical band 1–4B (docs top out ~8B). Closest measured class (Meteor Lake NPU, ~11
  TOPS): Qwen2.5-1.5B INT4 at ~6.8 words/s with a **95.9 s model load** — sub-CPU speed.
  `[Windows]` **MoE on NPU: no** — every MoE enablement note is CPU/GPU-only; NPU wants
  static dense graphs. Embeddings on NPU: documented **preview** (BGE-class, batch 1).
  https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html ·
  https://dev.to/mr1azl/i-tried-running-llms-on-intels-npu-heres-what-actually-happened-5h17 ·
  https://github.com/openvinotoolkit/model_server/blob/main/demos/embeddings/README.md
- **The ZE_AFFINITY_MASK landmine is avoidable by construction** `[MEASURED-HERE]` +
  `[Windows]`: the NPU is its own L0 driver (verified — 3 drivers under one loader);
  OpenVINO selects devices **by name** (`NPU`, `GPU.0` = iGPU always id 0, `GPU.1/GPU.2` =
  B70s), its GPU plugin is **OpenCL-based** (doesn't ride the GPU L0 path at all), and
  SYCL-side selection has `ONEAPI_DEVICE_SELECTOR`. Spec-level, L0 ≥1.10 adds
  `zeInitDrivers` for type-scoped init. Nothing in the seat pool ever needs the env var.
  Our enumeration + Stage 7a both show plain zeInit is safe on 8974.
  https://github.com/openvinotoolkit/openvino/blob/master/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/gpu-device.rst ·
  https://oneapi-src.github.io/level-zero-spec/level-zero/latest/core/PROG.html
- Known Windows multi-UMD wedge (not our deadlock, but adjacent): stale `ze_loader` +
  NPU+GPU UMDs → NPU plugin failure in `zeGetExtensionFunctionAddress`; fix ships in the
  **graphics** driver (it owns the loader). Keep 8974+ current; NPU driver 4778 clears the
  documented ≥3104 floor (4841 available).
  https://community.intel.com/t5/Intel-Distribution-of-OpenVINO/Running-official-OpenVINO-model-with-target-device-NPU-leads-to/m-p/1752704
- **iGPU seat**: Arrow Lake-S iGPU is 4 Xe-core Xe-LPG (Alchemist-class, no XMX
  `[UNSOURCED]`) — 1–3B INT4 at ~10–25 tok/s class expected `[OS-unclear analog]`. Prefer
  the OpenCL path (OVMS `GPU.0`) over llama.cpp Vulkan for it: Intel-iGPU Vulkan has a
  documented garbage-output history (`[Linux-only]`
  https://github.com/ggml-org/llama.cpp/issues/19327, older `[OS-unclear]`
  https://github.com/ggml-org/llama.cpp/issues/12096). Contention note: the iGPU eats host
  DDR5 bandwidth — it throttles CPU-side work (and the experts-on-host lane), not the B70s'
  GDDR6. `[DERIVED]`
- Loud-fallback rule for the seats: the NPU's ~1–2 min model compile/load means **port-open
  ≠ model-ready** all over again — health-gate every seat on a real 1-token completion (and
  a real embedding call), same as the door's serviceability probes.

---

## Q6 — SYCL llama.cpp on the B70s

**Verdict: IGNORE. No regime on this hardware/OS wins today.**

- Head-to-head on the exact card (Arc Pro B70, Linux): single-stream decode parity (77.3 vs
  76 tok/s) but Vulkan wins 8-stream aggregate 176 vs 100; author: "always pick Vulkan".
  `[Linux-only]` https://jonathanmann.tech/blog/intel-arc-b70-llama-cpp-benchmarks/
- "Brutally bad SYCL performance on Battlemage": Gemma-4-26B on B50/B70 — SYCL 351 pp /
  13.3 tg vs Vulkan 1169 / 40.2. `[Linux-only]` https://github.com/ggml-org/llama.cpp/issues/22413
- **Windows correctness**: garbled output on B580 where Vulkan is correct (closed
  not-planned). `[Windows]` https://github.com/ggml-org/llama.cpp/issues/20169
- **Our exact production shape is broken**: dual-B70 layer split of a MoE ignores
  `--tensor-split` and attempts a single 25.4 GB allocation → OOM (closed not-planned).
  `[Linux-only]` https://github.com/ggml-org/llama.cpp/issues/22885
- The one SYCL bright spot (TP decode wins) requires P2P — closed on this box per Q1.
- **Re-check triggers**: upstream fixes #22885-class MoE allocation AND a Battlemage
  Windows correctness pass; or SYCL gains a host-resident-expert path Vulkan lacks. Watch
  like the TP canary — one flag, three minutes, on engine bumps.
  https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md

---

## Verdict table (build / adopt / ignore)

| Idea | Verdict |
|---|---|
| L0 P2P / oneCCL / L0 collectives to fix `-sm tensor` | **IGNORE** — silicon-gated on consumer Intel root ports; oneCCL Linux-only; keep watch-upstream-Vulkan stance |
| One L0 context spanning both cards ("implicit scaling") | **IGNORE** — multi-tile-only mechanism; host-mirror tax |
| Deep-pack prefill via existing op-offload (experts-on-host) | **ADOPT + PROBE P1** — already in the pinned binary; cliff was measured below the trigger |
| Hybrid expert placement (subset of experts in VRAM) | **BUILD-lite (config)** — `-ot` layer-range regexes; design after P1/P6 |
| GPU-execute-from-host-USM expert engine (L0/SYCL custom) | **PARK** — no engine ships it; measure the ceiling first (P6); WDDM caps GPU-visible host at ~64 GB |
| IPEX-LLM FlashMoE / ktransformers-XPU / OpenVINO oversized | **IGNORE** — archived / Linux / CPU-executed experts / open feature request |
| Windows unbuffered weight loads (upstream PR #26014) | **ADOPT (cherry-pick) — PROBE P4** |
| L0 BCS prefetch sidecar for rotation | **PARK** — sound but feeds nothing; revive only if load_s still dominates after P4 |
| RAM-disk weight staging | **IGNORE** — commit-infeasible at 88 GiB; dominated by P4 at 19 GB |
| RAM-disk KV slot-save dir | **ADOPT — PROBE P2** (R2b) |
| File-less KV parking (`…_get_data_ext` / ON_DEVICE wrap) | **BUILD-lite** — Phase 2 KV-hydration-hooks implementation path |
| OVMS seat pool: embeddings on NPU + 1–3B on iGPU | **ADOPT — PROBE P5** (already-registered side lane, design above) |
| NPU as fast router-LLM | **IGNORE** — single-digit tok/s class + ~1–2 min compile; triage/embeddings only |
| SYCL llama.cpp lane on B70s | **IGNORE** — re-check triggers listed |

---

## Ranked probe ladder (info-per-hour, with the number each moves)

Every probe: loud fallback, receipts to `E:\work\battlemage\rotation-phase1\`, production
restored + door-proved after any GPU window. None requires `ZE_AFFINITY_MASK`.

**P1 — Flash deep-pack prefill in the op-offload regime** (~45 min, GPU window or
accept co-resident contention noise; the flash-lite load itself is on-tap proven).
Re-run the experts-on-host config exactly (`-ot ".ffn_.*_exps.=CPU"`, `-c 16384`) and
measure prefill at 512/2 K/8 K-token prompts, sweeping `-ub 512 1024` and
`GGML_OP_OFFLOAD_MIN_BATCH=16` vs default; confirm `--no-op-offload` absent. Variant B:
same sweep with `--load-mode none` (watch commit — experts leave file-backing). **Moves:
prefill tok/s (11.7 → hypothesis 45–105) and the "deep packs unusable" claim on the board;
also re-log decode.** Phase board: experts-on-host side-lane follow-ups / Phase 1 remainder.

**P2 — RAM-disk KV slot dir** (~45 min incl. install; no B70 window if run against the
27B pin rung :8084 or a canary instance). Install OSFMount; `OSFMount.com -a -t vm -m T:
-s 8G -o format:ntfs`; point `--slot-save-path T:\kv\`; re-run the exact P2/P3 protocol
(29 K-token save/restore). **Moves: KV save_s 1.74 → ~0.5–1.0, restore_s 1.19 → ~0.4–0.8.**
Phase board: R2b (downgraded → this refines it) + Phase 2 KV hydration budget.

**P3 — clpeak transfer-bandwidth calibration** (~15 min, no window — co-residency proven).
Windows release binary of clpeak (https://github.com/krrishnarraj/clpeak), `clpeak
--transfer-bandwidth` per B70. Cross-checks torch's 13.3 GB/s pinned figure (Linux-class
reports reach 20–28 GB/s on Gen5 x8 — if clpeak agrees with the higher number, every
ceiling in Q2 scales ×1.5–2; if it agrees with 13, that's the real wire budget). Also
repeat the D2D staged copy to settle the 2.29 vs 5.05 asymmetry. **Moves: the constants
under every Q2/Q3 estimate.**

**P4 — cherry-pick PR #26014 (Windows unbuffered loads) onto the knee tree** (~2–3 h:
patch `src\llama-mmap.cpp` Win32 impl, rebuild, R2-protocol re-timing ×3). **Moves:
load_s 8.2 → hypothesis 2–4 for the 19 GB dense; shrinks every swap in the rotation
cost model.** Phase board: Phase 2 lifecycle (swap-latency budget). Guard: verify the 88 GiB
mmap lane (experts-on-host) still prefers mmap per discussion #18758.

**P5 — OVMS seat pool, NPU-first** (one afternoon, zero B70 involvement — the registered
side lane). OVMS v2026.3 Windows package; export Qwen3-class 1–3B INT4-**sym** channel-wise
(`optimum-cli … --sym --ratio 1.0 --group-size -1`) + a BGE-class embedder; one
`config.json`: embedder `target_device: NPU` (preview), LLM `GPU.0` (iGPU, OpenCL path);
health-gate = real completion + real embedding, not port-open. Then benchmark the R9
routing task Flash vs fx99 vs NPU/iGPU on identical work, and record embedding
throughput for the belief/corpus lane (no embedding lane exists today). Stretch: an
OVMS servable on `GPU.1` (B70) to test whether name-based selection coexists with the
Vulkan server (Stage 7a says yes for L0 compute; OpenCL is a new data point). **Moves:
routing (R9 comparison row), creates the embeddings lane; zero VRAM cost.**

**P6 — GPU-from-host execution ceiling** (~30 min, one B70, light window). Small dense
model (e.g. the 27B is too big; use a 3–8B Q4) on one card, `GGML_VK_PREFER_HOST_MEMORY=1`
vs normal, same prompts. The decode ratio is the measured Vulkan-executes-from-host-memory
penalty on driver 8974 — the one number that decides whether a selective
experts-in-host-visible-memory patch could ever beat CPU-executed experts at decode.
**Moves: the Q2 decode hypothesis from arithmetic to measurement.**

**P7 — hybrid expert ladder** (~1–2 h window; design after P1+P6). `-ot` layer-range
regexes (e.g. experts of blocks 24–47 → CPU, 0–23 resident) walking VRAM-resident expert
fraction; find operating points between +6.3 GB/10.6 tok/s and 60.4 GB/27.7 tok/s.
**Moves: the Flash-beside-production frontier (tok/s per GB commit).** Phase board:
experts-on-host follow-up ("hybrid split — hot experts on-card").

**Parked** (revive triggers named): L0 BCS prefetch sidecar (if load_s still dominates
after P4); ze_peer Windows build (only if a future driver claims P2P — canAccessPeer
already answered 0 today); SYCL re-canary (on the Q6 triggers); Linux/switch/AMD platform
experiments (out of scope for the Windows thesis box).

---

## Driver / OS caveat sheet (Windows + Arc, as of 2026-08-28)

- GPU driver **32.0.101.8974** owns `ze_loader.dll`; loader multi-UMD fixes ship with
  graphics drivers — keep it current before any NPU work.
- **Never set `ZE_AFFINITY_MASK`** on this driver (deadlocks L0 init — OMEN-LIMIT-TEST 7a).
  Select devices by name (OpenVINO `NPU`/`GPU.x`), by `ONEAPI_DEVICE_SELECTOR` (SYCL), by
  `GGML_VK_VISIBLE_DEVICES` (Vulkan), or by `zeInitDrivers` type scoping (L0 ≥1.10).
- NPU driver **32.0.100.4778** (separate L0 UMD; ≥3104 floor for OpenVINO GenAI; 4841
  available upstream).
- WDDM budgets GPU-visible shared host memory at ~50 % of RAM (~64 GB here), fixed.
- Cross-process VRAM is invisible to Windows Vulkan (per-process budgets only) — admission
  gates must track their own ledger, not query "free" `[RECEIPT P1]`.
- `intel/compute-runtime` on GitHub is the **Linux** build of NEO; Windows ships closed
  from the same codebase — engine/BCS facts usually carry over, kernel-side P2P policy
  does not.
- oneCCL, vLLM-XPU, LLM-Scaler serving, IPEX-LLM FlashMoE, io_uring loaders: **Linux-only**.
- OpenVINO has no Vulkan backend; the NPU is invisible to Vulkan — L0/OpenCL are the only
  doors to NPU and the cleanest door to the iGPU.

## Sources of record

Local: this session's probe scripts (scratchpad `ze_enum.py`, `xpu_bw.py`, `gguf_kv.py`),
`E:\work\battlemage\rotation-phase1\` receipts, `E:\work\llamacpp-knee` (file:line cited
inline), `OMEN-LIMIT-TEST-2026-08.html` Stage 7a, `ROTATION-PROGRAM.html` phase board.
Web: URLs inline per finding above. Four research transcripts archived in the session task
outputs (2026-08-28).
