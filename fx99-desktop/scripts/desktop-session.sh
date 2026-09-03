#!/bin/sh
set -eu

export DISPLAY=:0

xset s off
xset s noblank
xset dpms 0 0 0
xset -dpms
xsetroot -solid '#111318'
xrdb -merge /home/derek/.config/fx99-desktop/Xresources

openbox --sm-disable &
wm_pid=$!
sleep 1

tint2 &
pcmanfm --no-desktop /home/derek &
xterm \
  -title 'FX99 — ai-1' \
  -geometry 116x34+100+100 \
  -e /bin/bash -lc 'printf "FX99 virtual workstation\n\nGPU: RTX 2070 SUPER\nHost: ai-1 (192.168.12.220)\n\n"; exec /bin/bash -l' &

wait "$wm_pid"
