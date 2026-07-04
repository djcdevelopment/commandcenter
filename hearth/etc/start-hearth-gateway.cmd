@echo off
rem HEARTH gateway login-start wrapper (same posture as OmenOllamaTracingProxy).
rem Registered as scheduled task "HearthGateway" (logon trigger, user OMEN\derek).
cd /d C:\work\commandcenter
set HEARTH_SCOPE=C:\work\commandcenter
echo [%date% %time%] HearthGateway task starting >> hearth\var\gateway-task.log
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.kernel.gateway --callers hearth\var\callers.json --providers hearth.toolsurface.fs,hearth.toolsurface.git,hearth.toolsurface.testing,hearth.toolsurface.knowledge,hearth.toolsurface.summon,hearth.toolsurface.inference >> hearth\var\gateway-task.log 2>&1
echo [%date% %time%] HearthGateway exited with %errorlevel% >> hearth\var\gateway-task.log
