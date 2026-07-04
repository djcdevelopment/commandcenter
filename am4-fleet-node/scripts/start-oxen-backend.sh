#!/usr/bin/env bash
set -eo pipefail

env_file="${AM4_OXEN_ENV:-/home/derek/.config/am4-fleet/oxen.env}"
if [[ -f "${env_file}" ]]; then
  while IFS= read -r raw; do
    line="${raw#"${raw%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" || "${line}" == \#* || "${line}" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    if [[ -z "${!key+x}" ]]; then
      export "${key}=${value}"
    fi
  done < "${env_file}"
fi

BACKEND_KIND="${BACKEND_KIND:-sycl}"
LLAMA_SERVER="${LLAMA_SERVER:-/home/derek/baseline/llama.cpp/build-sycl/bin/llama-server}"
MODEL_FILE="${MODEL_FILE:-/home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf}"
HOST="${BACKEND_HOST:-127.0.0.1}"
PORT="${BACKEND_PORT:-8080}"
CTX="${CTX:-16384}"
PARALLEL="${PARALLEL:-1}"
THREADS="${THREADS:-$(nproc)}"
TENSOR_SPLIT="${TENSOR_SPLIT:-1,1}"
DEVICE_LIST="${DEVICE_LIST:-0,1}"
SPLIT_MODE="${SPLIT_MODE:-layer}"
MAIN_GPU="${MAIN_GPU:-}"
KV_TYPE_K="${KV_TYPE_K:-q8_0}"
KV_TYPE_V="${KV_TYPE_V:-q8_0}"
NO_MMAP="${NO_MMAP:-0}"
NO_HOST="${NO_HOST:-0}"

if [[ "${BACKEND_KIND}" == "sycl" && -f /opt/intel/oneapi/setvars.sh ]]; then
  # Required for libsycl/libsvml and friends when systemd launches the service.
  # shellcheck disable=SC1091
  source /opt/intel/oneapi/setvars.sh >/tmp/am4-oxen-oneapi-setvars.log 2>&1 || true
fi

if [[ "${BACKEND_KIND}" == "vulkan" ]]; then
  export GGML_VK_VISIBLE_DEVICES="${GGML_VK_VISIBLE_DEVICES:-${DEVICE_LIST}}"
  export GGML_VK_DISABLE_COOPMAT="${GGML_VK_DISABLE_COOPMAT:-1}"
fi

if [[ ! -x "${LLAMA_SERVER}" ]]; then
  echo "llama-server not executable: ${LLAMA_SERVER}" >&2
  exit 2
fi

if [[ ! -f "${MODEL_FILE}" ]]; then
  echo "model file not found: ${MODEL_FILE}" >&2
  exit 2
fi

args=(
  -m "${MODEL_FILE}"
  -ngl 99
  -dev "${DEVICE_LIST}"
  -sm "${SPLIT_MODE}"
  -ts "${TENSOR_SPLIT}"
  -fa on
  -fit off
  -ctk "${KV_TYPE_K}"
  -ctv "${KV_TYPE_V}"
  -c "${CTX}"
  -np "${PARALLEL}"
  --threads "${THREADS}"
  --host "${HOST}"
  --port "${PORT}"
)

if [[ "${NO_MMAP}" == "1" ]]; then
  args+=(--no-mmap)
fi

if [[ "${NO_HOST}" == "1" ]]; then
  args+=(--no-host)
fi

if [[ -n "${MAIN_GPU}" ]]; then
  args+=(-mg "${MAIN_GPU}")
fi

exec "${LLAMA_SERVER}" "${args[@]}"
