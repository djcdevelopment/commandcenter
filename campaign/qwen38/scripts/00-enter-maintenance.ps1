param([switch]$ConfirmOutage)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'maintenance.ps1') -Action Enter -ConfirmOutage:$ConfirmOutage

