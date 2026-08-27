$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:Q38SourceRoot = Split-Path -Parent $PSScriptRoot
$script:Q38ConfigPath = Join-Path $script:Q38SourceRoot 'config\campaign.json'
$script:Q38ArtifactsPath = Join-Path $script:Q38SourceRoot 'config\artifacts.json'
$script:Q38Python = Join-Path $script:Q38SourceRoot 'qwen38_campaign.py'
$script:Q38RepoRoot = [string](Get-Content -LiteralPath $script:Q38ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).commandcenter_root

function Get-Q38Config {
    Get-Content -LiteralPath $script:Q38ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-Q38RuntimeRoot {
    [string](Get-Q38Config).runtime_root
}

function Get-Q38Artifact {
    param([Parameter(Mandatory = $true)][string]$Id)
    $doc = Get-Content -LiteralPath $script:Q38ArtifactsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $artifact = @($doc.artifacts | Where-Object { $_.id -eq $Id })
    if ($artifact.Count -ne 1) { throw "Expected exactly one artifact '$Id'; found $($artifact.Count)" }
    $artifact[0]
}

function Get-Q38Property {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        $Default = $null
    )
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    $property.Value
}

function Stop-Q38RecordedServer {
    param([Parameter(Mandatory = $true)]$Server)
    $processId = [int](Get-Q38Property -Object $Server -Name 'pid' -Default 0)
    $port = [int](Get-Q38Property -Object $Server -Name 'port' -Default 0)
    if ($processId -le 0 -or $port -le 0) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return }
    $portPattern = '(?:--port|-port)\s+' + [regex]::Escape([string]$port) + '(?:\s|$)'
    if ([string]$process.Name -ne 'llama-server.exe' -or [string]$process.CommandLine -notmatch $portPattern) {
        Write-Warning "Refusing to stop reused or mismatched PID $processId; it is not the recorded llama-server on port $port"
        return
    }
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

function Write-Q38JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temp = "$Path.$PID.tmp"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Read-Q38LatestJsonl {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "JSONL input is missing: $Path" }
    $rows = @(Get-Content -LiteralPath $Path -Encoding UTF8 |
        Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    @($rows | Group-Object request_id | ForEach-Object { $_.Group | Select-Object -Last 1 })
}

function Invoke-Q38Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & python $script:Q38Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "qwen38_campaign.py failed with exit code $LASTEXITCODE" }
}

function Assert-Q38Maintenance {
    $path = Join-Path (Get-Q38RuntimeRoot) 'state\maintenance.json'
    if (-not (Test-Path -LiteralPath $path)) { throw "Campaign maintenance has not been entered ($path is absent)" }
    $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.status -ne 'entered') { throw "Campaign maintenance state is '$($state.status)', not 'entered'" }
    $state
}

function Get-Q38CommitFreeGB {
    $os = Get-CimInstance Win32_OperatingSystem
    [Math]::Round([double]$os.FreeVirtualMemory / 1MB, 2)
}

