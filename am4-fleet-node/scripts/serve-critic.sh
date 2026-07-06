#!/usr/bin/env bash
# B70 CRITIC — Qwen2.5-14B, single card SYCL1, 127.0.0.1:8081 (oxen/vllama-critic).
# Model on the 4TB /mnt/win mount. 8k ctx keeps host RAM inside the 32GB DDR4 budget
# (32k ctx held 7.8GiB and OOM-killed the co-resident planner — see B70-CARD-MANAGEMENT.md).
source /opt/intel/oneapi/setvars.sh >/tmp/oneapi-critic.log 2>&1 || true
/home/derek/baseline/b70-preflight.sh 27 critic || exit 3
exec /home/derek/baseline/llama.cpp/build-sycl/bin/llama-server \
  -m /mnt/win/work/battlemage/models/qwen2.5-14b-instruct-q4_K_M.gguf \
  -ngl 99 -dev SYCL1 -fa on -fit off -ctk q8_0 -ctv q8_0 -c 8192 \
  -np 1 --threads 12 --host 127.0.0.1 --port 8081
