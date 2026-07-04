@echo off
rem mechnet-watchdog-patrol scheduled-task wrapper (5-min cheap-observer cadence).
rem Runs ONE cheap, observer-only patrol(refresh=False) snapshot tick: no liveness
rem probe, no masters_pet, no knowledge refresh. Appends a compact snapshot to
rem hearth\var\mechnet_watchdog_patrol_snapshots.json (capped ~12 entries / ~1h
rem buffer) and a best-effort mechnet_watchdog.patrol_snapshot ledger event.
rem The EXISTING 15-min "MechnetWatchdog" task (mechnet-watchdog.cmd) is separate
rem and unchanged in its own liveness+masters_pet behavior; it additionally reads
rem back the last 3 of these snapshots for a trend check (persistent/new/resolved).
rem Register every 5 min:
rem
rem   schtasks /Create /TN "MechnetWatchdogPatrol" /SC MINUTE /MO 5 /RL LIMITED /F ^
rem     /TR "C:\work\commandcenter\fleet\mechnet-watchdog-patrol.cmd"
rem
cd /d C:\work\commandcenter
echo [%date% %time%] mechnet-watchdog-patrol tick starting >> hearth\var\watchdog-patrol-task.log
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m fleet.mechnet_watchdog --patrol-only --json >> hearth\var\watchdog-patrol-task.log 2>&1
echo [%date% %time%] mechnet-watchdog-patrol tick exited with %errorlevel% >> hearth\var\watchdog-patrol-task.log
