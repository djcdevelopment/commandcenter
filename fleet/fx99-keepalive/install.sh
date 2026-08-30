#!/usr/bin/env bash
# install.sh — install the omen-arc keep-alive on fx99. Idempotent; safe to re-run.
#
# Run ON fx99, from a copy of this directory:
#   sudo ./install.sh              # install + enable + start
#   sudo ./install.sh --no-enable  # install files only, leave the timer stopped
#   sudo ./install.sh --dry-run    # show what it would do
#   sudo ./install.sh --disable    # stop and disable, leave files in place
#
# It deliberately does NOT touch OMEN. The two things that must be true on the OMEN side
# are listed in README.md and are the operator's to run: fx99's public key in OMEN's
# authorized_keys, and a reachable SSH transport.
set -euo pipefail

DEST=/opt/arc-keepalive
UNITS=/etc/systemd/system
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY=0
ENABLE=1
MODE=install
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-enable) ENABLE=0 ;;
    --disable) MODE=disable ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done
run() { if [ "$DRY" = 1 ]; then echo "  would: $*"; else "$@"; fi; }

if [ "$MODE" = disable ]; then
  run systemctl disable --now arc-keepalive.timer arc-keepalive-deep.timer
  echo "arc-keepalive: stopped and disabled (files left in $DEST)"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }

echo "installing arc-keepalive from $SRC"
run install -d -m 0755 "$DEST"
run install -m 0755 "$SRC/warm-omen-arc.sh" "$DEST/warm-omen-arc.sh"
run install -m 0644 "$SRC/arc-keepalive.service" "$UNITS/arc-keepalive.service"
run install -m 0644 "$SRC/arc-keepalive.timer"   "$UNITS/arc-keepalive.timer"
run install -m 0644 "$SRC/arc-keepalive-deep.service" "$UNITS/arc-keepalive-deep.service"
run install -m 0644 "$SRC/arc-keepalive-deep.timer"   "$UNITS/arc-keepalive-deep.timer"

# The log is written by the unit's user, not by root.
run touch /var/log/arc-keepalive.log
run chown derek:derek /var/log/arc-keepalive.log
run chmod 0644 /var/log/arc-keepalive.log

# Config is optional; create it only if absent so a hand-tuned host list survives.
if [ ! -f /etc/arc-keepalive.conf ]; then
  if [ "$DRY" = 1 ]; then
    echo "  would: write /etc/arc-keepalive.conf"
  else
    cat >/etc/arc-keepalive.conf <<'CONF'
# omen-arc keep-alive settings. LAN first: ADR-0014/0015 make the LAN the machine lane
# and reserve Tailscale for humans and Funnel. The tailnet address is a fallback only.
ARC_HOSTS="192.168.12.239 100.124.12.37"
ARC_USER="derek"
ARC_KEY="/home/derek/.ssh/id_omen"
CONF
    chmod 0644 /etc/arc-keepalive.conf
  fi
fi

run systemctl daemon-reload
if [ "$ENABLE" = 1 ]; then
  run systemctl enable --now arc-keepalive.timer
  run systemctl enable --now arc-keepalive-deep.timer
else
  # Installed but idle. Used when the OMEN-side transport is not open yet: an enabled
  # timer would otherwise burn a ~10 s failing SSH attempt every 30 s and fill the log
  # with noise that tells us nothing we do not already know.
  echo "  timer NOT enabled (--no-enable). Enable with:"
  echo "    sudo systemctl enable --now arc-keepalive.timer"
fi

if [ "$DRY" = 0 ]; then
  echo
  systemctl list-timers 'arc-keepalive*' --no-pager || true
  echo
  echo "verify with:  sudo -u derek /opt/arc-keepalive/warm-omen-arc.sh; tail /var/log/arc-keepalive.log"
fi
