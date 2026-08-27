param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '02-qwen27-compatibility'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
try {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'qwen27-dual-production' -Vision
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-compat-mtp-off' `
        -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
        -Kind Assay -Concurrency 2 -MaxTokens 2048
    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-compat-context32k-mtp-off' `
        -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
        -Kind Load -Concurrency 1 -PromptTokens 32000 -MaxTokens 32 -RequestsPerClient 1 -Retrieval

    $basePath = Join-Path $root 'results\requests\qwen27-compat-mtp-off.jsonl'
    $base = @(Read-Q38LatestJsonl -Path $basePath)
    $integrityFailures = @($base | Where-Object { -not $_.success -or $_.failure_class -in @('empty_output', 'replacement_character', 'invalid_special_token', 'repetition_loop') })
    $protocol = @($base | Where-Object { $_.task_family -in @('extraction', 'tool_execution', 'document_ocr', 'chart_diagram', 'screenshot_grounded') })
    $protocolRate = @($protocol | Where-Object { $_.valid }).Count / [Math]::Max(1, $protocol.Count)
    if ($integrityFailures.Count -gt 0) { throw "Base path has $($integrityFailures.Count) transport/corruption failures" }
    if ($protocolRate -lt 0.90) { throw "Base structured/tool/vision validity is only $([Math]::Round(100*$protocolRate,1))%" }
    $baseContext = @(Read-Q38LatestJsonl -Path (Join-Path $root 'results\requests\qwen27-compat-context32k-mtp-off.jsonl'))
    if ($baseContext.Count -ne 1 -or -not $baseContext[0].valid) { throw 'Base path failed the 32K buried-code retrieval gate' }

    $mtpGatePath = Join-Path $root 'state\mtp-eligibility.json'
    $mtpEligible = $false
    $mtpDetail = $null
    try {
        & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'qwen27-dual-production' -Vision -Mtp
        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-compat-mtp-on' `
            -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
            -Kind Assay -Concurrency 2 -MaxTokens 2048 -Mtp `
            -Families @('extraction', 'classification', 'tool_execution', 'document_ocr', 'chart_diagram', 'screenshot_grounded')
        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId 'qwen27-compat-context32k-mtp-on' `
            -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
            -Kind Load -Concurrency 1 -PromptTokens 32000 -MaxTokens 32 -RequestsPerClient 1 -Retrieval -Mtp
        $mtpPath = Join-Path $root 'results\requests\qwen27-compat-mtp-on.jsonl'
        $mtpRows = @(Read-Q38LatestJsonl -Path $mtpPath)
        $mtpIntegrity = @($mtpRows | Where-Object { -not $_.success -or $_.failure_class -in @('empty_output', 'replacement_character', 'invalid_special_token', 'repetition_loop') })
        $baseComparable = @($base | Where-Object { $_.task_family -in @('extraction', 'classification', 'tool_execution', 'document_ocr', 'chart_diagram', 'screenshot_grounded') })
        $baseRate = @($baseComparable | Where-Object { $_.valid }).Count / [Math]::Max(1, $baseComparable.Count)
        $mtpRate = @($mtpRows | Where-Object { $_.valid }).Count / [Math]::Max(1, $mtpRows.Count)
        if ($mtpIntegrity.Count -gt 0) { throw "MTP path has $($mtpIntegrity.Count) transport/corruption failures" }
        if ($mtpRate -lt $baseRate) { throw "MTP validity regressed ($mtpRate vs base $baseRate)" }
        $mtpContext = @(Read-Q38LatestJsonl -Path (Join-Path $root 'results\requests\qwen27-compat-context32k-mtp-on.jsonl'))
        if ($mtpContext.Count -ne 1 -or -not $mtpContext[0].valid) { throw 'MTP path failed the 32K buried-code retrieval gate' }
        $mtpEligible = $true
        $mtpDetail = "MTP compatibility passed at comparable validity $mtpRate."
    } catch {
        Assert-Q38FailureQuarantinable -RunId 'qwen27-compat-mtp-on' -Message $_.Exception.Message
        Assert-Q38FailureQuarantinable -RunId 'qwen27-compat-context32k-mtp-on' -Message $_.Exception.Message
        $mtpDetail = "MTP quarantined: $($_.Exception.Message)"
    } finally {
        & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
    }
    Write-Q38JsonAtomic -Path $mtpGatePath -Value ([ordered]@{ eligible = $mtpEligible; evaluated_at = (Get-Date).ToString('o'); detail = $mtpDetail })
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail "Base compatibility passed; protocol rate=$protocolRate. $mtpDetail"
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
