#!/usr/bin/env bash
# Launch llama-server once and prove it is healthy before anything else runs.
# usage: launch-and-check.sh <logname> -- <run-flashnext.sh args...>
# Prints VRAM every 30 s, aborts (kill by pid) on: VRAM > 31.4 GiB on a card while loading,
# no /health ok within 8 min, or first-token decode < 10 tok/s. Never uses pkill -f.
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/$1.log"; shift; [[ "$1" == "--" ]] && shift
PORT="${PORT:-8001}"
LIMIT_MIB=32154   # 31.4 GiB
MAX_WAIT=480

vram() { for g in 0 1; do xpu-smi stats -d $g 2>/dev/null | awk -F'avg: ' '/GPU Memory Used/{split($2,a,","); printf "card%d=%dMiB ", '$g', a[1]}'; done; }

if pgrep -x llama-server >/dev/null; then echo "ABORT: a llama-server is already running (pid $(pgrep -x llama-server | head -1)); not launching"; exit 3; fi
cd "$HERE" && setsid nohup ./${RUNNER:-run-flashnext.sh} "$@" > "$LOG" 2>&1 < /dev/null &
sleep 4
PID=$(pgrep -x llama-server | head -1 || true)
echo "launched: $(date +%H:%M:%S) pid=${PID:-?} log=$LOG args: $*"
t0=$(date +%s)
while :; do
  now=$(date +%s); el=$((now-t0))
  PID=$(pgrep -x llama-server | head -1 || true)
  if [[ -z "$PID" && $el -gt 15 ]]; then echo "ABORT: server process exited during load"; tail -5 "$LOG" | cut -c1-200; exit 1; fi
  h=$(curl -s -m 3 "http://127.0.0.1:$PORT/health" || true)
  v=$(vram)
  if (( el % 30 < 5 )); then echo "t+${el}s $v health=${h:0:40}"; fi
  for n in $(echo "$v" | grep -o -E '[0-9]+MiB' | tr -d 'MiB'); do
    if (( n > LIMIT_MIB )); then echo "ABORT: $v exceeds ${LIMIT_MIB}MiB (memory cliff); killing pid $PID"; kill "$PID"; exit 1; fi
  done
  if echo "$h" | grep -q '"ok"'; then break; fi
  if (( el > MAX_WAIT )); then echo "ABORT: no health ok after ${MAX_WAIT}s; killing pid $PID"; kill "$PID"; exit 1; fi
  sleep 5
done
echo "READY after ${el}s: $(vram)"
r=$(timeout 120 curl -s -m 110 "http://127.0.0.1:$PORT/completion" -H 'Content-Type: application/json' \
   -d '{"prompt":"The capital of France is","n_predict":32,"cache_prompt":false,"temperature":0,"speculative.n_max":0}' || true)
dec=$(echo "$r" | python3 -c "import json,sys
try: t=json.load(sys.stdin)['timings']; print(round(t['predicted_per_second'],2))
except Exception as e: print(0)")
echo "first_token decode=${dec} tok/s  $(vram)"
if python3 -c "import sys; sys.exit(0 if float('$dec')>=10 else 1)"; then echo "PASS: server pid $PID is serving; nothing unloaded from here on"; else echo "ABORT: first-token decode ${dec} < 10 tok/s; killing pid $PID"; kill "$PID"; exit 1; fi
