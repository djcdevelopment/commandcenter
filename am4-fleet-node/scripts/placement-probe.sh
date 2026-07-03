#!/usr/bin/env bash
set -eo pipefail

force=0
if [[ "${1:-}" == "--force" ]]; then
  force=1
fi

if [[ "${force}" != "1" ]]; then
  busy="$(for node in /dev/dri/renderD128 /dev/dri/renderD129; do [ -e "$node" ] && fuser -v "$node" 2>&1 || true; done)"
  if [[ "${busy}" == *"COMMAND"* ]]; then
    echo "render nodes are busy; refusing placement probe. Pass --force only if co-tenancy is deliberate." >&2
    echo "${busy}" >&2
    exit 2
  fi
fi

if [[ -f /opt/intel/oneapi/setvars.sh ]]; then
  # shellcheck disable=SC1091
  source /opt/intel/oneapi/setvars.sh >/tmp/am4-placement-oneapi-setvars.log 2>&1 || true
fi

LLAMA_BENCH="${LLAMA_BENCH:-/home/derek/baseline/llama.cpp/build-sycl/bin/llama-bench}"
MODEL_FILE="${MODEL_FILE:-/home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf}"
CTX="${CTX:-16384}"
PROMPT_TOKENS="${PROMPT_TOKENS:-512}"
GEN_TOKENS="${GEN_TOKENS:-32}"

run_case() {
  local name="$1"; shift
  echo
  echo "== ${name} =="
  "${LLAMA_BENCH}" \
    -m "${MODEL_FILE}" \
    -ngl 99 \
    -d "${CTX}" \
    -p "${PROMPT_TOKENS}" \
    -n "${GEN_TOKENS}" \
    -ctk q8_0 \
    -ctv q8_0 \
    "$@"
}

run_case "sycl-single-device-0" -dev SYCL0 -sm none
run_case "sycl-single-device-1" -dev SYCL1 -sm none
run_case "sycl-layer-0-1" -dev SYCL0/SYCL1 -sm layer -ts 1/1
run_case "sycl-row-main-0" -dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 0
run_case "sycl-row-main-1" -dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 1
run_case "sycl-tensor-0-1" -dev SYCL0/SYCL1 -sm tensor -ts 1/1
