#!/usr/bin/env bash
# Bring up the interactive half of AM4's Valheim node: a window manager plus a
# logged-in Steam client on the physical panel.
#
# Valheim's client build hard-requires a live Steam client. The game is still
# launched separately by start_game_bepinex.sh because Unix doorstop frequently
# fails to inject when Steam launches it. Steam running and a scripted game
# launch are both required.
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"

# Bare Openbox does not provide a desktop settings daemon. Keep Xft, the cursor,
# and Steam readable on the 28-inch 4K panel at the chosen 150% scale.
export XCURSOR_SIZE="${XCURSOR_SIZE:-36}"
export STEAM_FORCE_DESKTOPUI_SCALING="${STEAM_FORCE_DESKTOPUI_SCALING:-1.5}"
if [ -r "$HOME/.Xresources" ]; then
    xrdb -display "$DISPLAY" -merge "$HOME/.Xresources"
fi

# Make GTK dialogs usable in this minimal session.
export GTK_USE_PORTAL=0
export NO_AT_BRIDGE=1
export XDG_CURRENT_DESKTOP=GNOME

if ! command -v steam >/dev/null 2>&1; then
    echo "steam is not installed. Run first (it prompts for Valve's license):" >&2
    echo "  sudo apt install steam-installer" >&2
    exit 1
fi

if ! pgrep -x openbox >/dev/null 2>&1; then
    setsid nohup openbox > "$HOME/openbox.log" 2>&1 < /dev/null &
    sleep 2
fi

if ! pgrep -x steam >/dev/null 2>&1; then
    setsid nohup steam > "$HOME/steam.log" 2>&1 < /dev/null &
fi

sleep 3
echo "openbox : $(pgrep -x openbox >/dev/null && echo up || echo DOWN)"
echo "steam   : $(pgrep -x steam   >/dev/null && echo starting || echo DOWN)"
echo
echo "Log in on the monitor as waryfool. Leave Steam running, then launch:"
echo "  cd ~/valheim && DISPLAY=:0 ./start_game_bepinex.sh -console &"
