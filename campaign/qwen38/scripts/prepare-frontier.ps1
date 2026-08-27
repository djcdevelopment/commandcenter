param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = 'frontier-reference'
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
Invoke-Q38Python init
$referencePath = Join-Path $root 'results\requests\gemini-pro-assay.jsonl'
$currentReferencePath = Join-Path $root 'results\summaries\gemini-pro-assay-current.jsonl'
$taskSetRevision = (Get-FileHash -LiteralPath (Join-Path $script:Q38SourceRoot 'assay\tasks.json') -Algorithm SHA256).Hash.ToLowerInvariant()
$expected = ([int]$config.quality.families.Count * [int]$config.quality.tasks_per_family) + `
    ([int]$config.quality.repeat_task_ids.Count * ([int]$config.quality.repeat_seeds.Count - 1))

function Write-CurrentReference($Rows) {
    @($Rows | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 20 }) |
        Set-Content -LiteralPath $currentReferencePath -Encoding UTF8
}

if ((Test-Q38ReceiptPassed $stage) -and -not $Force -and (Test-Path -LiteralPath $referencePath)) {
    $currentRows = @(Read-Q38LatestJsonl -Path $referencePath | Where-Object {
        [string]$_.model -eq [string]$config.frontier_reference.model -and `
        [string](Get-Q38Property -Object $_ -Name 'artifact_revision' -Default '') -eq $taskSetRevision -and `
        [int](Get-Q38Property -Object $_ -Name 'requested_max_tokens' -Default 0) -eq [int]$config.frontier_reference.max_output_tokens
    })
    if ($currentRows.Count -eq $expected -and @($currentRows | Where-Object { -not $_.success }).Count -eq 0) {
        Write-CurrentReference -Rows $currentRows
        Write-Host "$stage already passed for the current model/task/budget contract; skipping"
        exit 0
    }
}
$receipt = New-Q38Receipt $stage
try {
    Invoke-Q38Python frontier-preflight
    & python $script:Q38Python gemini-assay --run-id gemini-pro-assay --concurrency 2 --include-repeats `
        --max-tokens ([int]$config.frontier_reference.max_output_tokens)
    if ($LASTEXITCODE -ne 0) { throw 'Gemini 3.1 Pro reference assay failed' }
    $rows = @(Read-Q38LatestJsonl -Path $referencePath | Where-Object {
        [string]$_.model -eq [string]$config.frontier_reference.model -and `
        [string](Get-Q38Property -Object $_ -Name 'artifact_revision' -Default '') -eq $taskSetRevision -and `
        [int](Get-Q38Property -Object $_ -Name 'requested_max_tokens' -Default 0) -eq [int]$config.frontier_reference.max_output_tokens
    })
    if ($rows.Count -ne $expected) { throw "Gemini reference has $($rows.Count) task/seed rows; expected $expected" }
    $transportFailures = @($rows | Where-Object { -not $_.success })
    if ($transportFailures.Count) {
        throw "Gemini reference has $($transportFailures.Count) transport failures; rerun this stage to retry them before maintenance"
    }
    Write-CurrentReference -Rows $rows
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail "Pinned Gemini reference captured for all $expected task/seed pairs before maintenance."
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
}
