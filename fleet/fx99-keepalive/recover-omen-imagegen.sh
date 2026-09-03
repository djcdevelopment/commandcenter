#!/usr/bin/env bash
# Ask OMEN to repair an abandoned image session. fx99 owns only the schedule;
# the fenced recovery decision, ArcServe restart task, logs, and secrets stay
# on OMEN. A healthy active image session is a no-op.
#
# TIMEOUT BUDGET -- these four numbers have to close, and they did not before:
#
#   OMEN verify loop   120 s   (recovery.py DEFAULT_VERIFY_TIMEOUT, passed explicitly)
#   per-host SSH       240 s   (must EXCEED the verify loop, or the only branch that does
#                               real work is guaranteed to be cut off mid-restart, leaving
#                               the fence held and the next tick re-arming the force-kill)
#   overall deadline   300 s   (bounds the 2-host fallback; without it two dead hosts cost
#                               2 x 240 s on their own)
#   TimeoutStartSec    600 s   (arc-keepalive-deep.service: covers this leg PLUS the 180 s
#                               worst case of warm-omen-arc.sh ahead of it)
#
# A tick that is genuinely recovering can therefore outlast the 5-minute timer. That is
# intended: systemd will not start a second copy while this one is active, and skipping one
# decode measurement is much cheaper than two concurrent ArcServe restarts.
set -uo pipefail

CONF=/etc/arc-keepalive.conf
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

ARC_HOSTS="${ARC_HOSTS:-192.168.12.239 100.124.12.37}"
ARC_USER="${ARC_USER:-derek}"
ARC_KEY="${ARC_KEY:-$HOME/.ssh/id_omen}"
IMAGEGEN_RECOVERY_SCRIPT="${IMAGEGEN_RECOVERY_SCRIPT:-E:\omen\imagegen\ops\Invoke-ImageGenRecovery.ps1}"
RECOVERY_TIMEOUT="${IMAGEGEN_RECOVERY_TIMEOUT:-240}"
RECOVERY_DEADLINE="${IMAGEGEN_RECOVERY_DEADLINE:-300}"
VERIFY_TIMEOUT="${IMAGEGEN_VERIFY_TIMEOUT:-120}"
LOG="${IMAGEGEN_RECOVERY_LOG:-/var/log/imagegen-recovery.log}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(stamp) $*" >>"$LOG" 2>/dev/null || echo "$(stamp) $*"; }

started=$(date +%s)
for host in $ARC_HOSTS; do
  now=$(date +%s)
  left=$(( RECOVERY_DEADLINE - (now - started) ))
  if [ "$left" -le 10 ]; then
    log "overall deadline reached; not trying $host"
    break
  fi
  budget=$(( left < RECOVERY_TIMEOUT ? left : RECOVERY_TIMEOUT ))
  out=$(timeout "$budget" ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=8 \
        -o StrictHostKeyChecking=accept-new \
        -o ControlMaster=auto \
        -o ControlPath="/tmp/arc-ka-%r@%h:%p" \
        -o ControlPersist=10m \
        -i "$ARC_KEY" \
        "$ARC_USER@$host" \
        "powershell -NoProfile -ExecutionPolicy Bypass -File $IMAGEGEN_RECOVERY_SCRIPT -VerifyTimeout $VERIFY_TIMEOUT" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    log "ok via $host :: $(echo "$out" | tr -d '\r' | tail -1)"
    exit 0
  fi
  log "FAILED via $host (rc=$rc) :: $(echo "$out" | tr -d '\r' | tail -1)"
done

log "all transports failed; image-session state unchanged"
exit 1