function Resolve-Q38B70Adapters {
    param([switch]$Refresh)
    $root = Get-Q38RuntimeRoot
    $statePath = Join-Path $root 'state\b70-adapters.json'
    if ((Test-Path -LiteralPath $statePath) -and -not $Refresh) {
        return @((Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json).adapters)
    }
    $b70tools = 'E:\work\b70tools\build\b70tools.exe'
    if (-not (Test-Path -LiteralPath $b70tools)) { throw "b70tools is missing: $b70tools" }
    $probe = Join-Path $root ("results\telemetry\adapter-probe-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    & $b70tools --run --ticks 1 --cadence-ms 250 --no-sleep --flush-every-tick --out $probe | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "b70tools adapter probe failed with exit code $LASTEXITCODE" }
    $events = Join-Path $probe 'events.jsonl'
    $adapters = @(Get-Content -LiteralPath $events -Encoding UTF8 |
        Where-Object { $_ -match '"k":"ai"' } |
        ForEach-Object { $_ | ConvertFrom-Json } |
        Where-Object { $_.desc -match 'Arc.*Pro B70' } |
        ForEach-Object {
            $vulkanBinding = @($_.bind | Where-Object { $_ -match '^Vulkan:idx=(\d+)' }) | Select-Object -First 1
            $vulkanIndex = if ($vulkanBinding -match '^Vulkan:idx=(\d+)') { [int]$Matches[1] } else { $null }
            [pscustomobject]@{
                id = $_.a
                luid_fragment = ([string]$_.a).Replace('adapter_', '0x')
                description = $_.desc
                bdf = $_.bdf
                vulkan_index = $vulkanIndex
                dedicated_bytes = [int64]$_.dvm
            }
        })
    if ($adapters.Count -ne 2) { throw "Expected two B70 adapters from b70tools; found $($adapters.Count)" }
    Write-Q38JsonAtomic -Path $statePath -Value ([ordered]@{ resolved_at = (Get-Date).ToString('o'); adapters = $adapters })
    $adapters
}

function Get-Q38SharedGB {
    param([object[]]$Adapters = @())
    if ($Adapters.Count -eq 0) { $Adapters = @(Resolve-Q38B70Adapters) }
    try {
        $counter = Get-Counter '\GPU Adapter Memory(*)\Shared Usage' -ErrorAction Stop
        $values = @($counter.CounterSamples | Where-Object {
            $path = $_.Path
            @($Adapters | Where-Object { $path -match [regex]::Escape($_.luid_fragment) }).Count -gt 0
        } | ForEach-Object { [double]$_.CookedValue })
        if ($values.Count -eq 0) { throw 'No B70 shared-memory counter instances matched current LUIDs' }
        [Math]::Round([double](($values | Measure-Object -Maximum).Maximum) / 1GB, 3)
    } catch {
        throw "Unable to read B70 shared-memory counters: $($_.Exception.Message)"
    }
}

function Get-Q38B70TelemetrySample {
    param(
        [object[]]$Adapters = @(),
        [string]$Label = 'sample'
    )
    if ($Adapters.Count -eq 0) { $Adapters = @(Resolve-Q38B70Adapters) }
    $root = Get-Q38RuntimeRoot
    $safeLabel = $Label -replace '[^A-Za-z0-9_.-]', '_'
    $probe = Join-Path $root ("results\telemetry\temp-{0}-{1}-{2}" -f $safeLabel, (Get-Date -Format 'yyyyMMdd-HHmmssfff'), $PID)
    & 'E:\work\b70tools\build\b70tools.exe' --run --ticks 1 --cadence-ms 250 --no-sleep --flush-every-tick --out $probe | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "b70tools exited $LASTEXITCODE" }
    $eventsPath = Join-Path $probe 'events.jsonl'
    if (-not (Test-Path -LiteralPath $eventsPath)) { throw 'b70tools did not emit events.jsonl' }
    $eventRows = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8 |
        Where-Object { $_ -match '^\{' } | ForEach-Object { $_ | ConvertFrom-Json })
    $adapterIds = @($Adapters | ForEach-Object { [string]$_.id })
    $temperatureRows = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -match 'temperature_c$' -and $adapterIds -contains $_.a })
    if (-not $temperatureRows.Count) { throw 'no B70 temperature samples were emitted' }
    $adapterTemperatures = @(foreach ($adapter in $Adapters) {
        $gpuRow = @($temperatureRows | Where-Object { $_.a -eq $adapter.id -and $_.n -eq 'gpu.temperature_c' } |
            Sort-Object t | Select-Object -Last 1)
        $vramRow = @($temperatureRows | Where-Object { $_.a -eq $adapter.id -and $_.n -eq 'vram.temperature_c' } |
            Sort-Object t | Select-Object -Last 1)
        $values = @()
        if ($gpuRow.Count) { $values += [double]$gpuRow[0].v }
        if ($vramRow.Count) { $values += [double]$vramRow[0].v }
        if (-not $values.Count) { continue }
        [pscustomobject]@{
            adapter_id = [string]$adapter.id
            bdf = [string]$adapter.bdf
            vulkan_index = $adapter.vulkan_index
            gpu_temperature_c = if ($gpuRow.Count) { [double]$gpuRow[0].v } else { $null }
            vram_temperature_c = if ($vramRow.Count) { [double]$vramRow[0].v } else { $null }
            max_temperature_c = [double](($values | Measure-Object -Maximum).Maximum)
        }
    })
    $hottestRow = @($temperatureRows | Sort-Object { [double]$_.v } | Select-Object -Last 1)[0]
    $hottestAdapter = @($Adapters | Where-Object { $_.id -eq $hottestRow.a } | Select-Object -First 1)
    $latestEnergy = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -eq 'gpu.energy_j_counter' -and $adapterIds -contains $_.a } |
        Group-Object a | ForEach-Object { $_.Group | Sort-Object t | Select-Object -Last 1 })
    $latestHost = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -eq 'host.memory.used_bytes' } | Sort-Object t | Select-Object -Last 1)
    [pscustomobject]@{
        probe_path = $probe
        max_temperature_c = [double](($temperatureRows.v | Measure-Object -Maximum).Maximum)
        hottest_adapter_id = [string]$hottestRow.a
        hottest_adapter_bdf = if ($hottestAdapter.Count) { [string]$hottestAdapter[0].bdf } else { $null }
        hottest_sensor = [string]$hottestRow.n
        adapter_temperatures = $adapterTemperatures
        energy_j_counter = if ($latestEnergy.Count) { [double](($latestEnergy.v | Measure-Object -Sum).Sum) } else { $null }
        local_vram_used_gb = $null
        local_vram_observability = 'unavailable-cross-process-windows-vulkan'
        host_ram_used_gb = if ($latestHost.Count) { [Math]::Round([double]$latestHost[0].v / 1GB, 3) } else { $null }
    }
}

