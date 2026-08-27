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
    $temperatureRows = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -match 'temperature_c$' -and @($Adapters.id) -contains $_.a })
    if (-not $temperatureRows.Count) { throw 'no B70 temperature samples were emitted' }
    $latestEnergy = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -eq 'gpu.energy_j_counter' -and @($Adapters.id) -contains $_.a } |
        Group-Object a | ForEach-Object { $_.Group | Sort-Object t | Select-Object -Last 1 })
    $latestHost = @($eventRows | Where-Object { $_.k -eq 'ms' -and $_.n -eq 'host.memory.used_bytes' } | Sort-Object t | Select-Object -Last 1)
    [pscustomobject]@{
        probe_path = $probe
        max_temperature_c = [double](($temperatureRows.v | Measure-Object -Maximum).Maximum)
        energy_j_counter = if ($latestEnergy.Count) { [double](($latestEnergy.v | Measure-Object -Sum).Sum) } else { $null }
        local_vram_used_gb = $null
        local_vram_observability = 'unavailable-cross-process-windows-vulkan'
        host_ram_used_gb = if ($latestHost.Count) { [Math]::Round([double]$latestHost[0].v / 1GB, 3) } else { $null }
    }
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
