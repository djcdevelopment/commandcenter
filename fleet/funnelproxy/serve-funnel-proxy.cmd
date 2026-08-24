@echo off
rem serve-funnel-proxy.cmd — the Funnel-facing Caddy hop for HEARTH (amends ADR-0025).
rem
rem WHY THIS FILE EXISTS AT ALL: this proxy ran from 2026-07-21 as a hand-started
rem process with no boot task. The 2026-08-20 OMEN rebuild wiped it, and nobody
rem noticed for four days because Tailscale Funnel stayed configured and kept
rem answering -- with a 502. A public hostname served errors while the fleet map
rem still claimed the lane was live. Same failure class as every other find this
rem week: the thing reported success (Funnel "on") while doing nothing.
rem
rem Run by the HearthFunnelProxyBoot scheduled task (S4U, boot trigger,
rem ExecutionTimeLimit PT0S, RestartCount 3 @ PT1M — the ADR-0032 hardening
rem pattern used by ArcServeBoot and HearthGatewayBoot).
rem
rem TOPOLOGY:
rem   internet -> tailscale funnel (omen.tail8e749c.ts.net:443)
rem            -> THIS proxy (127.0.0.1:8711)
rem            -> HEARTH gateway (127.0.0.1:8710, loopback-bound)
rem
rem The proxy exists for two reasons, both structural:
rem   1. The gateway binds loopback only and refuses non-loopback Host values
rem      (ADR-0022 DNS-rebinding guard). Caddy rewrites Host so a Funnel request
rem      is not rejected with 421.
rem   2. Path narrowing: only /mcp is forwarded; everything else 404s without
rem      ever reaching the gateway process.
rem
rem NO SECRET IS STAMPED HERE (changed 2026-08-24). Callers present their own
rem X-Hearth-Key and authenticate per-request. Do not re-add a stamp: that is
rem what made the Funnel URL itself a credential and leaked it into the logs.

set CADDY=C:\Users\derek\AppData\Local\Microsoft\WinGet\Packages\CaddyServer.Caddy_Microsoft.Winget.Source_8wekyb3d8bbwe\caddy.exe

rem Caddy writes its own rolling, header-redacted logs (configured in the
rem Caddyfile). Stdout/stderr here only catches startup failures.
"%CADDY%" run ^
  --config C:\work\commandcenter\hearth\etc\caddy\Caddyfile ^
  --adapter caddyfile ^
  > "C:\work\commandcenter\hearth\var\funnel-proxy-task.log" 2>&1