function Wait-Q38ThermalHeadroom {
    <#
        Proves the cards are cool before work starts. Used at maintenance entry
        and again before each deep-context cell: this campaign lost two
        topologies to heat because every cell began from wherever the previous
        one left the cards.

        -FatalOnTimeout is the difference between the two callers. At maintenance
        entry a hot box means do not start at all, so failing to cool is FATAL.
        Before an individual cell it only means that cell is not measurable now,
        which should quarantine the cell and let the campaign continue.
    #>
    param(
        [object[]]$Adapters = @(),
        [string]$Label = 'resume',
        [bool]$FatalOnTimeout = $true
    )
    $config = Get-Q38Config
    $root = Get-Q38RuntimeRoot
    if ($Adapters.Count -eq 0) { $Adapters = @(Resolve-Q38B70Adapters) }
    $resumeBelow = [double]$config.safety.temperature_resume_below_c
    $requiredSamples = [int]$config.safety.temperature_resume_consecutive_samples
    $timeoutSeconds = [int]$config.safety.temperature_resume_timeout_s
    $intervalSeconds = [int]$config.safety.sample_interval_s
    $startedAt = Get-Date
    $deadline = $startedAt.AddSeconds($timeoutSeconds)
    $session = "{0}-{1}-{2}" -f $Label, (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
    $telemetryPath = Join-Path $root ("results\telemetry\thermal-headroom-{0}.jsonl" -f $session)
    $receiptPath = Join-Path $root 'state\thermal-headroom.json'
    $coolSamples = 0
    do {
        $sample = Get-Q38B70TelemetrySample -Adapters $Adapters -Label $Label
        $row = [ordered]@{
            timestamp = (Get-Date).ToString('o')
            max_temperature_c = $sample.max_temperature_c
            adapter_temperatures = $sample.adapter_temperatures
            resume_below_c = $resumeBelow
            consecutive_cool_samples = $coolSamples
        }
        if ([double]$sample.max_temperature_c -lt $resumeBelow) { $coolSamples++ } else { $coolSamples = 0 }
        $row.consecutive_cool_samples = $coolSamples
        $row | ConvertTo-Json -Compress -Depth 8 | Add-Content -LiteralPath $telemetryPath -Encoding UTF8
        if ($coolSamples -ge $requiredSamples) {
            Write-Q38JsonAtomic -Path $receiptPath -Value ([ordered]@{
                contract_version = 'qwen38-thermal-headroom.v1'
                status = 'passed'
                started_at = $startedAt.ToString('o')
                completed_at = (Get-Date).ToString('o')
                resume_below_c = $resumeBelow
                consecutive_samples = $coolSamples
                telemetry = $telemetryPath
                final_sample = $row
            })
            Write-Host "Thermal headroom proved below $resumeBelow C for $coolSamples consecutive samples."
            return $row
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Seconds $intervalSeconds
    } while ($true)
    Write-Q38JsonAtomic -Path $receiptPath -Value ([ordered]@{
        contract_version = 'qwen38-thermal-headroom.v1'
        status = 'failed'
        started_at = $startedAt.ToString('o')
        completed_at = (Get-Date).ToString('o')
        resume_below_c = $resumeBelow
        consecutive_samples = $coolSamples
        telemetry = $telemetryPath
        final_sample = $row
    })
    $message = "B70 temperatures did not remain below $resumeBelow C within $timeoutSeconds seconds (label: $Label)"
    if ($FatalOnTimeout) { throw "FATAL SAFETY: $message" }
    throw $message
}

function Get-Q38BadEvents {
    param([Parameter(Mandatory = $true)][datetime]$Since)
    $events = @()
    foreach ($filter in @(
        @{ LogName = 'System'; Id = 4101; StartTime = $Since },
        @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; StartTime = $Since },
        @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $Since }
    )) {
        try { $events += @(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop) } catch {}
    }
    $events
}

