[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workerTasks = @('BF6RenderBridge', 'BF6RenderAgent', 'BF6Extractor')
$renderRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'var\render'
$pausePath = Join-Path $renderRoot 'PAUSED'
$queueCounts = @{
    queue = @(Get-ChildItem -LiteralPath (Join-Path $renderRoot 'queue') -File -ErrorAction SilentlyContinue).Count
    claims = @(Get-ChildItem -LiteralPath (Join-Path $renderRoot 'claims') -File -ErrorAction SilentlyContinue).Count
    results = @(Get-ChildItem -LiteralPath (Join-Path $renderRoot 'results') -File -ErrorAction SilentlyContinue).Count
    inflight = @(Get-ChildItem -LiteralPath (Join-Path $renderRoot 'inflight') -File -ErrorAction SilentlyContinue).Count
}
$bf6Ffmpeg = @(Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" |
    Where-Object { $_.CommandLine -match 'E:\\BF6-Highlights\\work\\' })
try {
    $am4 = Invoke-RestMethod -Uri 'http://192.168.12.233:8787/api/render/status' -TimeoutSec 5
} catch {
    throw "AM4 render status is unavailable: $($_.Exception.Message)"
}
if ($queueCounts.queue -or $queueCounts.claims -or $queueCounts.results -or $queueCounts.inflight -or
    $bf6Ffmpeg.Count -or @($am4.in_flight).Count -or @($am4.stuck_segments).Count) {
    throw ("Legacy pipeline is not drained: queue={0}, claims={1}, results={2}, inflight={3}, " +
        "ffmpeg={4}, am4_in_flight={5}, stuck={6}") -f
        $queueCounts.queue, $queueCounts.claims, $queueCounts.results,
        $queueCounts.inflight, $bf6Ffmpeg.Count, @($am4.in_flight).Count,
        @($am4.stuck_segments).Count
}

[IO.File]::WriteAllText(
    $pausePath,
    "Hatchet production authority; legacy workers held for rollback`n",
    [Text.UTF8Encoding]::new($false)
)
foreach ($name in $workerTasks) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName $name | Out-Null
}
$patterns = @(
    '^\s*.*python(?:\.exe)?\s+extract\.py\s+--poll\s+20\s*$',
    '^\s*.*python(?:\.exe)?\s+-m\s+hearth\.media\.agent\s*$',
    '^\s*.*python(?:\.exe)?\s+-m\s+hearth\.media\.bf6_bridge\s*$'
)
foreach ($process in Get-CimInstance Win32_Process -Filter "Name='python.exe'") {
    if ($patterns | Where-Object { $process.CommandLine -match $_ }) {
        taskkill.exe /PID $process.ProcessId /T /F | Out-Null
    }
}
Start-Sleep -Seconds 1
$remaining = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $command = $_.CommandLine
    $patterns | Where-Object { $command -match $_ }
})
if ($remaining) { throw 'One or more legacy BF6 worker processes survived the pause.' }
Write-Host 'Legacy BF6 processing is drained, paused, and disabled. OBS capture was not touched.'
