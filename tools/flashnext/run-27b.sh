#!/usr/bin/env bash
# Qwen3.8-27B Q4_K_M under llama.cpp SYCL on ONE B70 (card 1 = SYCL1), for the engine A/B against the vLLM seat.
# usage: run-27b.sh [--mtp] [extra llama-server args]
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="${BIN:-$HERE/build-sycl-f16/bin/llama-server}"
M=/mnt/omen-e-read/work/battlemage/models/qwen38
MODEL="$M/Qwen3.8-27B-Q4_K_M.gguf"; DRAFT="$M/mtp-Qwen3.8-27B-Q4_0.gguf"
PORT="${PORT:-8001}"; DEV="${DEV:-SYCL1}"
MTP=0; EXTRA=()
for a in "$@"; do case "$a" in --mtp) MTP=1;; *) EXTRA+=("$a");; esac; done
source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
export SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS=0 SYCL_CACHE_PERSISTENT=0 SYCL_DEVICE_FILTER=level_zero ZE_FLAT_DEVICE_HIERARCHY=COMPOSITE
MTPFLAGS=(); [[ $MTP == 1 ]] && MTPFLAGS=(-md "$DRAFT" -ngld 999 --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75 --spec-draft-device "$DEV")
exec "$BIN" -m "$MODEL" --device "$DEV" -ngl 99 -fa on -t 6 -tb 14 -np 1 -c 32768 -b 1024 --ubatch-size 1024 \
  --no-warmup --host 127.0.0.1 --port "$PORT" --temp 0 "${MTPFLAGS[@]}" "${EXTRA[@]}"
