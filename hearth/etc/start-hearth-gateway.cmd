@echo off
rem HEARTH gateway login-start wrapper (same posture as OmenOllamaTracingProxy).
rem Registered as scheduled task "HearthGateway" (logon trigger, user OMEN\derek).
cd /d C:\work\commandcenter
set HEARTH_SCOPE=C:\work\commandcenter
rem Secrets (e.g. Banked Fire's AM4_OXEN_TOKEN) live in this gitignored batch
rem fragment so they stay out of git but load on every gateway start/restart.
rem MUST be .cmd (not .env): `call` only executes .bat/.cmd as batch — calling a
rem .env silently opens it in the file's associated editor instead of running it.
rem Format: one `set NAME=value` per line. Optional.
if exist hearth\var\gateway.cmd call hearth\var\gateway.cmd
echo [%date% %time%] HearthGateway task starting >> hearth\var\gateway-task.log
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.kernel.gateway --callers hearth\var\callers.json --providers hearth.toolsurface.fs,hearth.toolsurface.git,hearth.toolsurface.testing,hearth.toolsurface.knowledge,hearth.toolsurface.summon,hearth.toolsurface.inference,hearth.toolsurface.task_lane >> hearth\var\gateway-task.log 2>&1
echo [%date% %time%] HearthGateway exited with %errorlevel% >> hearth\var\gateway-task.log
