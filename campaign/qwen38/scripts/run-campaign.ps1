param(
    [switch]$ConfirmOutage,
    [switch]$SkipFlash,
    [switch]$AcknowledgeThermalQuarantine
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')
if (-not $ConfirmOutage) { throw 'The full campaign requires -ConfirmOutage' }

Invoke-Q38Python verify-lock
& (Join-Path $PSScriptRoot 'prepare-frontier.ps1')
$failure = $null
try {
    & (Join-Path $PSScriptRoot '00-enter-maintenance.ps1') -ConfirmOutage
    & (Join-Path $PSScriptRoot '01-baseline.ps1')
    & (Join-Path $PSScriptRoot '02-qwen27-compatibility.ps1')
    & (Join-Path $PSScriptRoot '03-qwen27-performance.ps1') -AcknowledgeThermalQuarantine:$AcknowledgeThermalQuarantine
    & (Join-Path $PSScriptRoot '04-qwen27-quality.ps1')
    if (-not $SkipFlash) { & (Join-Path $PSScriptRoot '05-flash-quarantine.ps1') }
    & (Join-Path $PSScriptRoot '06-final-soak.ps1')
} catch {
    $failure = $_
    Write-Warning "Campaign stopped: $($_.Exception.Message)"
} finally {
    & (Join-Path $PSScriptRoot '99-restore.ps1')
}
if ($failure) { throw $failure }
& (Join-Path $PSScriptRoot '07-score.ps1') -SkipFlash:$SkipFlash
