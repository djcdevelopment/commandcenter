[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'BF6PipelineWatchdog'
$launcher = Join-Path $PSScriptRoot 'watchdog-bf6-pipeline-launch.vbs'
$identity = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Watchdog launcher is missing: $launcher"
}

$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" `
    -Argument ('"{0}"' -f $launcher)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 45) `
    -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description 'Every minute: heal stopped BF6 OMEN tasks; watch render heartbeat and AM4 health.' |
    Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host "$taskName registered for $identity and started."
Write-Host "Rollback: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
