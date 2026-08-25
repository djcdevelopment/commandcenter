@echo off
rem BF6 sidecar bridge -- carries AM4's render requests to HEARTH's door.
rem Registered as scheduled task "BF6RenderBridge" (LOGON trigger, OMEN\derek).
rem
rem Holds the least-privilege bf6-dispatcher credential (media_render only).
rem The key is read from hearth\var\bf6-dispatcher.key, which is gitignored.
cd /d C:\work\commandcenter
set HEARTH_SCOPE=C:\work\commandcenter;C:\work
if not exist hearth\var\log mkdir hearth\var\log
python -m hearth.media.bf6_bridge >> hearth\var\log\bf6-bridge.log 2>&1
