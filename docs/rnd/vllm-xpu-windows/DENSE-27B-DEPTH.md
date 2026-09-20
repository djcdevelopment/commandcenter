# Qwen3.8-27B at 64–128k on one B70 under vLLM-XPU/Windows — depth baseline and tunables

R&D loop opened 2026-09-20 (session cc-e0fdf649), continuing `WORKFLOW.md`. Question: the dense/hybrid 27B — the
fleet's depth worker — at the 64k–128k contexts our long-task use needs, on the vLLM stack that now serves the
30B-A3B at 82 tok/s. Baseline first, then every knob that moved or could move, with the measurement that says so.
Production reference for this model: llama.cpp SYCL dual-B70, prefill 813/817/736/614 tok/s at 16k/30k/60k/119k,
decode 9.4 tok/s at 119k (f16 KV), 20 tok/s with MTP (`docs/rnd/sycl-vs-vulkan/WORKFLOW.md`).

## The model is not what "dense" suggests

`SergiioB/Qwen3.8-27B-GPTQ-Int4-sym-G128-MTP-BF16` (the Arc Pro B70 cookbook author's checkpoint; 19 GB; GPTQ int4
sym g128, desc_act off; an MTP head in bf16; `Qwen3_5ForConditionalGeneration` with `language_model_only`):
**64 layers = 48 gated-delta-net (linear attention) + 16 full-attention** (`full_attention_interval` 4), 24 q heads /
4 kv heads, **head_dim 256**, 5120 hidden. Consequences on one 32 GB card:

- KV cache is only the 16 full layers: **64 KiB/token → 4.0 GiB at 64k, 8.0 GiB at 128k** (+ a small fixed GDN
  state per sequence). Weights 16.7 GiB. 128k needs `--gpu-memory-utilization 0.93` (8.33 GiB KV; 0.9 leaves 8.01).
- 48 of 64 layers never touch the KV cache: decode at depth is *not* dominated by KV reads the way the 30B-A3B or a
  classic dense model is — the linear layers are constant-cost per token.

## Getting it to run (walls, in order)

1. **`_xpu_C.gdn_attention`** — on XPU the GDN layer hard-routes to the SYCL-TLA gated-delta kernel, which faults
   on this Windows driver like FA2/MoE did; the GPU dies and the *next* Triton launch (the FLA gated RMSNorm) takes
   the access violation (0xC0000005 in `driver.py:launch`). Standalone, every FLA kernel and the norm are clean.
   Routed to `forward_cuda` (FLA Triton: causal_conv1d + chunk/fused-recurrent gated delta rule) on XPU
   (`VLLM_XPU_GDN_TLA=1` restores the native kernel). vllm-src branch `windows-xpu`.
2. V2 model runner's `warmup_kernels` hit the same fault before the fix; **V1 runner** (`VLLM_USE_V2_MODEL_RUNNER=0`)
   is what the baseline ran on. Re-test V2 + graphs with the GDN fix (uncertainty).
3. `--max-num-batched-tokens 16384` raises peak activation memory enough that 128k KV no longer fits at 0.93 —
   chunk size trades against context on one card. And it was *slower* (below).

Launch: `E:\work\vllm-xpu-win\serve-27b.cmd <log> <card> <port> <max_model_len> [args]` (`UTIL=0.93`,
`VLLM_USE_V2_MODEL_RUNNER=0`, `--enforce-eager` for the baseline).

## Baseline (eager, V1 runner, one stream, card 1)

| depth | prefill | decode after | needle |
| --- | --- | --- | --- |
| short chat | — | 14.8 tok/s | correct, T=0 identical ×3 |
| 13.6k | **183 tok/s** (74.5 s) | ~10.8 tok/s | FOUND |
| 13.6k, chunk 16k | 148 tok/s (91.6 s) | — | FOUND |
| **13.6k, tuned attention tiling** | **463 tok/s** (29.4 s) | ~13 tok/s | FOUND |
| **63.5k, tuned tiling** | **161 tok/s** (395 s) | **6.0 tok/s** (128 tok / 21.3 s) | FOUND |
| 128k | not run — ~20–25 min prefill at the O(n²) trend; needs a cue | | |

Reference, llama.cpp SYCL dual-B70 on this model: 736 tok/s prefill at 60k, ~9 tok/s decode at 119k (f16 KV), 20 with
MTP. On ONE card under vLLM/Windows the 64k point is 161 / 6.0 — prefill at 22 % and decode at ~65 % of the dual-card
SYCL seat, with the attention kernel still the O(n²) term (below).

