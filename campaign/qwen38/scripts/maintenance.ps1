param(
    [Parameter(Mandatory = $true)][ValidateSet('Enter', 'Restore', 'Status')][string]$Action,
    [switch]$ConfirmOutage,
    [int]$RestoreTimeoutSeconds = 600
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$root = Get-Q38RuntimeRoot
$statePath = Join-Path $root 'state\maintenance.json'
$restartBlockPath = Join-Path $script:Q38RepoRoot 'hearth\var\arc-maintenance.stop'

function Get-ArcProcesses {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '(?:--port|-port)\s+8082(?:\s|$)' })

    # Processes launched by ArcServeBoot can be visible to TCP inspection while
    # Win32_Process withholds their CommandLine. Include a verified
    # llama-server owner of the production listener so maintenance can still
    # stop that process without ever killing an unrelated port owner.
    $listenerPids = @(Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($listenerPid in $listenerPids) {
        if (@($processes | Where-Object { $_.ProcessId -eq $listenerPid }).Count) { continue }
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
        if ($owner -and $owner.Name -eq 'llama-server.exe') { $processes += $owner }
    }

    @($processes | Sort-Object ProcessId -Unique)
}

function Get-CampaignProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '(?:--port|-port)\s+181\d\d(?:\s|$)' })
}

function Protect-Q38CommandLine([string]$CommandLine) {
    if (-not $CommandLine) { return $CommandLine }
    $CommandLine -replace '(?i)((?:--api-key|-api-key)\s+)(?:"[^"]*"|\S+)', '$1<redacted>'
}

if ($Action -eq 'Status') {
    $state = if (Test-Path -LiteralPath $statePath) { Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    [pscustomobject]@{
        maintenance = if ($state) { $state.status } else { 'not-entered' }
        production_processes = @(Get-ArcProcesses).Count
        campaign_processes = @(Get-CampaignProcesses).Count
        production_port = @(Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue).Count
        production_restart_blocked = Test-Path -LiteralPath $restartBlockPath
    }
    exit 0
}

if ($Action -eq 'Enter') {
    if (-not $ConfirmOutage) { throw 'Refusing to stop HEARTH local inference without -ConfirmOutage' }
    Invoke-Q38Python init
    & (Join-Path $PSScriptRoot 'preflight.ps1') -Mode Hardware
    if (Test-Path -LiteralPath $statePath) {
        $existing = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($existing.status -eq 'entered') {
            if (@(Get-ArcProcesses).Count -or @(Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue).Count) {
                throw 'Maintenance receipt says entered, but the production process/port is active'
            }
            [IO.File]::WriteAllText($restartBlockPath, "qwen38 maintenance`r`n")
            Write-Host "Maintenance is already entered ($($existing.entered_at)); no action taken"
            exit 0
        }
    }
    if (@(Get-CampaignProcesses).Count) {
        throw 'Campaign-port llama-server processes already exist; run 99-restore.ps1 before entering a new window'
    }
    $taskSnapshot = (schtasks /Query /TN ArcServeBoot /FO LIST /V 2>&1 | Out-String)
    $restartTask = (schtasks /Query /TN ArcServeRestart /FO LIST /V 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "ArcServeRestart scheduled task is unavailable: $restartTask" }
    $arcBefore = @(Get-ArcProcesses)
    $arcPids = @($arcBefore | ForEach-Object { [int]$_.ProcessId })
    $otherLlama = @(Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $arcPids -notcontains [int]$_.ProcessId })
    if ($otherLlama.Count) {
        throw "Refusing elevated stop because unrelated llama-server processes exist: $($otherLlama.ProcessId -join ', ')"
    }
    $state = [ordered]@{
        contract_version = 'qwen38-maintenance.v1'
        status = 'entering'
        entered_at = (Get-Date).ToString('o')
        restored_at = $null
        task_snapshot = $taskSnapshot
        production_pids_before = @($arcBefore | ForEach-Object { $_.ProcessId })
        production_command_lines = @($arcBefore | ForEach-Object { Protect-Q38CommandLine -CommandLine ([string]$_.CommandLine) })
    }
    Write-Q38JsonAtomic -Path $statePath -Value $state

    try {
        [IO.File]::WriteAllText($restartBlockPath, "qwen38 maintenance`r`n")
        schtasks /End /TN ArcServeBoot | Out-Null
        schtasks /Run /TN ArcServeRestart | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to run elevated ArcServeRestart stop path' }

        $stopDeadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Seconds 1
            $productionActive = @(Get-ArcProcesses).Count -or @(Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue).Count
        } while ($productionActive -and (Get-Date) -lt $stopDeadline)
        if ($productionActive) { throw 'Production omen-arc process or port 8082 remained after elevated maintenance stop' }
    } catch {
        Remove-Item -LiteralPath $restartBlockPath -Force -ErrorAction SilentlyContinue
        schtasks /Run /TN ArcServeBoot | Out-Null
        throw
    }
    $state.status = 'entered'
    $state.production_stopped_at = (Get-Date).ToString('o')
    Write-Q38JsonAtomic -Path $statePath -Value $state
    Write-Host 'HEARTH local inference is in campaign maintenance; cloud gateway paths were not modified.'
    exit 0
}

# Restore is intentionally usable even when state is incomplete: it is the
# emergency path after a killed shell or partially written campaign stage.
foreach ($process in @(Get-CampaignProcesses)) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
$serverState = Join-Path $root 'state\servers.json'
if (Test-Path -LiteralPath $serverState) {
    $servers = Get-Content -LiteralPath $serverState -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($server in @($servers.servers)) {
        Stop-Q38RecordedServer -Server $server
    }
}
Start-Sleep -Seconds 2
Remove-Item -LiteralPath $restartBlockPath -Force -ErrorAction SilentlyContinue
schtasks /Run /TN ArcServeBoot | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to start ArcServeBoot' }

$probe = Join-Path $script:Q38RepoRoot 'fleet\arcserve\arc-serviceability.ps1'
$deadline = (Get-Date).AddSeconds($RestoreTimeoutSeconds)
$proved = $false
do {
    Start-Sleep -Seconds 5
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $probe *> $null
    if ($LASTEXITCODE -eq 0) { $proved = $true; break }
} while ((Get-Date) -lt $deadline)
if (-not $proved) { throw "ArcServeBoot did not pass real generation serviceability within $RestoreTimeoutSeconds seconds" }

$state = if (Test-Path -LiteralPath $statePath) {
    Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    [pscustomobject]@{ contract_version = 'qwen38-maintenance.v1'; entered_at = $null }
}
$restored = [ordered]@{
    contract_version = 'qwen38-maintenance.v1'
    status = 'restored'
    entered_at = Get-Q38Property -Object $state -Name 'entered_at'
    production_stopped_at = Get-Q38Property -Object $state -Name 'production_stopped_at'
    restored_at = (Get-Date).ToString('o')
    real_completion_proved = $true
    production_pids_before = Get-Q38Property -Object $state -Name 'production_pids_before' -Default @()
    production_command_lines = Get-Q38Property -Object $state -Name 'production_command_lines' -Default @()
    task_snapshot = Get-Q38Property -Object $state -Name 'task_snapshot'
}
Write-Q38JsonAtomic -Path $statePath -Value $restored
Write-Host 'ArcServeBoot restored and proved by a real one-token completion.'
