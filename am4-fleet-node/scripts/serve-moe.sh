#!/usr/bin/env bash
# B70 MOE — gpt-oss-120b MXFP4, dual-card SYCL0+SYCL1 layer-split, 0.0.0.0:8082 (am4-moe rung).
# Managed by b70-moe.service. The resident mechnet big-MoE (router/worker).
# NOTE: replaces b70-planner/critic as the steady-state LLM tenant — do not
# start those units while this one holds both cards.
source /opt/intel/oneapi/setvars.sh >/tmp/oneapi-moe.log 2>&1 || true
/home/derek/baseline/b70-preflight.sh 27 moe || exit 3
set -a; source /home/derek/.config/am4-fleet/oxen.env >/dev/null 2>&1 || true; set +a
exec /home/derek/baseline/llama.cpp/build-sycl/bin/llama-server \
  -m /mnt/win/work/models/gpt-oss-120b/gpt-oss-120b-MXFP4.gguf \
  --alias gpt-oss-120b \
  -ngl 99 -dev SYCL0,SYCL1 -sm layer -ts 1,1 --n-cpu-moe 4 \
  -fa on -fit off -ctk q8_0 -ctv q8_0 -c 65536 -np 4 \
  --slots --metrics --jinja \
  --threads 12 --host 0.0.0.0 --port 8082 \
  ${AM4_OXEN_TOKEN:+--api-key "$AM4_OXEN_TOKEN"}
