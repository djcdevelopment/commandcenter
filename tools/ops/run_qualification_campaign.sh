#!/usr/bin/env bash
# run_qualification_campaign.sh — execute qualify workflow under an active experiment configuration.
# Switches OMEN serving profile via omen-profile switch and restores to two-lane on exit.
set -euo pipefail

CONFIG="${1:-seat0-devstral2507}"
OMEN_PROFILE="/home/derek/work/commandcenter-linux-flash/host/omen-linux/bin/omen-profile"
PYTHON="/home/derek/.venvs/hearth-private/bin/python"

cleanup() {
    "$PYTHON" "$OMEN_PROFILE" switch two-lane
}
trap cleanup EXIT INT TERM

"$PYTHON" "$OMEN_PROFILE" switch "$CONFIG"

"$PYTHON" /home/derek/work/lab-rnd/research/cli.py qualify --configuration "$CONFIG" --json
