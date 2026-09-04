@echo off
rem with-gateway-env.cmd -- run a command under the HEARTH launcher's environment.
rem
rem In-process callers of hearth.toolsurface.inference (the doc/ADR bench, experiment harnesses,
rem one-off pins under a DispatchIdentity) need the rungs' auth env vars, which live ONLY in the
rem gitignored hearth\var\gateway.cmd (one `set NAME=...` per line). The gateway launcher CALLs that
rem file; an interactive shell never does, so every pinned call fails with "no auth token for <rung>"
rem (2026-09-03 lesson). This wrapper CALLs it silently and runs whatever follows.
rem
rem Usage (from PowerShell; Git Bash mangles `cmd /c "call ... && ..."` chains):
rem   & cmd /c "C:\work\commandcenter\hearth\etc\with-gateway-env.cmd fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.experiments.run_doc_adr_bench --smoke --arms omen-swap:phi4-vk1"
rem
rem Never echo the token file, never `set` without arguments here, never redirect its output anywhere
rem but nul.
cd /d "%~dp0..\.."
if exist hearth\var\gateway.cmd call hearth\var\gateway.cmd >nul 2>&1
if "%~1"=="" (
  echo with-gateway-env: nothing to run. Usage: with-gateway-env.cmd ^<command^> [args...] 1>&2
  exit /b 2
)
%*
exit /b %ERRORLEVEL%
