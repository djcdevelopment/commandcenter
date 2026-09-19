# Levers for 256k Qwen3.8-27B — the inventory

Written 2026-09-19 08:50Z (session cc-544e4480) at Derek's ask: "instead of saying no, inventory what we have,
what exists." Objective: a 256k-token prompt on the dense 27B, prefilled fast (Derek's bar: ~3 min), decoded as
fast as the silicon allows, answers still right, production untouched. Everything below is something that
**exists on this fleet or in this fork today**, with its status and the lap that would test it. Verdicts are
recorded as what is measured, what is untested, and what would reopen a closed item — nothing is "no".

Where we stand (measured tonight, `docs/rnd-log.md` L4b/L4c): both B70s, SYCL, layer split, f16 KV,
`-ub 4096 -b 8192`: **248,515 tokens prefilled in 512.7 s (8.5 min, 485 tok/s), decode 5.89 tok/s**, correct
answer. The hybrid with AM4 (119k on the NVIDIA pair + the tail on the B70s) was 7.2 min at q4_0. Vulkan's knee
puts the same job past an hour.

## 1. What exists — hardware

- **OMEN** (Windows 11): 2× Arc Pro B70 32 GB (Vulkan, SYCL/Level Zero, OpenVINO `GPU.0/GPU.1`, OpenCL);
  Core Ultra 9 285K, 24 cores, no AVX-512; **127 GB DDR5**; Intel iGPU (Xe-LPG, `level_zero:2` / `SYCL2` /
  `GPU.2`, 69 GB shared); **NPU "Intel AI Boost"** (OpenVINO `NPU`); NICs: **Marvell AQtion 10GbE** (linked at
  1 Gbps — the far end is 1 GbE), Intel I226-V 1GbE; TB4 ports; WSL2 (Ubuntu, dual-Arc bridge kernel-dead since
  June) + Docker Desktop; E: NVMe holding the KV parking lot (`E:\work\battlemage\kv`).
- **AM4** (Ubuntu 26.04): RTX 4070 Ti 12 GB + RTX 5070 12 GB (CUDA 13.2; the Ti is the faster prefill seat);
  Ryzen 9 5900X 12C/24T; **30 GB RAM**; Intel I211 **1 GbE** + Wi-Fi 6; oneAPI 2026.0 (no Intel GPU); the 4 TB
  lab NVMe is currently not visible to the OS.
- **fx99** (Linux, always-on monitoring node): RTX 2070 SUPER 8 GB, CUDA, Ollama `:11434` with qwen2.5-coder:7b
  resident; LAN 192.168.12.220 wired; runs the keep-alive timers and the friend bastion.
- **Wires**: OMEN↔AM4 direct link 10.44.0.0/30 at 1 GbE (112 MB/s with the MemSplice header, 118.6 raw); LAN
  1 GbE to fx99 via the switch; Tailscale for humans only.

## 2. What exists — software and tooling

