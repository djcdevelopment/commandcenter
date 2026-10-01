#!/usr/bin/env bash
set -euo pipefail

gpu=${1:?Pass B70 GPU index 0 or 1}
case "$gpu" in
  0) port=18091 ;;
  1) port=18092 ;;
  *) echo 'B70 GPU index must be 0 or 1' >&2; exit 2 ;;
esac

model=${OMEN_MODEL:-/home/derek/models/qwen3-30b-a3b-gptq-int4}
port=${OMEN_PORT:-$port}
served_name=${OMEN_SERVED_NAME:-qwen3-30b-a3b}
vllm=/home/derek/.venvs/vllm-xpu-030/bin/vllm
test -f "$model/config.json"
[[ -f "$model/model.safetensors" || -f "$model/model.safetensors.index.json" ]]
test -x "$vllm"

export ZE_AFFINITY_MASK="$gpu"
extra=()
if [[ ${OMEN_EAGER:-0} == 1 ]]; then
  unset VLLM_XPU_ENABLE_XPU_GRAPH
  extra+=(--enforce-eager)
else
  export VLLM_XPU_ENABLE_XPU_GRAPH=1
  # OMEN_GRAPH_SIZES: JSON list of capture sizes (default matches the measured 09-23 recipe)
  extra+=(--compilation-config "{\"cudagraph_mode\":\"FULL_DECODE_ONLY\",\"cudagraph_capture_sizes\":${OMEN_GRAPH_SIZES:-[1,2,4,8]}}")
fi

# Stage 0 (2026-09-27) recipe knobs. Defaults reproduce the previous behaviour exactly.
if [[ ${OMEN_PREFIX_CACHE:-0} == 1 ]]; then
  extra+=(--enable-prefix-caching)
else
  extra+=(--no-enable-prefix-caching)
fi
if [[ -n ${OMEN_KV_CACHE_BYTES:-} ]]; then
  extra+=(--kv-cache-memory-bytes "$OMEN_KV_CACHE_BYTES")   # overrides --gpu-memory-utilization for the KV pool
fi
# systemd Environment= strips double quotes, so JSON cannot survive a drop-in. Take an integer instead.
if [[ -n ${OMEN_MTP_K:-} ]]; then
  # OMEN_MTP_NO_BLOCK_DROP=1 keeps the last mamba block cacheable under MTP (vLLM "eagle block drop", experimental)
  nbd=""; [[ ${OMEN_MTP_NO_BLOCK_DROP:-0} == 1 ]] && nbd=",\"disable_eagle_block_drop\":true"
  extra+=(--speculative-config "{\"method\":\"mtp\",\"num_speculative_tokens\":${OMEN_MTP_K}${nbd}}")
elif [[ -n ${OMEN_SPECULATIVE_CONFIG:-} ]]; then
  extra+=(--speculative-config "$OMEN_SPECULATIVE_CONFIG")
fi
if [[ ${OMEN_LANGUAGE_MODEL_ONLY:-0} == 1 ]]; then
  extra+=(--language-model-only)
fi
# Clients that cannot send chat_template_kwargs (OpenCode) get thinking off by default; explicit kwargs (HEARTH) still win.
if [[ ${OMEN_THINKING_DEFAULT_OFF:-0} == 1 ]]; then
  extra+=(--default-chat-template-kwargs "{\"enable_thinking\": false}")
fi
if [[ -n ${OMEN_TOKENIZER_MODE:-} ]]; then
  extra+=(--tokenizer-mode "$OMEN_TOKENIZER_MODE")   # "mistral" for tekken.json tokenizers
fi
if [[ -n ${OMEN_GDN_PREFILL_BACKEND:-} ]]; then
  extra+=(--gdn-prefill-backend "$OMEN_GDN_PREFILL_BACKEND")
fi
# Hybrid (GDN) models only hit the prefix cache on whole blocks (832/1088 tokens on XPU); a finer match unit
# registers partial-block state so a repeated prompt recomputes < unit tokens instead of up to a block (+1 under MTP).
if [[ -n ${OMEN_PREFIX_MATCH_UNIT:-} ]]; then
  extra+=(--prefix-match-unit "$OMEN_PREFIX_MATCH_UNIT")
fi
if [[ -n ${OMEN_MAX_BATCHED_TOKENS:-} ]]; then
  extra+=(--max-num-batched-tokens "$OMEN_MAX_BATCHED_TOKENS")   # speculative config otherwise caps scheduling at 2048
fi

extra+=(--enable-auto-tool-choice --tool-call-parser "${OMEN_TOOL_PARSER:-hermes}" --enable-prompt-tokens-details)   # usage.prompt_tokens_details.cached_tokens
if [[ -n ${OMEN_REASONING_PARSER:-} ]]; then
  extra+=(--reasoning-parser "$OMEN_REASONING_PARSER")
fi

if [[ ${OMEN_DRY:-0} == 1 ]]; then   # preflight: print the exact argv, one per line, and exit
  printf '%s\n' serve "$model" --host 127.0.0.1 --port "$port" --served-model-name "$served_name" \
    --dtype float16 --quantization "${OMEN_QUANTIZATION:-gptq}" --attention-backend TRITON_ATTN \
    --max-model-len "${OMEN_MAX_MODEL_LEN:-16384}" --max-num-seqs "${OMEN_MAX_NUM_SEQS:-16}" \
    --gpu-memory-utilization "${OMEN_GPU_MEM_UTIL:-0.82}" "${extra[@]}"
  exit 0
fi
exec "$vllm" serve "$model" \
  --host 127.0.0.1 --port "$port" \
  --served-model-name "$served_name" \
  --dtype float16 --quantization "${OMEN_QUANTIZATION:-gptq}" --attention-backend TRITON_ATTN \
  --max-model-len "${OMEN_MAX_MODEL_LEN:-16384}" --max-num-seqs "${OMEN_MAX_NUM_SEQS:-16}" \
  --gpu-memory-utilization "${OMEN_GPU_MEM_UTIL:-0.82}" \
  "${extra[@]}"
