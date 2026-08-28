[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$renderRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'var\render'
$pausePath = Join-Path $renderRoot 'PAUSED'
$workerTasks = @(
    'BF6RenderBridge',
    'BF6RenderAgent',
    'BF6Extractor'
)
$captureTask = 'BF6 Local Highlights Agent'
$watchdogTask = 'BF6PipelineWatchdog'

# Enable launch control before removing the hold, so a partial failure leaves
# the pipeline paused instead of half-resumed.
foreach ($name in $workerTasks) {
    Enable-ScheduledTask -TaskName $name | Out-Null
}
Enable-ScheduledTask -TaskName $watchdogTask | Out-Null

Remove-Item -LiteralPath $pausePath -Force -ErrorAction SilentlyContinue

foreach ($name in $workerTasks) {
    Start-ScheduledTask -TaskName $name
}
Start-ScheduledTask -TaskName $captureTask
Start-ScheduledTask -TaskName $watchdogTask

Write-Host 'BF6 processing resumed. The preserved render queue will recover automatically.'
Write-Host 'Status: http://192.168.12.233:8787/api/render/status'
