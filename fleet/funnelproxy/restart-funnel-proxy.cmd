@echo off
rem restart-funnel-proxy.cmd — bounce the Funnel-facing Caddy hop. Invoked by the
rem HearthFunnelProxyRestart scheduled task (no trigger, RunLevel Highest, S4U) so
rem a medium-integrity caller can restart the high-integrity proxy UAC-free:
rem     schtasks /Run /TN HearthFunnelProxyRestart
rem (Same pattern as restart-arc.cmd and the gateway's own restart task —
rem  ADR-0015/0024/0032.)
rem
rem Caddy is stopped by name. Nothing else on this box runs caddy.exe; if that
rem ever changes, narrow this to the PID holding :8711 first.
schtasks /End /TN HearthFunnelProxyBoot >nul 2>&1
taskkill /IM caddy.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
schtasks /Run /TN HearthFunnelProxyBoot
