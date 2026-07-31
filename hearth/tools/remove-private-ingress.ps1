[CmdletBinding()]
param(
    [string]$HttpsPort = "8443"
)

$ErrorActionPreference = "Stop"
$tailscale = Get-Command tailscale -ErrorAction Stop
& $tailscale.Source serve --https=$HttpsPort off
if ($LASTEXITCODE -ne 0) {
    throw "tailscale serve removal failed with exit code $LASTEXITCODE"
}
Write-Host "Removed the tailnet-only HEARTH Serve listener on HTTPS port $HttpsPort."
