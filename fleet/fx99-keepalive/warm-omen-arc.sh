#!/usr/bin/env bash
# warm-omen-arc.sh — FX99's half of the omen-arc keep-alive (ADR-0043).
#
# Runs on fx99 (`ai-1`, 192.168.12.220). Reaches into OMEN over SSH and runs
# fleet/arcserve/warm-arc.ps1, which issues a 1-token completion against the rung's
# loopback port and appends a receipt on OMEN.
#
# WHY IT IS SHAPED LIKE THIS
#
#   * The SCHEDULE lives here, the SECRET lives on OMEN. fx99 never holds the bearer
#     token and never needs to: warm-arc.ps1 reads it from OMEN's own gitignored
#     gateway.cmd. All fx99 can do through this path is ask OMEN to warm itself.
#   * The rung listens on 127.0.0.1 ONLY. Nothing here punches that open; SSH is the
#     transport precisely so :8082 stays loopback-bound.
#   * A keep-alive that runs on the box it keeps alive dies with that box. That is the
#     whole reason this is not a scheduled task on OMEN.
#
# TRANSPORT ORDER is deliberate: the LAN address first, because ADR-0014/0015 make the
# LAN the machine lane and reserve Tailscale for humans and Funnel. The tailnet address
# is a FALLBACK, not the design — if the LAN path is the one that works, this script
# never touches the tailnet.
#
# Failure is quiet and non-actuating. If OMEN is unreachable or the rung is down, this
# logs and exits non-zero; it never restarts anything. Warming is observation plus a
# nudge, and a keep-alive that reaches for a restart would be a self-inflicted outage
# generator on a flaky link.
set -uo pipefail

CONF=/etc/arc-keepalive.conf
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

ARC_HOSTS="${ARC_HOSTS:-192.168.12.239 100.124.12.37}"
ARC_USER="${ARC_USER:-derek}"
ARC_KEY="${ARC_KEY:-$HOME/.ssh/id_omen}"
ARC_SCRIPT="${ARC_SCRIPT:-C:\\work\\commandcenter\\fleet\\arcserve\\warm-arc.ps1}"
# The first request after an idle gap takes ~11.5 s (ADR-0043), so anything under ~30 s
# here would time out on exactly the call this exists to make.
ARC_TIMEOUT="${ARC_TIMEOUT:-90}"
# 1 = cheap warm ping. 32 = the periodic DEEP probe, which is the only form that yields a
# measurable decode rate -- a 1-token ping cannot see the decode collapse it prevents.
ARC_TOKENS="${1:-${ARC_TOKENS:-1}}"
LOG="${ARC_LOG:-/var/log/arc-keepalive.log}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(stamp) $*" >>"$LOG" 2>/dev/null || echo "$(stamp) $*"; }

for host in $ARC_HOSTS; do
  out=$(timeout "$ARC_TIMEOUT" ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=8 \
        -o StrictHostKeyChecking=accept-new \
        -o ControlMaster=auto \
        -o ControlPath="/tmp/arc-ka-%r@%h:%p" \
        -o ControlPersist=10m \
        -i "$ARC_KEY" \
        "$ARC_USER@$host" \
        "powershell -NoProfile -ExecutionPolicy Bypass -File $ARC_SCRIPT -Tokens $ARC_TOKENS" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    log "ok via $host :: $(echo "$out" | tr -d '\r' | tail -1)"
    exit 0
  fi
  log "FAILED via $host (rc=$rc) :: $(echo "$out" | tr -d '\r' | tail -1)"
done

log "all transports failed; rung NOT warmed"
exit 1
