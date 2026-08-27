param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Topology,
    [Parameter(Mandatory = $true)][string]$Model,
    [Parameter(Mandatory = $true)][ValidateSet('Load', 'Assay')][string]$Kind,
    [int]$Concurrency = 1,
    [int]$PromptTokens = 512,
    [int]$MaxTokens = 200,
    [int]$RequestsPerClient = 3,
    [int]$DurationSeconds = 0,
    [switch]$Mtp,
    [switch]$ThinkOn,
    [switch]$Retrieval,
    [switch]$IncludeRepeats,
    [switch]$RepeatOnly,
    [string[]]$Families = @(),
    [string[]]$TaskIds = @()
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$root = Get-Q38RuntimeRoot
$serverState = Get-Content -LiteralPath (Join-Path $root 'state\servers.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($serverState.status -ne 'running') { throw "Recorded server state is '$($serverState.status)', not running" }
if ([string]$serverState.topology -ne $Topology) { throw "Requested topology $Topology does not match running topology $($serverState.topology)" }
if ([string]$serverState.candidate -ne $Candidate) { throw "Requested candidate $Candidate does not match running candidate $($serverState.candidate)" }
if ($Retrieval -and $Kind -ne 'Load') { throw '-Retrieval is valid only for Load legs' }
$serverMtp = [bool](Get-Q38Property -Object $serverState -Name 'mtp' -Default $false)
if ($serverMtp -ne [bool]$Mtp) { throw "Requested MTP=$([bool]$Mtp) does not match running server MTP=$serverMtp" }
$endpoints = @($serverState.servers | ForEach-Object { [string]$_.endpoint })
if ($endpoints.Count -eq 0) { throw 'No running campaign endpoints are recorded' }
$slotDepth = [int](($serverState.servers.slot_depth | Measure-Object -Minimum).Minimum)
$parallelSlots = [int](($serverState.servers.parallel | Measure-Object -Sum).Sum)
if ($slotDepth -lt 1 -or $parallelSlots -lt 1) { throw 'Recorded server topology has invalid slot metadata' }
$stopFile = Join-Path $root ("state\watchdog-stop-{0}" -f $RunId)
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
$watchOut = Join-Path $root ("results\telemetry\watchdog-{0}.stdout.log" -f $RunId)
$watchErr = Join-Path $root ("results\telemetry\watchdog-{0}.stderr.log" -f $RunId)
$watchPassed = Join-Path $root ("results\telemetry\watchdog-{0}-passed.json" -f $RunId)
$watchAbort = Join-Path $root ("results\telemetry\watchdog-{0}-abort.json" -f $RunId)
Remove-Item -LiteralPath $watchPassed -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $watchAbort -Force -ErrorAction SilentlyContinue
$maximumMinutes = if ($DurationSeconds -gt 0) { [Math]::Ceiling($DurationSeconds / 60) + 10 } else { 180 }
$watch = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'watchdog.ps1'),
    '-Stage', $RunId, '-StopFile', $stopFile, '-MaximumMinutes', [string]$maximumMinutes
) -RedirectStandardOutput $watchOut -RedirectStandardError $watchErr -WindowStyle Hidden -PassThru

try {
    $arguments = @(
        $Kind.ToLowerInvariant(), '--run-id', $RunId,
        '--candidate', $Candidate, '--topology', $Topology, '--model', $Model,
        '--concurrency', [string]$Concurrency, '--max-tokens', [string]$MaxTokens,
        '--slot-depth', [string]$slotDepth, '--parallel-slots', [string]$parallelSlots,
        '--candidate-revision', [string]$serverState.engine_revision,
        '--artifact-revision', [string]$serverState.artifact_revision,
        '--model-quant', [string]$serverState.model_quant,
        '--placement', [string]$serverState.placement,
        '--shared-postload-gb', ([double]$serverState.shared_postload_gb).ToString('R', [Globalization.CultureInfo]::InvariantCulture),
        '--commit-preload-gb', ([double]$serverState.commit_preload_gb).ToString('R', [Globalization.CultureInfo]::InvariantCulture),
        '--commit-postload-gb', ([double]$serverState.commit_postload_gb).ToString('R', [Globalization.CultureInfo]::InvariantCulture)
    )
    foreach ($endpoint in $endpoints) { $arguments += @('--endpoint', $endpoint) }
    if ($Mtp) { $arguments += '--mtp' }
    # Gated default for candidates is the no-think regime; -ThinkOn is the explicit
    # side-evidence override. The baseline model is non-thinking and gets neither.
    if ($Candidate -ne 'qwen3-30b-baseline' -and -not $ThinkOn) { $arguments += '--disable-thinking' }
    if ($Retrieval) { $arguments += '--retrieval' }
    if ($Kind -eq 'Load') {
        $arguments += @('--prompt-tokens', [string]$PromptTokens, '--requests-per-client', [string]$RequestsPerClient)
        if ($DurationSeconds -gt 0) { $arguments += @('--duration-s', [string]$DurationSeconds) }
    } else {
        if ($IncludeRepeats) { $arguments += '--include-repeats' }
        if ($RepeatOnly) { $arguments += '--repeat-only' }
        foreach ($family in $Families) { $arguments += @('--family', $family) }
        foreach ($taskId in $TaskIds) { $arguments += @('--task-id', $taskId) }
    }
    & python $script:Q38Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Request runner failed with exit code $LASTEXITCODE" }
} finally {
    New-Item -ItemType File -Path $stopFile -Force | Out-Null
    Wait-Process -Id $watch.Id -Timeout 90 -ErrorAction SilentlyContinue
    if ($null -ne (Get-Process -Id $watch.Id -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $watch.Id -Force -ErrorAction SilentlyContinue
        throw "Watchdog failed to stop; inspect $watchErr"
    }
}
$watch.Refresh()
if ($null -ne $watch.ExitCode -and $watch.ExitCode -ne 0) {
    throw "Watchdog aborted leg $RunId with exit code $($watch.ExitCode); inspect $watchErr"
}
if (-not (Test-Path -LiteralPath $watchPassed)) {
    throw "Watchdog did not emit a clean receipt for leg $RunId; inspect $watchErr"
}
$watchResult = Get-Content -LiteralPath $watchPassed -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$watchResult.status -ne 'passed' -or [string]$watchResult.stage -ne $RunId -or -not (Test-Path -LiteralPath ([string]$watchResult.telemetry))) {
    throw "Watchdog receipt is invalid for leg $RunId; inspect $watchPassed"
}
Write-Host "Leg $RunId completed with watchdog clean."
