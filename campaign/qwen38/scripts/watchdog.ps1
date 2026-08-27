param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$StopFile,
    [int]$MaximumMinutes = 0
)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'lib.ps1')
$ErrorActionPreference = 'Continue'

$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$serverStatePath = Join-Path $root 'state\servers.json'
if (-not (Test-Path -LiteralPath $serverStatePath)) { throw "Server state is missing: $serverStatePath" }
$serverState = Get-Content -LiteralPath $serverStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$servers = @($serverState.servers)
$adapters = @(Resolve-Q38B70Adapters)
$baselineShared = [double]$serverState.shared_postload_gb
$startedAt = Get-Date
$deadline = if ($MaximumMinutes -gt 0) { $startedAt.AddMinutes($MaximumMinutes) } else { [datetime]::MaxValue }
$telemetrySession = "{0}-{1}-{2}" -f $Stage, (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$telemetryPath = Join-Path $root ("results\telemetry\watchdog-{0}.jsonl" -f $telemetrySession)
$abortPath = Join-Path $root ("results\telemetry\watchdog-{0}-abort.json" -f $Stage)
$sharedHits = 0
$healthFailures = @{}
foreach ($server in $servers) { $healthFailures[[string]$server.port] = 0 }

function Append-Sample($sample) {
    $sample | ConvertTo-Json -Compress -Depth 8 | Add-Content -LiteralPath $telemetryPath -Encoding UTF8
}

function Abort-Campaign([string]$reason, $sample) {
    foreach ($server in $servers) { Stop-Q38RecordedServer -Server $server }
    Write-Q38JsonAtomic -Path $abortPath -Value ([ordered]@{
        contract_version = 'qwen38-watchdog-abort.v1'
        stage = $Stage
        aborted_at = (Get-Date).ToString('o')
        reason = $reason
        sample = $sample
    })
    Write-Error "WATCHDOG ABORT [$Stage]: $reason"
    exit 1
}

while ((Get-Date) -lt $deadline) {
    $reason = $null
    $shared = $null
    $commit = $null
    try { $shared = Get-Q38SharedGB -Adapters $adapters } catch { $reason = "shared telemetry unavailable: $($_.Exception.Message)" }
    try { $commit = Get-Q38CommitFreeGB } catch { $reason = "commit telemetry unavailable: $($_.Exception.Message)" }
    $growth = if ($null -ne $shared) { [Math]::Round([double]$shared - $baselineShared, 3) } else { $null }
    if ($null -ne $growth -and $growth -gt [double]$config.safety.shared_growth_abort_gb) { $sharedHits++ } else { $sharedHits = 0 }
    if ($sharedHits -ge [int]$config.safety.shared_growth_consecutive_samples) { $reason = "shared-memory growth $growth GB exceeded limit twice" }
    if ($null -ne $commit -and $commit -lt [double]$config.safety.commit_min_free_gb) { $reason = "commit headroom $commit GB below floor" }

    $events = @(Get-Q38BadEvents -Since $startedAt)
    if ($events.Count -gt 0) { $reason = "system event detected: $($events[0].ProviderName) / $($events[0].Id)" }
    foreach ($server in $servers) {
        $key = [string]$server.port
        $ok = $false
        try {
            $health = Invoke-WebRequest -Uri ([string]$server.health_url) -UseBasicParsing -TimeoutSec 5
            if ($health.StatusCode -eq 200) { $ok = $true }
        } catch {}
        if ($ok) { $healthFailures[$key] = 0 } else { $healthFailures[$key]++ }
        if ($healthFailures[$key] -ge [int]$config.safety.health_failures_abort) { $reason = "health failed repeatedly on port $key" }
    }

    # b70tools is deliberately sampled as a one-shot. Its JSON is the authority
    # for B70 temperatures because HWiNFO cannot see them on this driver.
    $maxTemp = $null
    $energyCounter = $null
    $localVramUsed = $null
    $hostRamUsed = $null
    try {
        $b70 = Get-Q38B70TelemetrySample -Adapters $adapters -Label $Stage
        $maxTemp = $b70.max_temperature_c
        $energyCounter = $b70.energy_j_counter
        $localVramUsed = $b70.local_vram_used_gb
        $hostRamUsed = $b70.host_ram_used_gb
    } catch { $reason = "b70tools telemetry unavailable: $($_.Exception.Message)" }
    if ($null -ne $maxTemp -and $maxTemp -ge [double]$config.safety.vram_temperature_abort_c) { $reason = "GPU/VRAM temperature $maxTemp C reached abort line" }

    $liveServerProcesses = @($servers | ForEach-Object {
        Get-Process -Id ([int]$_.pid) -ErrorAction SilentlyContinue
    })
    $workingSetBytes = ($liveServerProcesses | Measure-Object -Property WorkingSet64 -Sum).Sum
    $privateMemoryBytes = ($liveServerProcesses | Measure-Object -Property PrivateMemorySize64 -Sum).Sum

    $sample = [ordered]@{
        timestamp = (Get-Date).ToString('o')
        stage = $Stage
        telemetry_session = $telemetrySession
        shared_gb = $shared
        shared_growth_gb = $growth
        shared_growth_hits = $sharedHits
        commit_free_gb = $commit
        max_temperature_c = $maxTemp
        energy_j_counter = $energyCounter
        local_vram_used_gb = $localVramUsed
        local_vram_observability = 'unavailable-cross-process-windows-vulkan'
        host_ram_used_gb = $hostRamUsed
        server_working_set_gb = [Math]::Round([double]$workingSetBytes / 1GB, 3)
        server_private_memory_gb = [Math]::Round([double]$privateMemoryBytes / 1GB, 3)
        health_failures = $healthFailures
        bad_event_count = $events.Count
    }
    Append-Sample $sample
    if ($reason) { Abort-Campaign -reason $reason -sample $sample }
    if (Test-Path -LiteralPath $StopFile) { break }
    Start-Sleep -Seconds ([int]$config.safety.sample_interval_s)
}

if (-not (Test-Path -LiteralPath $StopFile)) {
    Abort-Campaign -reason "watchdog maximum duration of $MaximumMinutes minutes elapsed" -sample $null
}

Write-Q38JsonAtomic -Path (Join-Path $root ("results\telemetry\watchdog-{0}-passed.json" -f $Stage)) -Value ([ordered]@{
    contract_version = 'qwen38-watchdog-result.v1'
    stage = $Stage
    status = 'passed'
    started_at = $startedAt.ToString('o')
    completed_at = (Get-Date).ToString('o')
    telemetry = $telemetryPath
})
exit 0
