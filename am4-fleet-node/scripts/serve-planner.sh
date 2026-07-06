#!/usr/bin/env bash
# B70 PLANNER — Qwen3-30B-A3B, single card SYCL0, 127.0.0.1:8080 (oxen/vllama-planner).
# Managed by b70-planner.service. Deployed to /home/derek/baseline/ on AM4.
source /opt/intel/oneapi/setvars.sh >/tmp/oneapi-planner.log 2>&1 || true
/home/derek/baseline/b70-preflight.sh 27 planner || exit 3
exec /home/derek/baseline/llama.cpp/build-sycl/bin/llama-server \
  -m /home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf \
  -ngl 99 -dev SYCL0 -fa on -fit off -ctk q8_0 -ctv q8_0 -c 16384 \
  -np 1 --threads 12 --host 127.0.0.1 --port 8080
