@echo off
rem restart-arc.cmd -- bounce the omen-arc rung. Invoked by the ArcServeRestart
rem scheduled task (no trigger, RunLevel Highest, S4U) so a medium-integrity
rem caller can restart the high-integrity serve UAC-free:
rem     schtasks /Run /TN ArcServeRestart
rem (The gateway's own restart task uses the same pattern -- ADR-0015/0024/0032.)
rem
rem 2026-09-03 (ADR-0045 / P13): the rung now runs under llama-swap. The tree to tear down is
rem llama-swap.exe WITH its children (taskkill /T), and -- until the cutover has happened, or if a
rem direct server was ever started by hand -- any bare llama-server.exe as well. The wait below
rem requires BOTH images to be gone before ArcServeBoot is started again.
set "ARC_MAINTENANCE_STOP=C:\work\commandcenter\hearth\var\arc-maintenance.stop"
schtasks /End /TN ArcServeBoot >nul 2>&1
taskkill /IM llama-swap.exe /T /F >nul 2>&1
taskkill /IM llama-server.exe /F >nul 2>&1
rem Campaign maintenance leaves this sentinel in place until its guarded
rem restore path is ready to bring production back. The elevated restart task
rem therefore doubles as a UAC-free stop-only control for a medium caller.
if exist "%ARC_MAINTENANCE_STOP%" exit /b 0

rem WAIT FOR THE OLD PROCESSES TO ACTUALLY BE GONE. This used to be `timeout /t 3`,
rem and 3 seconds is not enough: the server holds ~30 GB loaded with --no-mmap -dio,
rem and teardown routinely outlives the sleep. ArcServeBoot then launched while the
rem old instance still held port 8082, serve-arc.cmd exited 1 WITHOUT truncating its
rem log, and production stayed down while every scheduled task reported success --
rem observed 2026-08-29 19:44 (LastTaskResult=1, log untouched, no process). The same
rem race is the most likely explanation for the ~3-minute outage earlier that day when
rem ArcServeRestart was invoked three times in a row.
rem Bounded at ~120 s; if a process will not die we REFUSE to start a second
rem instance rather than racing it, because two servers on one port is a worse
rem failure than a rung that is honestly down.
set /a _arcwait=0
:arc_waitgone
tasklist /FI "IMAGENAME eq llama-server.exe" 2>nul | find /I "llama-server.exe" >nul
if not errorlevel 1 goto arc_stillthere
tasklist /FI "IMAGENAME eq llama-swap.exe" 2>nul | find /I "llama-swap.exe" >nul
if errorlevel 1 goto arc_gone
:arc_stillthere
set /a _arcwait+=1
if %_arcwait% GEQ 60 goto arc_stuck
timeout /t 2 /nobreak >nul
goto arc_waitgone

:arc_gone
timeout /t 2 /nobreak >nul
schtasks /Run /TN ArcServeBoot
exit /b 0

:arc_stuck
echo restart-arc: llama-swap/llama-server still present after ~120s - refusing to start a second instance 1>&2
exit /b 1