- **The knee fork** `E:\work\llamacpp-knee` @ `60cdd25` (+ the across-layouts restore, both hosts): Vulkan build
  (production, `build\`), **SYCL build** (`build-sycl\`, oneDNN + oneMKL, XMX FA), CUDA build on AM4 (`5d3ef7cf8`).
  Backends present in the tree but **OFF in every build**: `ggml-rpc` (remote GPUs as devices), `ggml-openvino`
  (CPU / Intel GPU / **NPU** via OpenVINO; text-only; NPU stateless, Q4_0, fixed prefill chunk 256, no `-np`;
  validated on ≤8B models), `ggml-opencl`.
- **Server flags that are levers** (all in this build): `-sm layer|tensor` (`row` unsupported on SYCL), `-ts`,
  `-dev`, `-ot <pattern>=<buffer>` (place named tensors on a chosen device/host), `-nkvo` (KV in host RAM),
  `--fit`, `--cache-ram`, `--ctx-checkpoints`, `--slot-save-path`, `-kvu`, `-ctk/-ctv`; speculative decoding
  `--spec-type draft-mtp | draft-eagle3 | draft-dflash | draft-simple | ngram-simple | ngram-map-k | ngram-map-k4v
  | ngram-mod | ngram-cache`, `-md` draft model, **`-devd` draft device**, `-ngld`, `--spec-draft-n-max`.
- **Models on the shelf** (OMEN): `Qwen3.8-27B-Q4_K_M` (19.0 GB), **`mtp-Qwen3.8-27B-Q4_0`** (1.7 GB — the MTP
  head), `mmproj-Qwen3.8-27B-Q8_0`, `mtp-Qwen3.8-Flash-Next`, the 30B-A3B, Llama-3.3-70B, gemma-3-27B, Mistral
  24B, gpt-oss-20b, phi-4, qwen2.5 14B/32B/coder-32B. AM4: the 27B, the 30B-A3B, qwen2.5-14B.
- **MemSplice** (`C:\work\memsplice`): slot save → wire → restore, proven vendor- and backend-agnostic;
  drivers for cold answers, depth ladders, concurrency, split prefill, body building; `sycl-env/seat/run.cmd`.
- **OpenVINO 2026.3** (`E:\work\battlemage\npuw-2026.3\.venv`): sees `CPU, GPU.0, GPU.1, GPU.2, NPU`. OpenVINO
  GenAI is **not** installed; Qwen3.8-27B needs GenAI **2026.4+** (day-0 support, int4 with the GDN layers kept at
  float — the official int4 card is marked experimental/broken, a community re-export works; one community
  measurement: **13.89 tok/s decode, short context, one B70**). HETERO/MULTI plugins exist for splitting across
  `GPU.0,GPU.1`. torch-xpu 2.14 venvs alongside.
- **HEARTH door / control plane / scheduler / rotation**: any seat becomes a pin-only rung with a stanza; the
  route can name a prefill target and a decode target; CP-SAT advisory scheduling exists.
- **Upstream**: llama.cpp PR #24406 (Vulkan Xe FA kernels) still open; our across-layouts PR parked.

## 3. The levers, by the limit they attack

### Limit A — deep prefill: 485 tok/s at 248k; the bar is ~1,400

- **A1. The four-GPU RPC pipeline** — MEASURED 2026-09-19 (L4f): loads and runs; on 1 GbE prefill 498 tok/s at 119k (dual B70 694) because f32 activations cross per ubatch with no async on RPC devices; decode 11.7 (+24 %). A decode lever until the link is faster. Originally: Build both hosts with `GGML_RPC=ON`, run
  `rpc-server` on AM4, launch OMEN's SYCL server with `--rpc 10.44.0.2:50052 -sm layer -ts …` so the 27B is
  layer-split across AM4's pair *and* the B70s. AM4's 128k ceiling stops mattering (it holds only its layers'
  KV); prefill scales with pipeline depth (expect ~2× the B70-alone rate → the 4-minute neighbourhood at 250k);
  no wire step, no restore. Per-ubatch crossing ≈ 10 MB (~95 ms at 1 GbE) against ~1.7 s of compute. Cost:
  two rebuilds (~20 min) + a lap. Risk: RPC over TCP on a Windows client is less travelled than Linux.
- **A2. AM4 carries all 256k** — a smaller weight quant on AM4 (IQ4_XS ≈ 15 GB, Q3_K_M ≈ 13 GB; a download)
  fits 256k of q4_0 KV on the 12 GB pair → prefill at ~1,200 tok/s ≈ **3.5 min**, then ship. Ship size is the
  catch: q4_0 5 GB (45 s) decodes slowly on the B70s; f16 16 GB (2.4 min at 1 GbE) decodes fast → see A3.
- **A3. 10 GbE** — OMEN already has the AQtion 10G port; AM4 needs a 2.5/10 GbE NIC (a $30–100 part). The
  wire goes 2.5–10×: an f16 256k state ships in ~16 s instead of 2.4 min. Makes A2 whole and hides most of the
  MemSplice transport everywhere.
- **A4. Two-card independent prefill** — SYCL single-card prefill equalled the dual pipeline at 16k (810 vs
  813), so for the job shop two prompts prefill on separate cards at ~2× aggregate; decode brings them together
  on tensor split via save/restore. Policy, no code.
- **A5. Batch shape** — `-ub 4096 -b 8192` bought +19 %; the next step flattened (+3 %). Spent.
- **A6. Vulkan #24406** — when merged, `build-vk-next.cmd` is ready; +39–83 % prefill on an Xe3 iGPU in the PR.
- **A7. OpenVINO GenAI on the B70s** — paged attention + continuous batching + KV compression exist there; depth
  behaviour unmeasured; the one short-context decode number (13.9 tok/s) is below llama.cpp's 21.5. Needs GenAI
  2026.4+ (a venv bump) and a re-export. Test only if A1–A3 stall.

### Limit B — decode at depth: 5.89 tok/s at 248k (f16, layer split); 6.74 at 119k (q4_0, tensor split)

- **B1. f16 KV on tensor split** — MEASURED (L4d): 10.24 vs 9.41 — does not stack (+9 %, prefill −22 %).
- **B2. MTP** — MEASURED (L4d): 119k 19.98 tok/s, 248k 14.37, exact. `--spec-type draft-mtp -md mtp-Qwen3.8-27B-Q4_0.gguf --spec-draft-n-max 3`. At depth a token's
  cost is the attention read; a 4-token verify pays it once. Nightshift measured 2.1× on Vulkan at short context
  (ADR-0038 notes a T=0 divergence to check). One flag.
- **B3. n-gram speculation** — MEASURED (L4d): 6.30 tok/s, worse than none on prose. `--spec-type ngram-cache` / `ngram-map-k4v`: drafts from the prompt's own
  repetition, **no draft model**. A 250k prompt of source code is highly repetitive; acceptance could be high.
  One flag, zero memory.
- **B4. oneDNN for decode** — the selector's ≥32-query-token gate keeps every decode step on TILE/VEC; lowering
  it for f16 KV at long KV is a one-line fork change. Unknown whether oneDNN's fused SDPA beats VEC at q=1.
- **B5. A draft model on the iGPU or CPU** — `-md <small Qwen> -devd SYCL2` (or CPU): keeps B70 VRAM for KV;
  needs a small draft GGUF on disk (none today; a ~1 GB download).
- **B6. q8_0 / asymmetric KV** — MEASURED (L4e): q8_0/q4_1 5.84 tok/s, f16/q4_0 5.80 — memory lever only; only full f16 reaches 9.41.

### Limit C — capacity and residency: how many 256k contexts, and where they wait

- Both B70s at f16 hold ~2 resident 256k contexts per card after weights; at q4_0, ~8. The across-layouts patch
  makes idle residency free (lap 12). Slot files park on NVMe (restore ≈ 1 s per 119k).
- **C1. `-nkvo`** — KV in OMEN's 127 GB RAM with compute on the GPUs: far bigger context than VRAM allows, at a
  large per-token cost (KV read over PCIe every step). Exists; not for speed.
- **C2. `-ot`** — pin named tensors (e.g. the 682 MB of CPU-mapped tensors, or an attention-only subset) to a
  chosen device. Exists; no measured use yet.

### Limit D — correctness and determinism

- Dense 27B is byte-identical across backends at a fixed `-ub`; changes with `-ub`. SYCL MoE is non-deterministic
  at T=0 (four runs, four answers). Speculative decoding at T=0 is expected to be equivalent but must be checked
  (B2/B3). The gate for any promotion is the accuracy suite (L8), not byte-equality.

### Limit E — the wire

- Streaming detour (`llama_state_seq_save_file` hook on AM4) — unbuilt; hides the ship under the prefill.
- A3 (10 GbE) is the cheaper, bigger win. A1 (RPC) removes the ship step entirely for the split case.

## 4. Assets that exist but do not move *this* objective — with the reopen condition

- **NPU (Intel AI Boost)**: reachable two ways — OpenVINO GenAI, and the fork's `ggml-openvino` backend
  (`GGML_OPENVINO=ON`, `GGML_OPENVINO_DEVICE=NPU`). Both are static-graph, stateless, ≤8B-class, 256-token prefill
  chunks, no parallel sequences. Not a 27B@256k engine. What it *could* do: host a small **draft** model for
  speculation (B5 via `-devd` if the ggml-openvino device works as a draft device — untested), or serve the
  job shop's small models. Prior verdict (NPU expert engine, 2026-08-29): closed on economics; reopen condition
  = a draft-device path that measurably lifts B70 decode, or NPU hardware with real bandwidth.
- **fx99 (2070 SUPER 8 GB)**: cannot hold the 27B (19 GB). Under A1 it can join the RPC pipeline as a third host
  carrying a handful of layers (`-ts` weighted small, since the pipeline runs at its slowest stage's pace); over the
  LAN, not the direct link. Marginal (~10 % more prefill capacity), and it already has a job (monitoring,
  keep-alive, small-model rung). Reopen when A1 is proven on four GPUs and the 10 GbE question is settled.
- **The iGPU (`SYCL2`)**: ~1/8 of a B70 on shared DDR5; as a pipeline stage it is the slowest; as a draft device
  (B5) it is plausible. Not a KV host.
- **The CPU (285K, 127 GB)**: draft device (B5), `-ot` target for host-side tensors, `--cpu-moe` for the Flash-Next
  MoE (not this objective). No AVX-512.
- **WSL2 / Docker → vLLM XPU**: dual-Arc bridge dead (ember ADR-0012); single-Arc never re-tested since June.
  Reopen = a WSL kernel that opens one adapter, or native Linux on OMEN.
- **`ggml-opencl`**: present, Adreno-focused, untested on Xe2. Low expectation.
- **Flash-Next (35B-A3B MoE)**: a different model; inherits the SYCL MoE non-determinism finding.
- **llm-scaler / vLLM containers**: Linux only — parked with the vLLM reopen condition.

## 5. Recommended order

1. **B1 + B2 + B3 in one window** (~30 min): f16 tensor split, MTP, n-gram — three relaunches on the 119k and
   248k bodies. Decode at depth is where tonight's momentum is.
2. **A1, the four-GPU RPC pipeline** (~1 h incl. builds): the only lever that changes the prefill *ceiling*
   rather than shaving it. If it works, fx99 can be tried as a fifth GPU afterwards.
3. **A3 + A2** (hardware + a download): a NIC for AM4 and an IQ4_XS 27B — the AM4-alone ~3.5-minute path with a
   16-second ship.
4. **B4** (fork patch) and **A7** (OpenVINO GenAI) only if 1–3 leave the bars unmet.