**Where prefill goes (torch.profiler, 3.4k-token prefill, 477 tok/s):** `kernel_unified_attention` (TRITON_ATTN
prefill) **4.49 s of 6.84 s device time = 65 %** for the 16 full-attention layers — ~280 ms per layer ≈ 1 TFLOP/s on a
37-TFLOP card, and O(n²) in depth; oneDNN `int4_gemm_w4a16` 1.30 s (19 %); FLA `chunk_gated_delta_rule` 0.57 s (8 %).
Cause in the kernel wrapper: `BLOCK_M = 16` and `BLOCK_Q = BLOCK_M // num_queries_per_kv` = **2 query tokens per
program** for this GQA-6 model, `TILE_SIZE=32` over K/V, head_dim 256 — every program re-streams K/V for 2 tokens and
feeds DPAS 12-row dots. The only "large head" branch (`BLOCK_M=32, TILE=128, 8 warps`) is gated to NVIDIA
capability family 100. Added an XPU override (`VLLM_XPU_ATTN_PREFILL="BLOCK_M,TILE,num_warps,num_stages"`) and a
sweep harness (`probes\l7_attn_sweep.py`, 81 configs, seq 4096, correctness-checked against SDPA).

## Tunables (what moved, what could)

| lever | status | evidence / expectation |
| --- | --- | --- |
| **Prefill attention tiling** (`BLOCK_M`, `TILE_SIZE`, warps, stages) | **done: 3.06× on the kernel, 2.5× end to end at 13.6k** | `VLLM_XPU_ATTN_PREFILL=64,32,8,1` (vllm-src 61cc833); `TILE=128` exceeds Xe2's 128 KB SLM — the Blackwell "large head" tuning cannot even compile here; still O(n²): 463 → 161 tok/s from 13.6k to 64k |
| **The Linux delta** (Derek, 2026-09-20) | desk: the CUTLASS-SYCL kernels are validated on B70 under Linux with **IGC 2.11 → 2.34/2.38, compute-runtime 25.18 → 26.27, Level Zero 1.21 → 1.32** (`kernels/build_script/gpu_runtime_packages.json`, `vllm-src/docker/Dockerfile.xpu`); Windows 32.0.101.8974 embeds an unpublished IGC line; **newer Windows drivers exist: 32.0.101.8992 (Sept 2026) and 9030** | cheapest probe on the board: update the driver, rerun `l4_fa2.py` / `l4_moe.py` / the GDN op — 3 min to a verdict on FA2 + MoE + GDN natively; production Vulkan rides the same driver (re-run `vkdevices.py`), so it is Derek's call |
| FA2 (SYCL-TLA) prefill | blocked | faults on this Windows driver (lap 2); the Linux-validated fast path |
| `--max-num-batched-tokens` | measured | 16k chunk = −19 % prefill and costs 128k KV headroom; keep 8k; try 4k |
| XPU graphs for decode (sizes ≤ 8) + B70 tables | untested on this model | gave 4.4× single-stream on the 30B; GDN decode is `fused_recurrent` Triton + causal_conv1d — capturable? |
| Attention **decode** tiling at depth (`TILE_SIZE_DECODE=16`, 3D split-KV `num_par_softmax_segments`) | untested | at 128k the 16 full layers each walk 8 GiB of KV per token; decode 10.8 tok/s at 13.6k |
| MTP speculative decoding (`--speculative-config` with the checkpoint's MTP head) | untested | llama.cpp MTP doubled depth decode on this model (lap L4d in the SYCL program) |
| `--kv-cache-dtype fp8` | untested | halves the 8 GiB at 128k → room for two 128k streams or a 256k single; Xe2 has no fp8 math (dequant path) |
| int4 GEMM at large M (oneDNN) | 19 % of prefill | check M-dependence; `int4_gemm_w4a8` (A8) kernel exists in `_xpu_C` |
| FLA chunk kernel (`chunk_gated_delta_rule_fwd_kernel_h_blockdim64`) | 8 % of prefill | Triton, untuned for Xe2; block-dim knobs in `fla/ops/chunk*.py` |
| prefix caching | on | second call at 13.6k skipped prefill (10.6 s for 115 tokens) — long-document sessions benefit |
| two 64k streams / one 128k stream on one card; two cards = two seats | untested | KV 4 GiB per 64k stream; `max_num_seqs=8` today |
