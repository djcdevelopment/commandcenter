[CmdletBinding()]
param(
    [string]$CommandCenterRoot = 'C:\work\commandcenter',
    [string]$ResumeRoot = 'E:\resume',
    [string]$TaskName = 'HearthPublicPortfolioStage',
    [string]$At = '03:15'
)

$ErrorActionPreference = 'Stop'
$stageScript = Join-Path $CommandCenterRoot 'hearth\etc\stage-public-portfolio.ps1'
if (-not (Test-Path -LiteralPath $stageScript -PathType Leaf)) {
    throw "Staging script not found: $stageScript"
}

$actionArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy', 'Bypass',
    '-WindowStyle', 'Hidden',
    '-File', ('"{0}"' -f $stageScript),
    '-CommandCenterRoot', ('"{0}"' -f $CommandCenterRoot),
    '-ResumeRoot', ('"{0}"' -f $ResumeRoot)
) -join ' '
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

$registration = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Description = 'Stages privacy-safe HEARTH and career proof candidates; never publishes them.'
    Force = $true
}
Register-ScheduledTask @registration | Out-Null

Write-Output "Registered $TaskName for $At daily. The task stages candidates only; publication remains manual."
