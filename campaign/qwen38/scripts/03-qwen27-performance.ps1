param(
    [switch]$Force,
    [switch]$AcknowledgeThermalQuarantine
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$stage = '03-qwen27-performance'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
$quarantinePath = Join-Path $root 'results\quarantine\performance-cells.jsonl'

function Record-Quarantine(
    [string]$Cell,
    [string]$Topology,
    [string]$Disposition,
    [string]$Reason,
    [string]$Evidence = '',
    $TemperatureC = $null
) {
    if (Test-Path -LiteralPath $quarantinePath) {
        $existing = @(Get-Content -LiteralPath $quarantinePath -Encoding UTF8 |
            Where-Object { $_.Trim() } |
            ForEach-Object {
                try { $_ | ConvertFrom-Json } catch { $null }
            } |
            Where-Object { $_.cell -eq $Cell -and $_.disposition -eq $Disposition })
        if ($existing.Count) { return }
    }
    [ordered]@{
        contract_version = 'qwen38-performance-quarantine.v1'
        timestamp = (Get-Date).ToString('o')
        cell = $Cell
        topology = $Topology
        disposition = $Disposition
        reason = $Reason
        evidence = $Evidence
        max_temperature_c = $TemperatureC
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $quarantinePath -Encoding UTF8
}

function Record-ReplicaThermalQuarantine($Evidence, [bool[]]$MtpModes) {
    $reason = "Replica placement quarantined after $($Evidence.run_id) reached $($Evidence.max_temperature_c) C; operator acknowledgment is recorded in $($Evidence.amendment_path)."
    foreach ($mtpEnabled in $MtpModes) {
        $suffix = if ($mtpEnabled) { 'mtp-on' } else { 'mtp-off' }
        foreach ($promptTokens in @(512, 8192, 32768)) {
            $concurrencies = if ($promptTokens -eq 512) { @($config.matrix.concurrency) } else { @($config.matrix.concurrency | Where-Object { [int]$_ -le 16 }) }
            foreach ($concurrency in $concurrencies) {
                $cell = "qwen27-replica-production-$suffix-p$promptTokens-c$concurrency"
                if (Test-Q38LegPassed -RunId $cell) { continue }
                $disposition = if ($cell -eq $Evidence.run_id) { 'thermal-aborted' } else { 'skipped-thermal-quarantine' }
                Record-Quarantine -Cell $cell -Topology 'qwen27-replica-production' -Disposition $disposition `
                    -Reason $reason -Evidence $Evidence.abort_path -TemperatureC $Evidence.max_temperature_c
            }
        }
        foreach ($slotDepth in @($config.matrix.throughput_slot_depths)) {
            foreach ($aggregateConcurrency in @($config.matrix.concurrency)) {
                $cell = "replica-throughput-$suffix-d$slotDepth-c$aggregateConcurrency"
                Record-Quarantine -Cell $cell -Topology 'qwen27-replica-throughput' `
                    -Disposition 'skipped-thermal-quarantine' -Reason $reason `
                    -Evidence $Evidence.abort_path -TemperatureC $Evidence.max_temperature_c
            }
        }
    }
}

try {
    $mtpGatePath = Join-Path $root 'state\mtp-eligibility.json'
    $mtpModes = @($false)
    if (Test-Path -LiteralPath $mtpGatePath) {
        $mtpGate = Get-Content -LiteralPath $mtpGatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([bool]$mtpGate.eligible) { $mtpModes += $true }
    }
    $thermalEvidence = $null
    $productionTopologies = @('qwen27-dual-production')
    if ($AcknowledgeThermalQuarantine) {
        $thermalEvidence = Assert-Q38ThermalQuarantineEvidence
        Record-ReplicaThermalQuarantine -Evidence $thermalEvidence -MtpModes $mtpModes
        Write-Warning "Reviewed replica thermal quarantine active: $($thermalEvidence.reason)"
    } else {
        $productionTopologies += 'qwen27-replica-production'
    }
    foreach ($topology in $productionTopologies) {
        foreach ($mtpEnabled in $mtpModes) {
            $start = @{ Action = 'Start'; Topology = $topology; Vision = $true }
            if ($mtpEnabled) { $start.Mtp = $true }
            & (Join-Path $PSScriptRoot 'server-control.ps1') @start
            foreach ($promptTokens in @(512, 8192, 32768)) {
                $concurrencies = if ($promptTokens -eq 512) { @($config.matrix.concurrency) } else { @($config.matrix.concurrency | Where-Object { [int]$_ -le 16 }) }
                foreach ($concurrency in $concurrencies) {
                    $suffix = if ($mtpEnabled) { 'mtp-on' } else { 'mtp-off' }
                    $runId = "$topology-$suffix-p$promptTokens-c$concurrency"
                    $leg = @{ RunId = $runId; Candidate = 'qwen38-27b'; Topology = $topology; Model = 'qwen38-27b'; Kind = 'Load'; Concurrency = $concurrency; PromptTokens = $promptTokens; MaxTokens = 200; RequestsPerClient = [int]$config.generation.measured_rounds }
                    if ($mtpEnabled) { $leg.Mtp = $true }
                    & (Join-Path $PSScriptRoot 'invoke-leg.ps1') @leg
                }
            }
            & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
        }
    }

    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'qwen27-dual-production' -Vision
    foreach ($visionConcurrency in @($config.matrix.vision_concurrency)) {
        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId "qwen27-vision-c$visionConcurrency" `
            -Candidate 'qwen38-27b' -Topology 'qwen27-dual-production' -Model 'qwen38-27b' `
            -Kind Assay -Concurrency ([int]$visionConcurrency) -MaxTokens 2048 `
            -Families @('document_ocr', 'chart_diagram', 'screenshot_grounded')
    }
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop

    if (-not $AcknowledgeThermalQuarantine) {
        foreach ($mtpEnabled in $mtpModes) {
            $mtpSuffix = if ($mtpEnabled) { 'mtp-on' } else { 'mtp-off' }
            foreach ($slotDepth in @($config.matrix.throughput_slot_depths)) {
                foreach ($aggregateConcurrency in @($config.matrix.concurrency)) {
                    $perServer = [Math]::Ceiling($aggregateConcurrency / 2)
                    $cell = "replica-throughput-$mtpSuffix-d$slotDepth-c$aggregateConcurrency"
                    try {
                        $start = @{ Action = 'Start'; Topology = 'qwen27-replica-throughput'; ParallelPerServer = $perServer; SlotDepth = $slotDepth }
                        if ($mtpEnabled) { $start.Mtp = $true }
                        & (Join-Path $PSScriptRoot 'server-control.ps1') @start
                        $leg = @{ RunId = $cell; Candidate = 'qwen38-27b'; Topology = 'qwen27-replica-throughput'; Model = 'qwen38-27b'; Kind = 'Load'; Concurrency = $aggregateConcurrency; PromptTokens = 512; MaxTokens = 200; RequestsPerClient = [int]$config.generation.measured_rounds }
                        if ($mtpEnabled) { $leg.Mtp = $true }
                        & (Join-Path $PSScriptRoot 'invoke-leg.ps1') @leg
                    } catch {
                        Assert-Q38FailureQuarantinable -RunId $cell -Message $_.Exception.Message
                        Record-Quarantine -Cell $cell -Topology 'qwen27-replica-throughput' `
                            -Disposition 'infeasible' -Reason $_.Exception.Message
                        break
                    } finally {
                        & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
                    }
                }
            }
        }
    }

    foreach ($depth in @(32768, 65536, 131072, 262144)) {
        foreach ($concurrency in @(1, 2, 4, 8)) {
            $cell = "dual-context-d$depth-c$concurrency"
            try {
                & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Start -Topology 'qwen27-dual-context' `
                    -ParallelPerServer $concurrency -SlotDepth $depth -Vision
                & (Join-Path $PSScriptRoot 'invoke-leg.ps1') -RunId $cell -Candidate 'qwen38-27b' `
                    -Topology 'qwen27-dual-context' -Model 'qwen38-27b' -Kind Load `
                    -Concurrency $concurrency -PromptTokens ([Math]::Max(512, $depth - 512)) -MaxTokens 32 -RequestsPerClient ([int]$config.generation.measured_rounds) -Retrieval
            } catch {
                Assert-Q38FailureQuarantinable -RunId $cell -Message $_.Exception.Message
                Record-Quarantine -Cell $cell -Topology 'qwen27-dual-context' `
                    -Disposition 'infeasible' -Reason $_.Exception.Message
                break
            } finally {
                & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
            }
        }
    }
    & python $script:Q38Python summarize
    if ($LASTEXITCODE -ne 0) { throw 'Summary derivation failed' }
    $detail = if ($AcknowledgeThermalQuarantine) {
        'Dual production, vision, and context-residency matrices completed; replica production/throughput placements were quarantined after reviewed thermal abort evidence.'
    } else {
        'Production, MTP, throughput-knee, and context-residency matrices completed; infeasible cells quarantined.'
    }
    Complete-Q38Receipt -Receipt $receipt -Status passed -Detail $detail
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
} finally {
    & (Join-Path $PSScriptRoot 'server-control.ps1') -Action Stop
}
