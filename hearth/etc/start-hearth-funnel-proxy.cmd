@echo off
rem RETIRED 2026-08-24 — do not run this. Kept as a pointer, not deleted, because
rem ADR-0025 and SESSION-RETRO-2026-07-21.md both reference this path.
rem
rem The Funnel-facing Caddy proxy is now started by a scheduled task, so it
rem survives a rebuild. This hand-start script is what let it die silently in the
rem 2026-08-20 rebuild: nothing restarted it, Funnel kept answering the public
rem hostname with a 502, and nobody noticed for four days.
rem
rem   start / stop  ->  fleet\funnelproxy\serve-funnel-proxy.cmd
rem                     (via the HearthFunnelProxyBoot scheduled task)
rem   bounce it     ->  schtasks /Run /TN HearthFunnelProxyRestart
rem   register once ->  fleet\funnelproxy\register-funnel-proxy-tasks.ps1  (elevated)
rem
rem It is also actively harmful now: its `>>` append to caddy-funnel-proxy.log
rem fights Caddy's own rolling, header-REDACTED logger configured in the
rem Caddyfile. Running this would reintroduce an unrotated, unredacted log --
rem which is exactly how the stamped X-Hearth-Key leaked in the first place.

echo.
echo   RETIRED. Use the scheduled task instead:
echo       schtasks /Run /TN HearthFunnelProxyRestart
echo.
echo   If the tasks are not registered yet, run elevated once:
echo       powershell -ExecutionPolicy Bypass -File C:\work\commandcenter\fleet\funnelproxy\register-funnel-proxy-tasks.ps1
echo.
exit /b 1
