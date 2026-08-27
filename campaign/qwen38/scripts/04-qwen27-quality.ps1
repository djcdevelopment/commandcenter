param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '04-qwen27-quality'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
try {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'baseline-production'
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'baseline-assay-repeats' `
        -Candidate 'qwen3-30b-baseline' -Topology 'baseline-production' -Model 'qwen3-30b-a3b' `
        -Kind Assay -Concurrency 2 -MaxTokens 2048 -RepeatOnly -IncludeRepeats
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop

    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'qwen27-dual-production' -Vision
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-assay-repeats' `
        -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
        -Kind Assay -Concurrency 2 -MaxTokens 2048 -RepeatOnly -IncludeRepeats
    # Side evidence only: the 12 discriminating tasks with thinking left on and room
    # to close the think block. Not an input to any gate or judge packet.
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-assay-think-on' `
        -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
        -Kind Assay -Concurrency 2 -MaxTokens 8192 -RepeatOnly -ThinkOn
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop

    $inputs = @(
        (Join-Path $root 'results\requests\baseline-assay-deterministic.jsonl'),
        (Join-Path $root 'results\requests\baseline-assay-repeats.jsonl'),
        (Join-Path $root 'results\requests\qwen27-compat-mtp-off.jsonl'),
        (Join-Path $root 'results\requests\qwen27-assay-repeats.jsonl'),
        (Join-Path $root 'results\summaries\gemini-pro-assay-current.jsonl')
    )
    $candidatePacket = Join-Path $root 'results\summaries\judge-qwen27-vs-baseline.jsonl'
    $frontierPacket = Join-Path $root 'results\summaries\judge-qwen27-vs-gemini.jsonl'
    $judgeArgs = @('judge-packet')
    foreach ($input in $inputs) { if (Test-Path -LiteralPath $input) { $judgeArgs += @('--input', $input) } }
    & python $script:Q38Python @judgeArgs --baseline qwen3-30b-baseline --candidate qwen38-27b --output $candidatePacket
    if ($LASTEXITCODE -ne 0) { throw 'Baseline judge packet generation failed' }
    $expectedGroups = ([int]$config.quality.families.Count * [int]$config.quality.tasks_per_family) + `
        ([int]$config.quality.repeat_task_ids.Count * ([int]$config.quality.repeat_seeds.Count - 1))
    $expectedPacketRows = 2 * $expectedGroups
    $candidatePacketRows = @(Get-Content -LiteralPath $candidatePacket -Encoding UTF8).Count
    if ($candidatePacketRows -ne $expectedPacketRows) {
        throw "Baseline judge packet has $candidatePacketRows rows; expected $expectedPacketRows"
    }
    $judgeArgs2 = @('judge-packet')
    foreach ($input in $inputs) { if (Test-Path -LiteralPath $input) { $judgeArgs2 += @('--input', $input) } }
    & python $script:Q38Python @judgeArgs2 --baseline gemini-3.1-pro-reference --candidate qwen38-27b --output $frontierPacket
    if ($LASTEXITCODE -ne 0) { throw 'Frontier judge packet generation failed' }
    $frontierPacketRows = @(Get-Content -LiteralPath $frontierPacket -Encoding UTF8).Count
    if ($frontierPacketRows -ne $expectedPacketRows) {
        throw "Frontier judge packet has $frontierPacketRows rows; expected $expectedPacketRows"
    }
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail 'Seed repeats and complete order-balanced baseline/frontier packets produced; cloud judging is deferred until after production restore.'
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
