param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '05-flash-quarantine'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
$feasibilityPath = Join-Path $root 'results\quarantine\flash-feasibility.json'
$probeTasks = @('extract-02', 'class-03', 'reasoning-04', 'tool-02', 'doc-03', 'chart-02', 'screen-04', 'screen-08')
$placements = @()

$variants = @()
$iq4 = Get-Q38Artifact -Id 'qwen38-flash-next-iq4'
$q2 = Get-Q38Artifact -Id 'qwen38-flash-next-q2'
$iq4Present = Test-Path -LiteralPath ([string]$iq4.path)
$q2Present = Test-Path -LiteralPath ([string]$q2.path)
if ($iq4Present) {
    $variants += [pscustomobject]@{ id = 'qwen38-flash-next-iq4'; label = 'iq4'; fallback = $false }
}
if ($iq4Present -and $q2Present) {
    $variants += [pscustomobject]@{ id = 'qwen38-flash-next-q2'; label = 'q2'; fallback = $true }
}
if (-not $iq4Present -and $q2Present) {
    Write-Q38JsonAtomic -Path $feasibilityPath -Value ([ordered]@{ status = 'quarantined'; reason = 'Q2 fallback is present without the required IQ4 primary attempt' })
    Complete-Q38Receipt -Receipt $receipt -Status quarantined -Detail 'Q2 was not run because the IQ4 primary artifact is absent.'
    Write-Warning 'Flash Q2 is fallback-only; acquire IQ4 before running the quarantined lane.'
    exit 0
}
if (-not $variants.Count) {
    Complete-Q38Receipt -Receipt $receipt -Status skipped -Detail 'Neither Flash IQ4 nor predetermined Q2 fallback artifact is present.'
    Write-Warning 'Flash-Next artifacts are absent; quarantined specialist lane skipped.'
    exit 0
}

function Start-FlashVariant($Variant, [int]$Layers) {
    $start = @{ Action = 'Start'; Topology = 'flash-feasibility'; GpuLayers = $Layers; Vision = $true }
    if ([bool]$Variant.fallback) { $start.QuantFallback = $true }
    & (Join-Path $PSScriptRoot 'server-control.ps1') @start
}

