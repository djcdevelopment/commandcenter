param([switch]$ConfirmOutage)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'maintenance.ps1') -Action Enter -ConfirmOutage:$ConfirmOutage
try {
    . (Join-Path $PSScriptRoot 'lib.ps1')
    Wait-Q38ThermalHeadroom | Out-Null
} catch {
    & (Join-Path $PSScriptRoot 'maintenance.ps1') -Action Restore
    throw
}
