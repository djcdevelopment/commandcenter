#!/usr/bin/env bash
# Loud, harvestable alert for a failed B70 unit. Deployed to /home/derek/baseline/.
# Invoked as: b70-alert.sh <unit-name>   (via OnFailure=b70-alert@%n.service)
#
# Why a log file and not a HEARTH record_event call: the gateway listens on
# 127.0.0.1:8710 on OMEN and is NOT reachable from AM4, and ADR-0014 keeps
# machine lanes off the tailnet. Rather than widen the gateway's surface, this
# writes a JSON line that the existing OMEN->AM4 SSH lane can harvest.
#
# This runs in the failure path, so it must never fail: no `set -e`, every
# probe is best-effort, and it always exits 0.
set -u

UNIT="${1:-unknown.service}"
LOG=/home/derek/baseline/b70-alerts.log
TS="$(date -Is)"

result="$(systemctl --user show "$UNIT" -p Result --value 2>/dev/null)"
nrestarts="$(systemctl --user show "$UNIT" -p NRestarts --value 2>/dev/null)"
status="$(systemctl --user show "$UNIT" -p ExecMainStatus --value 2>/dev/null)"
oom="$(journalctl --user -u "$UNIT" --since '-2h' --no-pager 2>/dev/null | grep -ci 'oom-kill')"
memfree="$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null)"
holders="$(fuser -v /dev/dri/renderD128 /dev/dri/renderD129 2>&1 | awk 'NR>1 {print $NF}' | sort -u | paste -sd, -)"

[ -z "$result" ]    && result=unknown
[ -z "$nrestarts" ] && nrestarts=0
[ -z "$status" ]    && status=""
[ -z "$oom" ]       && oom=0
[ -z "$memfree" ]   && memfree=0
[ -z "$holders" ]   && holders=none

msg="B70 UNIT FAILED: ${UNIT} result=${result} exec_status=${status} restarts=${nrestarts} oom_kills_2h=${oom} mem_available_gib=${memfree} card_holders=${holders}"

# 1) Journal, err priority, distinct tag.  Read it with:
#      journalctl --user -t b70-alert -p err --since today
printf '%s\n' "$msg" | systemd-cat -t b70-alert -p err 2>/dev/null

# 2) Durable JSON line -- the off-box harvest point.
printf '{"ts":"%s","unit":"%s","result":"%s","exec_status":"%s","n_restarts":%s,"oom_kills_2h":%s,"mem_available_gib":%s,"card_holders":"%s"}\n' \
  "$TS" "$UNIT" "$result" "$status" "$nrestarts" "$oom" "$memfree" "$holders" >> "$LOG" 2>/dev/null

exit 0
