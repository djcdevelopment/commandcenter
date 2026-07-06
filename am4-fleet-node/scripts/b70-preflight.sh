#!/usr/bin/env bash
# Host-RAM preflight gate for the B70 llama-server slots — a Linux port of vllama's
# max_host_used_gb_preflight. AM4 has 32 GB DDR4; VRAM is plentiful (2x32 GB) but host
# RAM is the binding constraint (see am4-fleet-node/B70-CARD-MANAGEMENT.md). Refuse to
# launch a slot when available host RAM is below the model's floor, so a co-loaded model
# fails CLOSED (systemd marks it failed) instead of OOM-killing a running one mid-run.
#   usage: b70-preflight.sh <required_avail_gib> <label>
set -u
req="${1:?usage: b70-preflight.sh <required_avail_gib> <label>}"
label="${2:-model}"
avail=$(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo)
if [ "$avail" -lt "$req" ]; then
  echo "b70-preflight REFUSED: $label needs >=${req}GiB available host RAM, only ${avail}GiB free" \
       "(32GB DDR4 budget). Not starting — would risk an OOM cascade. Free a slot" \
       "('systemctl --user stop b70-critic') or lower a context." >&2
  exit 3
fi
echo "b70-preflight ok: ${avail}GiB available >= ${req}GiB for $label"
