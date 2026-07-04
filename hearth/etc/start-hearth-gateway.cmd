@echo off
rem HEARTH gateway login-start wrapper (same posture as OmenOllamaTracingProxy).
rem Registered as scheduled task "HearthGateway" (logon trigger, user OMEN\derek).
cd /d C:\work\commandcenter
set HEARTH_SCOPE=C:\work\commandcenter
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\pythonw.exe -m hearth.kernel.gateway --callers hearth\var\callers.json --providers hearth.toolsurface.fs,hearth.toolsurface.git,hearth.toolsurface.testing,hearth.toolsurface.knowledge,hearth.toolsurface.summon,hearth.toolsurface.inference
