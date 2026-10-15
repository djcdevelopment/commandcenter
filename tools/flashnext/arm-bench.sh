#!/usr/bin/env bash
# Campaign command for a fenced vLLM arm: engine cells against one seat. usage: arm-bench.sh <port> <label> [cells]
# Sources the seat key inside this process only; prints JSON rows; appends to results-20261015.jsonl.
set -eo pipefail
PORT="$1"; LABEL="$2"; CELLS="${3:-512:3,8192:3,30000:2}"; MODEL="${4:-qwen3.8-27b}"
set -a; . "$HOME/.config/omen-vllm/omen-api.env"; set +a
export OMEN_API_KEY="${VLLM_API_KEY:-$OMEN_ARC_TOKEN}"
D="$HOME/work/commandcenter-linux-flash/docs"
cd "$HOME/opt/llama.cpp-flashnext"
exec python3 -u bench_engine.py --engine vllm --url "http://127.0.0.1:$PORT" --tok-url "http://127.0.0.1:$PORT" --model "$MODEL" \
  --source "$D/rnd-log.md" --source "$D/FACTORY-FRONTIER-CARDS.md" --source "$D/sizing-map.md" --source "$D/delivery.md" \
  --cells "$CELLS" --out results-20261015.jsonl --label "$LABEL"
