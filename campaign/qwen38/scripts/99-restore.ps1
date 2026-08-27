$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'maintenance.ps1') -Action Restore