function New-Q38Receipt {
    param([Parameter(Mandatory = $true)][string]$Stage)
    $path = Join-Path (Get-Q38RuntimeRoot) ("results\receipts\{0}.json" -f $Stage)
    $receipt = [ordered]@{
        contract_version = 'qwen38-stage-receipt.v1'
        stage = $Stage
        status = 'running'
        started_at = (Get-Date).ToString('o')
        completed_at = $null
        detail = $null
    }
    Write-Q38JsonAtomic -Path $path -Value $receipt
    [pscustomobject]@{ Path = $path; Value = $receipt }
}

function Complete-Q38Receipt {
    param(
        [Parameter(Mandatory = $true)]$Receipt,
        [Parameter(Mandatory = $true)][ValidateSet('passed', 'failed', 'quarantined', 'skipped')][string]$Status,
        [string]$Detail = ''
    )
    $Receipt.Value.status = $Status
    $Receipt.Value.completed_at = (Get-Date).ToString('o')
    $Receipt.Value.detail = $Detail
    Write-Q38JsonAtomic -Path $Receipt.Path -Value $Receipt.Value
}

function Test-Q38ReceiptPassed {
    param([Parameter(Mandatory = $true)][string]$Stage)
    $path = Join-Path (Get-Q38RuntimeRoot) ("results\receipts\{0}.json" -f $Stage)
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $row = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $row.status -eq 'passed'
}

function Test-Q38LegPassed {
    param(
        [Parameter(Mandatory = $true)][string]$RunId,
        [int]$ExpectedAttemptedRows = 0
    )
    if ($env:QWEN38_FORCE_LEGS -eq '1') { return $false }
    $root = Get-Q38RuntimeRoot
    $path = Join-Path $root ("results\telemetry\watchdog-{0}-passed.json" -f $RunId)
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $row = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$row.contract_version -ne 'qwen38-watchdog-result.v1') { return $false }
        if ([string]$row.status -ne 'passed' -or [string]$row.stage -ne $RunId) { return $false }
        if (-not (Test-Path -LiteralPath ([string]$row.telemetry))) { return $false }
    } catch {
        return $false
    }
    # A watchdog receipt certifies only that no safety line tripped. It is written
    # even when the request runner died, because invoke-leg drops the stop file in
    # its finally block on the failure path too. Skipping a leg on that receipt
    # alone would turn a crashed leg into a permanent silent hole in the matrix,
    # so require the measurements themselves before treating a leg as done.
    $requests = Join-Path $root ("results\requests\{0}.jsonl" -f $RunId)
    if (-not (Test-Path -LiteralPath $requests)) { return $false }
    $attempted = New-Object 'System.Collections.Generic.HashSet[string]'
    $successful = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($line in (Get-Content -LiteralPath $requests -Encoding UTF8)) {
        if (-not $line.Trim()) { continue }
        try { $parsed = $line | ConvertFrom-Json } catch { continue }
        [void]$attempted.Add([string]$parsed.request_id)
        if ($parsed.success) { [void]$successful.Add([string]$parsed.request_id) }
    }
    # Completeness is about COVERAGE, not success: a request that timed out at
    # depth is a measurement, not a gap, and re-running it would burn hours to
    # re-observe the same timeout. What must never be skipped is a leg that never
    # got to attempt its requests, or one where every attempt died.
    if ($successful.Count -lt 1) { return $false }
    if ($ExpectedAttemptedRows -gt 0 -and $attempted.Count -lt $ExpectedAttemptedRows) { return $false }
    return $true
}

