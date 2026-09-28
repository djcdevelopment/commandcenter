#!/usr/bin/env bash
# start-am4-seat.sh <seat>  — one vLLM seat on ONE AM4 card, pinned by GPU UUID (ADR-0042: never by index).
# Reads ~/.config/am4-fleet/seat-<seat>.env: AM4_GPU_UUID AM4_PORT AM4_MODEL AM4_SERVED_NAME
#   AM4_MAX_MODEL_LEN AM4_MAX_NUM_SEQS AM4_GPU_MEM_UTIL AM4_QUANT AM4_TOOL_PARSER AM4_REASONING_PARSER AM4_EXTRA
# The tool-pair profile (2026-09-28): two of these, one per card, behind the oxen facade aliases
# am4-tool-4070ti / am4-tool-5070. The dense TP2 profile is am4-vllm.service (run-vllm-canary.sh).
set -euo pipefail
seat=${1:?seat name (e.g. 4070ti | 5070)}
env_file="$HOME/.config/am4-fleet/seat-${seat}.env"
[[ -r "$env_file" ]] || { echo "no seat env: $env_file" >&2; exit 2; }
set -a; . "$env_file"; set +a
: "${AM4_GPU_UUID:?}" "${AM4_PORT:?}" "${AM4_MODEL:?}" "${AM4_SERVED_NAME:?}"
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$AM4_GPU_UUID"
export VLLM_USE_FLASHINFER_SAMPLER=0
export CUDA_HOME=/home/derek/.venvs/vllm-cuda-030/lib/python3.12/site-packages/nvidia/cu13
export CUDA_PATH="$CUDA_HOME"
export PATH="$CUDA_HOME/bin:/home/derek/.venvs/vllm-cuda-030/bin:$PATH"
extra=()
[[ -n "${AM4_QUANT:-}" ]] && extra+=(--quantization "$AM4_QUANT")
[[ -n "${AM4_TOOL_PARSER:-}" ]] && extra+=(--enable-auto-tool-choice --tool-call-parser "$AM4_TOOL_PARSER")
[[ -n "${AM4_REASONING_PARSER:-}" ]] && extra+=(--reasoning-parser "$AM4_REASONING_PARSER")
[[ -n "${AM4_EXTRA:-}" ]] && read -r -a more <<<"$AM4_EXTRA" && extra+=("${more[@]}")
exec /home/derek/.venvs/vllm-cuda-030/bin/vllm serve "$AM4_MODEL" \
  --host 127.0.0.1 --port "$AM4_PORT" \
  --served-model-name "$AM4_SERVED_NAME" \
  --dtype float16 \
  --max-model-len "${AM4_MAX_MODEL_LEN:-32768}" --max-num-seqs "${AM4_MAX_NUM_SEQS:-2}" \
  --gpu-memory-utilization "${AM4_GPU_MEM_UTIL:-0.90}" \
  --language-model-only \
  "${extra[@]}"
