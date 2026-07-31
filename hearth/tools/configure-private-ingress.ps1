[CmdletBinding()]
param(
    [string]$HttpsPort = "8443",
    [string]$GatewayPort = "8710",
    [string]$GatewayEnvironmentFile = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $GatewayEnvironmentFile) {
    $GatewayEnvironmentFile = Join-Path $repoRoot "hearth\var\gateway.cmd"
}

$tailscale = Get-Command tailscale -ErrorAction Stop
$status = & $tailscale.Source status --json | ConvertFrom-Json
$dnsName = [string]$status.Self.DNSName
$dnsName = $dnsName.TrimEnd(".")
if (-not $dnsName) {
    throw "Tailscale did not report a MagicDNS hostname for this machine."
}
if ($HttpsPort -notin @("443", "8443", "10000")) {
    throw "Tailscale Serve HTTPS supports ports 443, 8443, and 10000."
}

$environmentDirectory = Split-Path -Parent $GatewayEnvironmentFile
New-Item -ItemType Directory -Force -Path $environmentDirectory | Out-Null
$setting = "set HEARTH_TRUSTED_PROXY_HOSTS=$dnsName"
$lines = @()
if (Test-Path -LiteralPath $GatewayEnvironmentFile) {
    $lines = @(Get-Content -LiteralPath $GatewayEnvironmentFile)
}
$lines = @($lines | Where-Object { $_ -notmatch "^\s*set\s+HEARTH_TRUSTED_PROXY_HOSTS=" })
$lines += $setting
Set-Content -LiteralPath $GatewayEnvironmentFile -Value $lines -Encoding ascii

& $tailscale.Source serve --bg --https=$HttpsPort "http://127.0.0.1:$GatewayPort"
if ($LASTEXITCODE -ne 0) {
    throw "tailscale serve failed with exit code $LASTEXITCODE"
}

Write-Host "Private HEARTH ingress configured."
Write-Host "  URL: https://$dnsName`:$HttpsPort/mcp"
Write-Host "  Scope: tailnet only (Tailscale Serve; Funnel was not enabled)"
Write-Host "  Auth: X-Hearth-Key remains required and is not stamped by the proxy"
Write-Host "  Next: restart the HEARTH gateway so it reads $GatewayEnvironmentFile"