function Assert-Q38ThermalQuarantineEvidence {
    $root = Get-Q38RuntimeRoot
    $config = Get-Q38Config
    $runId = 'qwen27-replica-production-mtp-off-p512-c4'
    $abortPath = Join-Path $root ("results\telemetry\watchdog-{0}-abort.json" -f $runId)
    $amendmentPath = Join-Path $root 'state\resume-amendment.json'
    $manifestPath = Join-Path $root 'state\run-manifest.json'
    foreach ($path in @($abortPath, $amendmentPath, $manifestPath)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Thermal-quarantine evidence is missing: $path" }
    }
    $abort = Get-Content -LiteralPath $abortPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $amendment = Get-Content -LiteralPath $amendmentPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $temperature = [double]$abort.sample.max_temperature_c
    if ([string]$abort.contract_version -ne 'qwen38-watchdog-abort.v1' -or [string]$abort.stage -ne $runId) {
        throw 'Thermal-quarantine abort receipt has the wrong contract or stage'
    }
    if ($temperature -lt [double]$config.safety.vram_temperature_abort_c -or [string]$abort.reason -notmatch 'temperature') {
        throw 'Thermal-quarantine abort receipt does not prove a threshold-crossing temperature event'
    }
    if ([string]$amendment.contract_version -ne 'qwen38-resume-amendment.v1' -or
        [string]$amendment.decision -ne 'operator_acknowledged_replica_thermal_quarantine' -or
        -not [bool]$amendment.model_and_engine_identity_unchanged -or
        -not [bool]$amendment.gate_constants_unchanged -or
        -not [bool]$amendment.task_set_unchanged) {
        throw 'Resume amendment does not authorize the reviewed thermal quarantine'
    }
    $requiredTopologies = @('qwen27-replica-production', 'qwen27-replica-throughput')
    foreach ($topology in $requiredTopologies) {
        if (@($amendment.quarantined_topologies) -notcontains $topology) {
            throw "Resume amendment does not quarantine $topology"
        }
    }
    $abortHash = (Get-FileHash -LiteralPath $abortPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([string]$amendment.abort_evidence.sha256 -ne $abortHash) { throw 'Resume amendment abort hash does not match current evidence' }
    if ([string]$amendment.current_manifest.sha256 -ne $manifestHash) { throw 'Resume amendment manifest hash does not match the active lock' }
    [pscustomobject]@{
        run_id = $runId
        abort_path = $abortPath
        amendment_path = $amendmentPath
        max_temperature_c = $temperature
        reason = [string]$abort.reason
    }
}

function Assert-Q38FailureQuarantinable {
    param(
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if ($Message -match '^FATAL SAFETY:') { throw $Message }
    $abortPath = Join-Path (Get-Q38RuntimeRoot) ("results\telemetry\watchdog-{0}-abort.json" -f $RunId)
    if (-not (Test-Path -LiteralPath $abortPath)) { return }
    $abort = Get-Content -LiteralPath $abortPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $reason = [string]$abort.reason
    if ($reason -match 'system event|temperature|commit headroom|telemetry unavailable') {
        throw "FATAL SAFETY: watchdog aborted ${RunId}: $reason"
    }
}
