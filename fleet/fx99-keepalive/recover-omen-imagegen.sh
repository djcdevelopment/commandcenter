#!/usr/bin/env bash
# Ask OMEN to repair an abandoned image session. fx99 owns only the schedule;
# the fenced recovery decision, ArcServe restart task, logs, and secrets stay
# on OMEN. A healthy active image session is a no-op.
set -uo pipefail

CONF=/etc/arc-keepalive.conf
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

ARC_HOSTS="${ARC_HOSTS:-192.168.12.239 100.124.12.37}"
ARC_USER="${ARC_USER:-derek}"
ARC_KEY="${ARC_KEY:-$HOME/.ssh/id_omen}"
IMAGEGEN_RECOVERY_SCRIPT="${IMAGEGEN_RECOVERY_SCRIPT:-E:\\omen\\imagegen\\ops\\Invoke-ImageGenRecovery.ps1}"
RECOVERY_TIMEOUT="${IMAGEGEN_RECOVERY_TIMEOUT:-180}"
LOG="${IMAGEGEN_RECOVERY_LOG:-/var/log/imagegen-recovery.log}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(stamp) $*" >>"$LOG" 2>/dev/null || echo "$(stamp) $*"; }

for host in $ARC_HOSTS; do
  out=$(timeout "$RECOVERY_TIMEOUT" ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=8 \
        -o StrictHostKeyChecking=accept-new \
        -o ControlMaster=auto \
        -o ControlPath="/tmp/arc-ka-%r@%h:%p" \
        -o ControlPersist=10m \
        -i "$ARC_KEY" \
        "$ARC_USER@$host" \
        "powershell -NoProfile -ExecutionPolicy Bypass -File $IMAGEGEN_RECOVERY_SCRIPT" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    log "ok via $host :: $(echo "$out" | tr -d '\r' | tail -1)"
    exit 0
  fi
  log "FAILED via $host (rc=$rc) :: $(echo "$out" | tr -d '\r' | tail -1)"
done

log "all transports failed; image-session state unchanged"
exit 1
