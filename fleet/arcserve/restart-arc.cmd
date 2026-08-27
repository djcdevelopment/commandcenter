@echo off
rem restart-arc.cmd — bounce the omen-arc rung. Invoked by the ArcServeRestart
rem scheduled task (no trigger, RunLevel Highest, S4U) so a medium-integrity
rem caller can restart the high-integrity serve UAC-free:
rem     schtasks /Run /TN ArcServeRestart
rem (The gateway's own restart task uses the same pattern — ADR-0015/0024/0032.)
set "ARC_MAINTENANCE_STOP=C:\work\commandcenter\hearth\var\arc-maintenance.stop"
schtasks /End /TN ArcServeBoot >nul 2>&1
taskkill /IM llama-server.exe /F >nul 2>&1
rem Campaign maintenance leaves this sentinel in place until its guarded
rem restore path is ready to bring production back. The elevated restart task
rem therefore doubles as a UAC-free stop-only control for a medium caller.
if exist "%ARC_MAINTENANCE_STOP%" exit /b 0
timeout /t 3 /nobreak >nul
schtasks /Run /TN ArcServeBoot
