@echo off
rem mechnet-watchdog scheduled-task wrapper (Banked Fire P4).
rem Runs one self-healing pass over fleet/inventory.toml: any down expect="up"
rem service with a declared `revive` command is revived once, re-probed, and the
rem outcome recorded on the HEARTH kernel ledger. Same login-start posture as
rem start-hearth-gateway.cmd. Register every 15 min (see the schtasks command in
rem HEARTH-BANKED-FIRE-STRATEGY.html, P4 row):
rem
rem   schtasks /Create /TN "MechnetWatchdog" /SC MINUTE /MO 15 /RL LIMITED /F ^
rem     /TR "C:\work\commandcenter\fleet\mechnet-watchdog.cmd"
rem
cd /d C:\work\commandcenter
echo [%date% %time%] mechnet-watchdog pass starting >> hearth\var\watchdog-task.log
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m fleet.mechnet_watchdog --json >> hearth\var\watchdog-task.log 2>&1
echo [%date% %time%] mechnet-watchdog exited with %errorlevel% >> hearth\var\watchdog-task.log
