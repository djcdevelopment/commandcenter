#!/usr/bin/env bash
# wait-am4-seat.sh <seat> [max_seconds] — wait for an am4-tool@<seat> seat after (re)start; fail fast and loudly.
# Success: 127.0.0.1:$AM4_PORT/v1/models answers 200. Failure: the unit's restart counter rises, its Result is
# exit-code/signal/core-dump, or the journal shows an engine death / OOM. Mirrors OMEN's wait-vllm-seat.sh.
set -u
seat=${1:?seat}; max=${2:-900}; unit=am4-tool@$seat.service
set -a; . "$HOME/.config/am4-fleet/seat-${seat}.env"; set +a
start=$(date +%s); base_restarts=$(systemctl --user show "$unit" -p NRestarts --value)
fail() { echo "SEAT $seat FAILED after $(( $(date +%s)-start ))s: $1"; journalctl --user -u "$unit" --since "@$start" --no-pager | grep -i -E "error|exit|fatal|OOM|out of memory" | tail -6 | sed 's/.*\]: //' | cut -c1-240; exit 3; }
while :; do
  code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://127.0.0.1:${AM4_PORT}/v1/models")
  [ "$code" = 200 ] && { echo "SEAT $seat healthy after $(( $(date +%s)-start ))s"; exit 0; }
  r=$(systemctl --user show "$unit" -p NRestarts --value); res=$(systemctl --user show "$unit" -p Result --value)
  [ "$r" -gt "$base_restarts" ] && fail "unit restarted ($base_restarts -> $r), Result=$res"
  case "$res" in exit-code|signal|core-dump) fail "unit Result=$res";; esac
  journalctl --user -u "$unit" --since "@$start" --no-pager | grep -q -E "EngineCore.*(died|failed)|Engine core initialization failed|error: argument|CUDA out of memory|OutOfMemoryError" && fail "fatal pattern in journal"
  [ $(( $(date +%s)-start )) -ge "$max" ] && fail "timeout ${max}s (unit $(systemctl --user is-active "$unit"), Result=$res, restarts $r)"
  sleep 3
done
