@echo off
rem On-demand elevated bounce of the HEARTH gateway.
rem Registered as scheduled task "HearthGatewayRestart" (RL HIGHEST, S4U, no trigger)
rem so a medium-integrity caller can restart the high-integrity gateway without a
rem UAC prompt:   schtasks /Run /TN HearthGatewayRestart
rem Needed because HearthGatewayBoot runs RL HIGHEST (checkpoint_vm needs Hyper-V
rem admin) and `schtasks /End` kills only the cmd wrapper, orphaning the python.
rem Match the SERVER side by local address, not by LISTENING state: an asyncio
rem accept loop can die (WinError 64 on a client abort) leaving the process alive
rem and still bound to :8710 with no listener. A LISTENING-only filter finds
rem nothing, kills nothing, exits 0 -- and the Boot that follows fails to bind
rem against the surviving zombie. Token 2 is the local address, so client rows
rem (local = ephemeral port) never match. Seen live 2026-07-16.
for /f "tokens=2,5" %%a in ('netstat -ano ^| findstr ":8710"') do (
  if "%%a"=="127.0.0.1:8710" taskkill /PID %%b /T /F >nul 2>&1
)
timeout /t 2 /nobreak >nul
schtasks /End /TN HearthGatewayBoot >nul 2>&1
schtasks /Run /TN HearthGatewayBoot
