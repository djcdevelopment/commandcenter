$ErrorActionPreference = 'Stop'
$schedulerRepo = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
$schedulerState = 'C:\Users\derek\.fleet-scheduler'
if (Get-NetTCPConnection -State Listen -LocalPort 8713 -ErrorAction SilentlyContinue) {
    throw 'Port 8713 has an owner; do not start a duplicate.'
}
$pythonPath = 'C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe'
$gateway = Start-Process $pythonPath -WindowStyle Hidden -WorkingDirectory $schedulerRepo -ArgumentList @('-m','fleet.jev.local','serve') -RedirectStandardOutput "$schedulerState\gateway.stdout.log" -RedirectStandardError "$schedulerState\gateway.stderr.log" -PassThru
$tunnel = Start-Process ssh.exe -WindowStyle Hidden -ArgumentList @('-NT','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-o','StrictHostKeyChecking=yes','-R','127.0.0.1:8713:127.0.0.1:8713','fx99') -RedirectStandardOutput "$schedulerState\tunnel.stdout.log" -RedirectStandardError "$schedulerState\tunnel.stderr.log" -PassThru
[pscustomobject]@{gatewayPid=$gateway.Id; tunnelPid=$tunnel.Id; startedUtc=[DateTime]::UtcNow.ToString('o')}
