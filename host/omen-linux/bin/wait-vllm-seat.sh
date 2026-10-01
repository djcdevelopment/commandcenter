#!/usr/bin/env bash
# wait-vllm-seat.sh <seat 0|1> [max_seconds]  — wait for a vLLM seat after (re)start; fail FAST and loudly.
# Success: /v1/models answers 200. Failure (any, within ~3 s of it happening):
#   - the unit's restart counter rises above its value at call time (crash loop)
#   - the unit's Result is exit-code/signal/core-dump
#   - the journal since START shows an EngineCore death, an argument error, or a device OOM
# A "--- Logging error ---" line is NOT a failure (vLLM 0.30 xpu.py logging bug). Prints elapsed seconds and the cause.
set -u
seat=${1:?seat}; max=${2:-900}; port=$((18091+seat)); unit=omen-vllm@$seat.service
set -a; . ~/.config/omen-vllm/omen-api.env; set +a
start=$(date +%s); base_restarts=$(systemctl --user show $unit -p NRestarts --value)
fail() { echo "SEAT $seat FAILED after $(( $(date +%s)-start ))s: $1"; journalctl --user -u $unit --since "@$start" --no-pager | grep -v -E "INFO|Logging error|logging/__init__|Message:|Arguments:|Traceback \(most recent call last\):$" | grep -i -E "error|exit|fatal|OOM|OUT_OF_RESOURCES" | tail -6 | sed 's/.*\]: //' | cut -c1-240; exit 3; }
while :; do
  code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $VLLM_API_KEY" http://127.0.0.1:$port/v1/models)
  [ "$code" = 200 ] && { echo "SEAT $seat healthy after $(( $(date +%s)-start ))s"; exit 0; }
  r=$(systemctl --user show $unit -p NRestarts --value); res=$(systemctl --user show $unit -p Result --value)
  [ "$r" -gt "$base_restarts" ] && fail "unit restarted ($base_restarts -> $r), Result=$res"
  case "$res" in exit-code|signal|core-dump) fail "unit Result=$res";; esac
  journalctl --user -u $unit --since "@$start" --no-pager | grep -q -E "EngineCore.*(died|failed)|Engine core initialization failed|error: argument|INVALIDARGUMENT|OUT_OF_RESOURCES|OutOfMemory|ZE_RESULT_ERROR" && fail "fatal pattern in journal"
  [ $(( $(date +%s)-start )) -ge "$max" ] && fail "timeout ${max}s (unit $(systemctl --user is-active $unit), Result=$res, restarts $r)"
  sleep 3
done
