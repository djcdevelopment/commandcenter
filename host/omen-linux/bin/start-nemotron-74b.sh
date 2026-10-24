#!/usr/bin/env bash
set -euo pipefail

extra_args=("$@")

LLAMA_SERVER="${OMEN_BOLT_BIN:-/home/derek/work/flash-next-lab-20261017/build-sycl-rpc/bin/llama-server}"
OMEN_BOLT_MODEL="${OMEN_BOLT_MODEL:-/home/derek/work/dual-b70-moe-lab-20261023/models/nemotron_35_bolt_74b_q4/UD-Q4_K_XL/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-UD-Q4_K_XL-00001-of-00003.gguf}"
OMEN_BOLT_ALIAS="${OMEN_BOLT_ALIAS:-nemotron-3.5-bolt-74b-a9b}"
OMEN_BOLT_PORT="${OMEN_BOLT_PORT:-18091}"
OMEN_BOLT_SLOTS="${OMEN_BOLT_SLOTS:-8}"
OMEN_BOLT_SLOT_CTX="${OMEN_BOLT_SLOT_CTX:-65536}"
OMEN_BOLT_TOTAL_CTX="${OMEN_BOLT_TOTAL_CTX:-$((OMEN_BOLT_SLOTS * OMEN_BOLT_SLOT_CTX))}"
OMEN_BOLT_SPLIT="${OMEN_BOLT_SPLIT:-53,47}"
OMEN_BOLT_BATCH="${OMEN_BOLT_BATCH:-4096}"
OMEN_BOLT_UBATCH="${OMEN_BOLT_UBATCH:-2048}"

llama_args=(
  -m "$OMEN_BOLT_MODEL"
  --alias "$OMEN_BOLT_ALIAS"
  -ngl 999
  --split-mode layer
  --tensor-split "$OMEN_BOLT_SPLIT"
  -dev SYCL0,SYCL1
  -c "$OMEN_BOLT_TOTAL_CTX"
  -np "$OMEN_BOLT_SLOTS"
  -b "$OMEN_BOLT_BATCH"
  -ub "$OMEN_BOLT_UBATCH"
  -ctk q8_0
  -ctv q8_0
  -fa on
  --fit off
  --jinja
  --no-warmup
  --reasoning-format deepseek
  --metrics
  --host 127.0.0.1
  --port "$OMEN_BOLT_PORT"
)

if [[ ${OMEN_DRY:-0} == 1 ]]; then
  printf '%s\n' "$LLAMA_SERVER" "${llama_args[@]}" "${extra_args[@]}"
  exit 0
fi

set +u
set -- --force
source /opt/intel/oneapi/setvars.sh >/dev/null 2>&1 || true
set --
set -u

export ONEAPI_DEVICE_SELECTOR="level_zero:0;level_zero:1"
export SYCL_CACHE_PERSISTENT=0
export GGML_SYCL_DISABLE_GRAPH=1
export SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS=1
export UR_L0_USE_IMMEDIATE_COMMANDLISTS=1

# Preflight checks: model exists, binary exists, no vllm holding B70 render nodes, port not bound.
if [[ ! -f "$OMEN_BOLT_MODEL" ]]; then
  echo "PREFLIGHT FAIL: model file not found: $OMEN_BOLT_MODEL" >&2
  exit 1
fi
if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "PREFLIGHT FAIL: llama-server binary not executable: $LLAMA_SERVER" >&2
  exit 1
fi
if pgrep -f "vllm serve" >/dev/null 2>&1; then
  echo "PREFLIGHT FAIL: vllm serve process is still running; stop omen-vllm@0 and omen-vllm@1 first" >&2
  exit 1
fi
for dev in /dev/dri/renderD128 /dev/dri/renderD129; do
  if [[ -e "$dev" ]]; then
    holders=$(fuser "$dev" 2>/dev/null || true)
    for pid in $holders; do
      if ps -p "$pid" -o args= 2>/dev/null | grep -qE "vllm|llama-server"; then
        echo "PREFLIGHT FAIL: GPU device $dev is held by PID $pid" >&2
        exit 1
      fi
    done
  fi
done
if ss -ltnH "( sport = :$OMEN_BOLT_PORT )" 2>/dev/null | grep -q ":$OMEN_BOLT_PORT"; then
  echo "PREFLIGHT FAIL: TCP port $OMEN_BOLT_PORT is already bound" >&2
  exit 1
fi

api_token="${OMEN_ARC_TOKEN:-${VLLM_API_KEY:-}}"
if [[ -n "$api_token" ]]; then
  llama_args+=(--api-key "$api_token")
fi

exec "$LLAMA_SERVER" "${llama_args[@]}" "${extra_args[@]}"
