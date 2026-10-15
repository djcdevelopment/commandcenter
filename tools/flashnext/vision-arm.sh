#!/usr/bin/env bash
# Campaign for the vision arm: the perception fixtures through the seat on <port>.
set -eo pipefail; PORT="$1"
set -a; . "$HOME/.config/omen-vllm/omen-api.env"; set +a; export OMEN_API_KEY="${VLLM_API_KEY:-$OMEN_ARC_TOKEN}"
F="$HOME/work/lab-rnd/research/evidence/daily-capability-20261001/selective-perception"
cd "$HOME/opt/llama.cpp-flashnext"
exec python3 -u vision_probe.py --url "http://127.0.0.1:$PORT" --model qwen3.8-27b --manifest "$F/manifest.json" --fixtures "$F/fixtures" --out results-20261015.jsonl --label 27b-vision-seat1
