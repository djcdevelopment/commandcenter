param(
    [switch]$Force,
    [switch]$SkipFlash
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '07-score'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
try {
    $maintenancePath = Join-Path $root 'state\maintenance.json'
    if (-not (Test-Path -LiteralPath $maintenancePath)) { throw 'Maintenance/restore receipt is missing' }
    $maintenance = Get-Content -LiteralPath $maintenancePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$maintenance.status -ne 'restored' -or -not [bool]$maintenance.real_completion_proved) {
        throw 'Refusing cloud judging/scoring until ArcServeBoot has been restored and proved'
    }
    $flashPath = Join-Path $root 'results\quarantine\flash-feasibility.json'
    if (-not $SkipFlash -and (Test-Path -LiteralPath $flashPath)) {
        $flash = Get-Content -LiteralPath $flashPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $flashPacket = [string](Get-Q38Property -Object $flash -Name 'packet' -Default '')
        if ($flashPacket -and ([string]$flash.status -eq 'awaiting-frontier-judgment' -or $Force)) {
            if (-not (Test-Path -LiteralPath $flashPacket)) { throw "Flash judge packet is missing: $flashPacket" }
            $variantLabel = [string]$flash.variant_label
            $candidateId = [string]$flash.candidate
            $flashJudgments = Join-Path $root ("results\summaries\judgments-flash-{0}-vs-gemini.jsonl" -f $variantLabel)
            & python $script:Q38Python gemini-judge --packet $flashPacket --output $flashJudgments --concurrency 2
            if ($LASTEXITCODE -ne 0) { throw 'Flash specialist frontier judging failed' }
            $judgmentRows = @(Read-Q38LatestJsonl -Path $flashJudgments)
            $consistent = @($judgmentRows | Group-Object task_id, seed | ForEach-Object {
                $validRows = @($_.Group | Where-Object { $_.valid })
                $mapped = @($validRows.mapped_winner | Sort-Object -Unique)
                $orders = @($validRows.order | Sort-Object -Unique)
                if ($validRows.Count -eq 2 -and $orders.Count -eq 2 -and $mapped.Count -eq 1) { $validRows[0] }
            })
            $wins = @($consistent | Where-Object { $_.mapped_winner -eq $candidateId }).Count
            $winRate = $wins / [Math]::Max(1, $consistent.Count)
            $observedGroups = @($judgmentRows | Group-Object task_id, seed).Count
            $expectedGroups = [int]$flash.expected_comparison_groups
            $coverage = $consistent.Count / [Math]::Max(1, $expectedGroups)
            $passed = $winRate -ge [double]$config.promotion.flash_specialist_win_rate_min -and `
                $coverage -ge [double]$config.promotion.blind_judgment_coverage_min
            $flashResult = [ordered]@{
                status = if ($passed) { 'provisional-specialist-candidate' } else { 'quarantined' }
                reason = if ($passed) { 'frontier specialist judgment gate passed' } else { 'frontier specialist judgment gate failed' }
                candidate = $candidateId
                variant_label = $variantLabel
                best_gpu_layers = $flash.best_gpu_layers
                jobs_rate_ratio = $flash.jobs_rate_ratio
                p95_latency_ratio = $flash.p95_latency_ratio
                frontier_win_rate = $winRate
                judgment_coverage = $coverage
                consistent_comparisons = $consistent.Count
                observed_comparison_groups = $observedGroups
                expected_comparison_groups = $expectedGroups
                packet = $flashPacket
                judgments = $flashJudgments
                placements = $flash.placements
            }
            Write-Q38JsonAtomic -Path $flashPath -Value $flashResult
        }
    }
    $candidatePacket = Join-Path $root 'results\summaries\judge-qwen27-vs-baseline.jsonl'
    $frontierPacket = Join-Path $root 'results\summaries\judge-qwen27-vs-gemini.jsonl'
    $judgments = Join-Path $root 'results\summaries\judgments-qwen27-vs-baseline.jsonl'
    $frontierJudgments = Join-Path $root 'results\summaries\judgments-qwen27-vs-gemini.jsonl'
    foreach ($packet in @($candidatePacket, $frontierPacket)) {
        if (-not (Test-Path -LiteralPath $packet)) { throw "Required judge packet is missing: $packet" }
    }
    & python $script:Q38Python gemini-judge --packet $candidatePacket --output $judgments --concurrency 2
    if ($LASTEXITCODE -ne 0) { throw 'Gemini blind judging of Qwen3.8 versus the baseline failed' }
    & python $script:Q38Python gemini-judge --packet $frontierPacket --output $frontierJudgments --concurrency 2
    if ($LASTEXITCODE -ne 0) { throw 'Gemini judging of Qwen3.8 versus the frontier reference failed' }
    & python $script:Q38Python summarize
    if ($LASTEXITCODE -ne 0) { throw 'Summary derivation failed' }
    $summaries = Join-Path $root 'results\summaries\all-configurations.json'
    $winner = Join-Path $root 'results\summaries\winning-topology.json'
    $scorecard = Join-Path $root 'results\summaries\promotion-scorecard.json'
    foreach ($path in @($summaries, $winner, $judgments, $frontierJudgments)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Required scoring input is missing: $path" }
    }
    & python $script:Q38Python scorecard --summaries $summaries --winner $winner --judgments $judgments `
        --frontier-judgments $frontierJudgments --output $scorecard
    if ($LASTEXITCODE -ne 0) { throw 'Scorecard compilation failed' }
    $verdict = Join-Path $root 'results\promotion-verdict.json'
    & python $script:Q38Python verdict --scorecard $scorecard --output $verdict
    $verdictExit = $LASTEXITCODE
    if ($verdictExit -notin @(0, 2)) { throw "Promotion verdict failed with exit $verdictExit" }
    $decision = (Get-Content -LiteralPath $verdict -Raw -Encoding UTF8 | ConvertFrom-Json).decision
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail "Deterministic verdict: $decision. A pass authorizes pin-only canary, never direct default promotion."
    # A negative promotion verdict is a valid campaign outcome, not a script
    # failure. Do not leak Python's intentional exit code 2 to the wrapper.
    $global:LASTEXITCODE = 0
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
}
