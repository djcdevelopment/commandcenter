#!/usr/bin/env bash
# Qwen3.8-Flash-Next on two B70s, llama.cpp SYCL (cookbook recipe, pin 52d4268 + 2 patches).
# Usage: run-flashnext.sh [8k|16k|128k] [--mtp] [--no-cache-prompt]
# Model stays on the read-only E: mount via mmap (never --mlock / --no-mmap).
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="${BIN:-$HERE/build-sycl-f16/bin/llama-server}"
M=/mnt/omen-e-read/work/battlemage/models/qwen38-flash-next
MODEL="$M/UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"
DRAFT="$HOME/models/flash-next-mtp/Qwen3.8-Flash-Next-MTP-Q4_K_M.gguf"
PORT="${PORT:-8001}"

CTX="${1:-16k}"; shift || true
MTP=0; EXTRA=(); OT_DEFAULT=(-ot 'per_layer_token_embd=CPU')
for a in "$@"; do case "$a" in --mtp) MTP=1;; -ot|--override-tensor) OT_DEFAULT=(); EXTRA+=("$a");; *) EXTRA+=("$a");; esac; done

source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
export SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS=0
export SYCL_CACHE_PERSISTENT=0
export SYCL_DEVICE_FILTER=level_zero
export ZE_FLAT_DEVICE_HIERARCHY=COMPOSITE
export LLAMA_ATTN_ROT_DISABLE=1   # mandatory with quantized KV on qwen4exp

case "$CTX" in
  8k)   CTXFLAGS=(-c 8192   -b 512  --ubatch-size 512);;
  16k)  CTXFLAGS=(-c 16384  -b 3072 --ubatch-size 3072);;
  128k) CTXFLAGS=(-c 131072 -b 1024 --ubatch-size 1024);;
  *) echo "ctx must be 8k|16k|128k" >&2; exit 2;;
esac

MTPFLAGS=()
if [[ $MTP == 1 ]]; then
  MTPFLAGS=(-md "$DRAFT" -ngld 999 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75
            --spec-draft-backend-sampling --spec-draft-device SYCL1)
fi

exec "$BIN" -m "$MODEL" --device SYCL0,SYCL1 --tensor-split 1,1 --split-mode layer -ngl 99 \
  "${OT_DEFAULT[@]}" -fa on -t 6 -tb 14 -np 1 \
  --cache-type-k q8_0 --cache-type-v q4_1 --no-warmup --host 127.0.0.1 --port "$PORT" \
  --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 \
  "${CTXFLAGS[@]}" "${MTPFLAGS[@]}" "${EXTRA[@]}"
