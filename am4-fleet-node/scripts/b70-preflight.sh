#!/usr/bin/env bash
# Host-RAM preflight gate for the B70 llama-server slots — a Linux port of vllama's
# max_host_used_gb_preflight. AM4 has 32 GB DDR4; VRAM is plentiful (2x32 GB) but host
# RAM is the binding constraint (see am4-fleet-node/B70-CARD-MANAGEMENT.md).
#
# CEILING-based, NOT an available-floor: refuse to launch only when host RAM is ALREADY
# near-full (used > ceiling), so we don't pile a new model onto an exhausted box. An
# available-floor design crash-loops — two legitimately co-loaded models keep MemAvailable
# low, so the gate refuses their own systemd restarts (the 2026-07-06 planner crash-loop).
#   usage: b70-preflight.sh <max_used_gib> <label>
set -u
ceil="${1:?usage: b70-preflight.sh <max_used_gib> <label>}"
label="${2:-model}"
used=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "%d", (t-a)/1048576}' /proc/meminfo)
if [ "$used" -gt "$ceil" ]; then
  echo "b70-preflight REFUSED: $label — host RAM already ${used}GiB used (> ${ceil}GiB ceiling," \
       "32GB DDR4). Not starting to avoid an OOM cascade. Free a slot" \
       "('systemctl --user stop b70-critic') or lower a context." >&2
  exit 3
fi
echo "b70-preflight ok: ${used}GiB used <= ${ceil}GiB ceiling for $label"
