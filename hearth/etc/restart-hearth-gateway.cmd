@echo off
rem On-demand elevated bounce of the HEARTH gateway.
rem Registered as scheduled task "HearthGatewayRestart" (RL HIGHEST, S4U, no trigger)
rem so a medium-integrity caller can restart the high-integrity gateway without a
rem UAC prompt:   schtasks /Run /TN HearthGatewayRestart
rem Needed because HearthGatewayBoot runs RL HIGHEST (checkpoint_vm needs Hyper-V
rem admin) and `schtasks /End` kills only the cmd wrapper, orphaning the python.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8710 ^| findstr LISTENING') do taskkill /PID %%p /T /F >nul 2>&1
timeout /t 2 /nobreak >nul
schtasks /End /TN HearthGatewayBoot >nul 2>&1
schtasks /Run /TN HearthGatewayBoot
