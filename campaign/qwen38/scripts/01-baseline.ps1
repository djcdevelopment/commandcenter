param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '01-baseline'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$config = Get-Q38Config
$receipt = New-Q38Receipt $stage
try {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'baseline-production'
    foreach ($promptTokens in @(512, 8192, 32768)) {
        $concurrencies = if ($promptTokens -eq 512) { @($config.matrix.concurrency) } else { @($config.matrix.concurrency | Where-Object { [int]$_ -le 16 }) }
        foreach ($concurrency in $concurrencies) {
            $runId = "baseline-p${promptTokens}-c${concurrency}"
            & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $runId -Candidate 'qwen3-30b-baseline' `
                -Topology 'baseline-production' -Model 'qwen3-30b-a3b' -Kind Load `
                -Concurrency $concurrency -PromptTokens $promptTokens -MaxTokens 200 -RequestsPerClient ([int]$config.generation.measured_rounds)
        }
    }
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'baseline-assay-deterministic' `
        -Candidate 'qwen3-30b-baseline' -Topology 'baseline-production' -Model 'qwen3-30b-a3b' `
        -Kind Assay -Concurrency 2 -MaxTokens 2048
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail 'Fresh production-shape performance and 72-task deterministic assay captured.'
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