try {
    $selected = $null
    foreach ($variant in $variants) {
        $bestLayers = $null
        foreach ($layers in @($config.matrix.flash_gpu_layer_ladder)) {
            $runId = "flash-$($variant.label)-ngl$layers-compat"
            try {
                Start-FlashVariant -Variant $variant -Layers ([int]$layers)
                & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $runId -Candidate ([string]$variant.id) `
                    -Topology 'flash-feasibility' -Model 'qwen38-flash-next' -Kind Assay -Concurrency 1 `
                    -MaxTokens 2048 -TaskIds $probeTasks
                $rowsPath = Join-Path $root ("results\requests\{0}.jsonl" -f $runId)
                $rows = @(Read-Q38LatestJsonl -Path $rowsPath)
                $valid = @($rows | Where-Object { $_.valid }).Count
                $integrityFailures = @($rows | Where-Object { -not $_.success -or $_.failure_class -in @('empty_output', 'replacement_character', 'invalid_special_token', 'repetition_loop') }).Count
                $status = if ($valid -eq $rows.Count -and $integrityFailures -eq 0) { 'passed' } else { 'failed' }
                $placements += [pscustomobject]@{ variant = $variant.label; gpu_layers = [int]$layers; valid = $valid; total = $rows.Count; integrity_failures = $integrityFailures; status = $status }
                if ($status -ne 'passed') { continue }
                $bestLayers = [int]$layers
            } catch {
                # A rung that cannot fit is what the ladder is here to find out.
                Assert-Q38PlacementProbeFailure -RunId $runId -Message $_.Exception.Message
                $placements += [pscustomobject]@{ variant = $variant.label; gpu_layers = [int]$layers; valid = 0; total = $probeTasks.Count; integrity_failures = 1; status = 'load_or_runtime_failure'; reason = $_.Exception.Message }
                Write-Warning "Flash rung ngl=$layers did not fit: $($_.Exception.Message)"
                continue
            } finally {
                & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
            }
        }
        if ($null -eq $bestLayers) { continue }

        $performanceRun = "flash-$($variant.label)-feasibility-performance"
        $performanceSucceeded = $false
        try {
            Start-FlashVariant -Variant $variant -Layers $bestLayers
            & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $performanceRun -Candidate ([string]$variant.id) `
                -Topology 'flash-feasibility' -Model 'qwen38-flash-next' -Kind Load `
                -Concurrency 1 -PromptTokens 512 -MaxTokens 200 -RequestsPerClient 3
            $performanceSucceeded = $true
        } catch {
            Assert-Q38FailureQuarantinable -RunId $performanceRun -Message $_.Exception.Message
            $placements += [pscustomobject]@{ variant = $variant.label; gpu_layers = $bestLayers; status = 'performance_runtime_failure'; reason = $_.Exception.Message }
        } finally {
            & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
        }
        if (-not $performanceSucceeded) { continue }
        & python $script:Q38Python summarize
        if ($LASTEXITCODE -ne 0) { throw "Unable to summarize Flash $($variant.label) feasibility performance" }
        $summaries = Get-Content -LiteralPath (Join-Path $root 'results\summaries\all-configurations.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $flashSummary = @($summaries | Where-Object { $_.candidate -eq $variant.id -and $_.run_ids -contains $performanceRun }) | Select-Object -First 1
        $baselineSummary = @($summaries | Where-Object { $_.candidate -eq 'qwen3-30b-baseline' -and $_.test_kind -eq 'performance' -and $_.concurrency -eq 1 -and $_.requested_prompt_tokens -eq 512 }) | Select-Object -First 1
        if ($null -eq $flashSummary -or $null -eq $baselineSummary) { throw "Flash $($variant.label) or baseline feasibility summary is missing" }
        $rateRatio = [double]$flashSummary.jobs_per_hour / [Math]::Max(0.000001, [double]$baselineSummary.jobs_per_hour)
        $p95Ratio = [double]$flashSummary.latency_p95_s / [Math]::Max(0.000001, [double]$baselineSummary.latency_p95_s)
        $placements += [pscustomobject]@{ variant = $variant.label; gpu_layers = $bestLayers; status = 'performance_gate'; jobs_rate_ratio = $rateRatio; p95_latency_ratio = $p95Ratio }
        if ($rateRatio -ge [double]$config.promotion.flash_feasibility_jobs_rate_ratio_min -and $p95Ratio -le [double]$config.promotion.p95_latency_ratio_max) {
            $selected = [pscustomobject]@{ variant = $variant; gpu_layers = $bestLayers; jobs_rate_ratio = $rateRatio; p95_latency_ratio = $p95Ratio }
            break
        }
        # Q2 is reached only after IQ4 misses residency, correctness, goodput, or latency.
    }

    if ($null -eq $selected) {
        Write-Q38JsonAtomic -Path $feasibilityPath -Value ([ordered]@{ status = 'quarantined'; reason = 'no variant cleared compatibility and performance'; placements = $placements })
        Complete-Q38Receipt -Receipt $receipt -Status quarantined -Detail 'Neither IQ4 nor the allowed Q2 fallback cleared Flash feasibility.'
        exit 0
    }

    Start-FlashVariant -Variant $selected.variant -Layers ([int]$selected.gpu_layers)
    $candidateId = [string]$selected.variant.id
    $variantLabel = [string]$selected.variant.label
    $fullAssayRun = "flash-$variantLabel-full-assay"
    $miniSoakRun = "flash-$variantLabel-mini-soak"
    try {
        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $fullAssayRun `
            -Candidate $candidateId -Topology 'flash-feasibility' -Model 'qwen38-flash-next' `
            -Kind Assay -Concurrency 1 -MaxTokens 4096
        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $miniSoakRun `
            -Candidate $candidateId -Topology 'flash-feasibility' -Model 'qwen38-flash-next' `
            -Kind Load -Concurrency 1 -PromptTokens 8192 -MaxTokens 32 -DurationSeconds 900 -Retrieval
    } catch {
        Assert-Q38FailureQuarantinable -RunId $fullAssayRun -Message $_.Exception.Message
        Assert-Q38FailureQuarantinable -RunId $miniSoakRun -Message $_.Exception.Message
        throw
    }
    $flashRows = Join-Path $root ("results\requests\{0}.jsonl" -f $fullAssayRun)
    $fullRows = @(Read-Q38LatestJsonl -Path $flashRows)
    $fullIntegrity = @($fullRows | Where-Object { -not $_.success -or $_.failure_class -in @('empty_output', 'replacement_character', 'invalid_special_token', 'repetition_loop') })
    if ($fullIntegrity.Count) { throw "Flash full assay contained $($fullIntegrity.Count) transport/corruption failures" }
    $soakRows = @(Read-Q38LatestJsonl -Path (Join-Path $root ("results\requests\{0}.jsonl" -f $miniSoakRun)))
    $badSoak = @($soakRows | Where-Object { -not $_.valid })
    if ($badSoak.Count) { throw "Flash retrieval soak contained $($badSoak.Count) invalid requests" }

    $geminiRows = Join-Path $root 'results\summaries\gemini-pro-assay-current.jsonl'
    if (-not (Test-Path -LiteralPath $geminiRows)) { throw 'Gemini frontier reference rows are missing from stage 04' }
    $packet = Join-Path $root ("results\summaries\judge-flash-{0}-vs-gemini.jsonl" -f $variantLabel)
    & python $script:Q38Python judge-packet --input $flashRows --input $geminiRows `
        --baseline gemini-3.1-pro-reference --candidate $candidateId `
        --family reasoning_planning --family document_ocr --family chart_diagram --family screenshot_grounded `
        --output $packet
    if ($LASTEXITCODE -ne 0) { throw 'Flash specialist frontier packet generation failed' }
    $expectedComparisonGroups = 4 * [int]$config.quality.tasks_per_family
    $packetRows = @(Get-Content -LiteralPath $packet -Encoding UTF8).Count
    if ($packetRows -ne 2 * $expectedComparisonGroups) {
        throw "Flash specialist packet has $packetRows rows; expected $(2 * $expectedComparisonGroups)"
    }
    Write-Q38JsonAtomic -Path $feasibilityPath -Value ([ordered]@{ status = 'awaiting-frontier-judgment'; candidate = $candidateId; variant_label = $variantLabel; best_gpu_layers = $selected.gpu_layers; jobs_rate_ratio = $selected.jobs_rate_ratio; p95_latency_ratio = $selected.p95_latency_ratio; packet = $packet; expected_comparison_groups = $expectedComparisonGroups; placements = $placements })
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail 'Flash cleared local feasibility and retrieval soak; complete specialist packet is deferred for post-restore cloud judging.'
} catch {
    $fatal = $_.Exception.Message -match '^FATAL SAFETY:'
    Write-Q38JsonAtomic -Path $feasibilityPath -Value ([ordered]@{ status = 'quarantined'; reason = $_.Exception.Message; placements = $placements })
    Complete-Q38Receipt -Receipt $receipt -Status quarantined -Detail $_.Exception.Message
    if ($fatal) { throw }
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
