#!/bin/bash
set -euo pipefail
umask 077
root=/home/derek/.local/share/hermes-fleet
mkdir -p "$root" /home/derek/.local/bin /home/derek/.config/hermes-fleet
if [ ! -d "$root/hermes-agent/.git" ]; then
    git clone --depth 1 --branch v2026.9.14 https://github.com/NousResearch/hermes-agent.git "$root/hermes-agent"
fi
test "$(git -C "$root/hermes-agent" rev-parse HEAD)" = 345cd2b057a452236de401d3534b8502a7465e8d
python3 -m venv "$root/venv"
"$root/venv/bin/python" -m pip install --disable-pip-version-check -e "$root/hermes-agent[mcp]" 'mcp==1.28.1'
"$root/venv/bin/python" -m pip check
"$root/venv/bin/python" -m pip freeze > "$root/dependencies.lock.txt"
printf 'Pinned Hermes installed; no model or GPU changes made.\n'
