#!/usr/bin/env bash
set -euo pipefail

# Text-only Qwen3.8-27B service on the mixed 5070 / 4070 Ti pair.
# The checkpoint was checksum-verified before promotion; the former GGUF
# server remains stopped and can be restored with restore-llama.sh.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export NCCL_P2P_DISABLE=1
export VLLM_USE_FLASHINFER_SAMPLER=0
export CUDA_HOME=/home/derek/bench27-cuda134/toolkit/nvidia/cu13
export FLASHINFER_WORKSPACE_BASE=/home/derek/bench27-cuda134/jit-workspace
export CUDA_PATH="$CUDA_HOME"
export PATH="$CUDA_HOME/bin:/home/derek/.venvs/vllm-cuda-030/bin:$PATH"
exec /home/derek/.venvs/vllm-cuda-030/bin/vllm \
  serve \
  /home/derek/models/qwen3-27b-gptq-int4 \
  --host \
  127.0.0.1 \
  --port \
  18094 \
  --served-model-name \
  qwen3-27b \
  --tensor-parallel-size \
  2 \
  --dtype \
  float16 \
  --quantization \
  gptq \
  --language-model-only \
  --gdn-prefill-backend \
  triton \
  --enable-auto-tool-choice \
  --disable-custom-all-reduce \
  --reasoning-parser \
  qwen3 \
  --tool-call-parser \
  qwen3_xml \
  --max-num-seqs \
  2 \
  --max-model-len \
  32768 \
  --gpu-memory-utilization \
  0.93
