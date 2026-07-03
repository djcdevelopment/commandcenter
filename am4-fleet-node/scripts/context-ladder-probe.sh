#!/usr/bin/env bash
set -eo pipefail

force=0
if [[ "${1:-}" == "--force" ]]; then
  force=1
fi

if [[ "${force}" != "1" ]]; then
  busy="$(for node in /dev/dri/renderD128 /dev/dri/renderD129; do [ -e "$node" ] && fuser -v "$node" 2>&1 || true; done)"
  if [[ "${busy}" == *"COMMAND"* ]]; then
    echo "render nodes are busy; refusing context ladder. Pass --force only if co-tenancy is deliberate." >&2
    echo "${busy}" >&2
    exit 2
  fi
fi

if [[ -f /opt/intel/oneapi/setvars.sh ]]; then
  # shellcheck disable=SC1091
  source /opt/intel/oneapi/setvars.sh >/tmp/am4-context-ladder-oneapi-setvars.log 2>&1 || true
fi

LLAMA_BENCH="${LLAMA_BENCH:-/home/derek/baseline/llama.cpp/build-sycl/bin/llama-bench}"
MODEL_FILE="${MODEL_FILE:-/home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf}"
PROMPT_TOKENS="${PROMPT_TOKENS:-512}"
GEN_TOKENS="${GEN_TOKENS:-32}"
REPETITIONS="${REPETITIONS:-1}"
TIMEOUT_SEC="${TIMEOUT_SEC:-420}"

if [[ -n "${CONTEXTS:-}" ]]; then
  read -r -a contexts <<< "${CONTEXTS}"
else
  contexts=(0 8192 16384 32768 65536 131072)
fi

if [[ -n "${MODES:-}" ]]; then
  read -r -a modes <<< "${MODES}"
else
  modes=(single0 single1 layer)
fi

run_mode() {
  case "$1" in
    single0) echo "-dev SYCL0 -sm none" ;;
    single1) echo "-dev SYCL1 -sm none" ;;
    layer) echo "-dev SYCL0/SYCL1 -sm layer -ts 1/1" ;;
    row0) echo "-dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 0" ;;
    row1) echo "-dev SYCL0/SYCL1 -sm row -ts 1/1 -mg 1" ;;
    tensor) echo "-dev SYCL0/SYCL1 -sm tensor -ts 1/1" ;;
    *) echo "unknown mode '$1'" >&2; return 1 ;;
  esac
}

echo "am4 context ladder"
echo "model=${MODEL_FILE}"
echo "contexts=${contexts[*]}"
echo "modes=${modes[*]}"
echo "prompt_tokens=${PROMPT_TOKENS} gen_tokens=${GEN_TOKENS} repetitions=${REPETITIONS}"

for mode in "${modes[@]}"; do
  args="$(run_mode "${mode}")"
  for ctx in "${contexts[@]}"; do
    echo
    echo "== mode=${mode} depth=${ctx} =="
    set +e
    # shellcheck disable=SC2086
    timeout "${TIMEOUT_SEC}s" "${LLAMA_BENCH}" \
      -r "${REPETITIONS}" \
      -m "${MODEL_FILE}" \
      -ngl 99 \
      -d "${ctx}" \
      -p "${PROMPT_TOKENS}" \
      -n "${GEN_TOKENS}" \
      -ctk q8_0 \
      -ctv q8_0 \
      ${args}
    rc=$?
    set -e
    echo "rc=${rc}"
    if [[ "${rc}" == "139" ]]; then
      echo "segfault; skipping remaining depths for mode=${mode}"
      break
    fi
  done
done
