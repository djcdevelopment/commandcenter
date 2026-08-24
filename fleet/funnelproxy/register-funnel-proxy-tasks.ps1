# register-funnel-proxy-tasks.ps1 — one-time, RUN AS ADMINISTRATOR.
#
# Registers the two scheduled tasks that keep the Funnel-facing Caddy hop alive,
# mirroring the ArcServeBoot / ArcServeRestart pattern exactly (ADR-0032):
#
#   HearthFunnelProxyBoot     boot trigger, S4U, RunLevel Highest,
#                             ExecutionTimeLimit PT0S, RestartCount 3 @ PT1M
#   HearthFunnelProxyRestart  no trigger — lets a MEDIUM-integrity caller bounce
#                             the high-integrity proxy UAC-free:
#                                 schtasks /Run /TN HearthFunnelProxyRestart
#
# WHY: this proxy ran from 2026-07-21 as a hand-started process with no boot
# task. The 2026-08-20 OMEN rebuild wiped it and nobody noticed for four days,
# because Tailscale Funnel stayed configured and kept answering — with a 502.
# A boot task is the difference between "survives a rebuild" and "silently gone".

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Must run elevated. Right-click PowerShell -> Run as Administrator, then re-run this script."
}

$serve   = 'C:\work\commandcenter\fleet\funnelproxy\serve-funnel-proxy.cmd'
$restart = 'C:\work\commandcenter\fleet\funnelproxy\restart-funnel-proxy.cmd'
foreach ($p in @($serve, $restart)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

# --- boot task -------------------------------------------------------------
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName 'HearthFunnelProxyBoot' -Force `
    -Trigger   (New-ScheduledTaskTrigger -AtStartup) `
    -Action    (New-ScheduledTaskAction  -Execute $serve) `
    -Principal $principal -Settings $settings `
    -Description 'Caddy reverse proxy fronting the HEARTH gateway for Tailscale Funnel (amends ADR-0025).' | Out-Null
Write-Host 'registered: HearthFunnelProxyBoot'

# --- restart task (no trigger; run on demand, UAC-free) --------------------
Register-ScheduledTask -TaskName 'HearthFunnelProxyRestart' -Force `
    -Action    (New-ScheduledTaskAction -Execute $restart) `
    -Principal $principal `
    -Settings  (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)) `
    -Description 'Bounce the Funnel-facing Caddy proxy without a UAC prompt.' | Out-Null
Write-Host 'registered: HearthFunnelProxyRestart'

Write-Host ''
Write-Host 'Verify with:'
Write-Host '  schtasks /Run /TN HearthFunnelProxyRestart'
Write-Host '  curl.exe -s -o NUL -w "%{http_code}\n" https://omen.tail8e749c.ts.net/mcp   # expect 406'
