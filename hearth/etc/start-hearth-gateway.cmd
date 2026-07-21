@echo off
rem HEARTH gateway login-start wrapper (same posture as OmenOllamaTracingProxy).
rem Registered as scheduled task "HearthGateway" (logon trigger, user OMEN\derek).
cd /d C:\work\commandcenter
rem Multi-root sandbox: first root is primary (repo-relative paths resolve there);
rem C:\work widens containment so files= can pack from any repo by absolute path.
set HEARTH_SCOPE=C:\work\commandcenter;C:\work
rem Secrets (e.g. Banked Fire's AM4_OXEN_TOKEN) live in this gitignored batch
rem fragment so they stay out of git but load on every gateway start/restart.
rem MUST be .cmd (not .env): `call` only executes .bat/.cmd as batch — calling a
rem .env silently opens it in the file's associated editor instead of running it.
rem Format: one `set NAME=value` per line. Optional.
if exist hearth\var\gateway.cmd call hearth\var\gateway.cmd
rem Boot-safe logging (2026-07-20). A stale EXCLUSIVE handle on the primary log
rem -- e.g. a socketless zombie gateway that survived a WinError-64 accept death
rem while still holding this file open -- must NEVER stop the door from starting.
rem The old code redirected the python launch straight to a fixed path; when that
rem path was locked, cmd could not open it, python never launched, and the task
rem exited 1 having written NOTHING to the very log meant to explain it. That was
rem an invisible ~40-minute outage (2026-07-20). Probe the primary log; a bounce
rem leaves the OLD wrapper's handle open for a second or two, so retry a few times
rem (that consolidates normal restarts onto the primary), and only if it is still
rem locked -- a genuinely wedged handle -- fall back to a unique per-launch file
rem so the launch redirect below can always open something and the door always
rem comes up. The sleep is `ping`, not `timeout`: doorcheck --revive launches this
rem script with stdin=DEVNULL, and `timeout` aborts under redirected stdin
rem ("Input redirection is not supported").
set "GWLOG=hearth\var\gateway-task.log"
set "_LOGTRY=0"
:hearth_trylog
(echo [%date% %time%] HearthGateway task starting)>> "%GWLOG%" 2>nul && goto hearth_gotlog
set /a _LOGTRY+=1
if %_LOGTRY% lss 6 (ping -n 2 127.0.0.1 >nul & goto hearth_trylog)
set "GWLOG=hearth\var\gateway-task-%RANDOM%%RANDOM%.log"
:hearth_gotlog
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.kernel.gateway --callers hearth\var\callers.json --providers hearth.toolsurface.fs,hearth.toolsurface.git,hearth.toolsurface.testing,hearth.toolsurface.knowledge,hearth.toolsurface.summon,hearth.toolsurface.inference,hearth.toolsurface.task_lane,hearth.toolsurface.fleet_harvest,hearth.toolsurface.patrol,hearth.toolsurface.masters_pet,hearth.toolsurface.dream,hearth.toolsurface.scheduler,hearth.toolsurface.am4,hearth.toolsurface.commander,hearth.toolsurface.build_requests >> "%GWLOG%" 2>&1
echo [%date% %time%] HearthGateway exited with %errorlevel% >> "%GWLOG%"
