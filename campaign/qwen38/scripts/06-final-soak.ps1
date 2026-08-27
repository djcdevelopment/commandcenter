param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '06-final-soak'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$root = Get-Q38RuntimeRoot
$summariesPath = Join-Path $root 'results\summaries\all-configurations.json'
$winnerPath = Join-Path $root 'results\summaries\winning-topology.json'
if (-not (Test-Path -LiteralPath $summariesPath)) { throw 'Performance summaries are missing; run stage 03 first' }
& python $script:Q38Python choose-topology --summaries $summariesPath --output $winnerPath
if ($LASTEXITCODE -ne 0) { throw 'No production-shaped topology qualified for soak' }
$winner = Get-Content -LiteralPath $winnerPath -Raw -Encoding UTF8 | ConvertFrom-Json
$receipt = New-Q38Receipt $stage
try {
    $start = @{ Action = 'Start'; Topology = [string]$winner.topology; Vision = $true }
    if ([bool]$winner.mtp_enabled) { $start.Mtp = $true }
    & (Join-Path $PSScriptRoot 'server-control.ps1') @start
    $soak = @{ RunId = 'final-soak'; Candidate = 'qwen38-27b'; Topology = [string]$winner.topology; Model = 'qwen38-27b'; Kind = 'Load'; Concurrency = 16; PromptTokens = 512; MaxTokens = 200; DurationSeconds = 3600 }
    if ([bool]$winner.mtp_enabled) { $soak.Mtp = $true }
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') @soak
    $deep = @{ RunId = 'final-deep-context'; Candidate = 'qwen38-27b'; Topology = [string]$winner.topology; Model = 'qwen38-27b'; Kind = 'Load'; Concurrency = 2; PromptTokens = 60000; MaxTokens = 32; DurationSeconds = 900; Retrieval = $true }
    if ([bool]$winner.mtp_enabled) { $deep.Mtp = $true }
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') @deep
    $soakRows = @()
    $soakRows += @(Read-Q38LatestJsonl -Path (Join-Path $root 'results\requests\final-soak.jsonl'))
    $soakRows += @(Read-Q38LatestJsonl -Path (Join-Path $root 'results\requests\final-deep-context.jsonl'))
    $soakRows = @($soakRows | Where-Object {
        [string]$_.topology -eq [string]$winner.topology -and [bool]$_.mtp_enabled -eq [bool]$winner.mtp_enabled
    })
    if (-not $soakRows.Count) { throw 'Final soak files do not contain rows for the selected topology/MTP state' }
    $bad = @($soakRows | Where-Object { -not $_.valid })
    if ($bad.Count -gt 0) { throw "Final soak contains $($bad.Count) invalid/corrupt requests" }
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail "60-minute 16-client soak plus 15-minute deep-context leg passed on $($winner.topology), MTP=$($winner.mtp_enabled)."
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
