$ErrorActionPreference = 'Stop'
$controllerRoot = 'C:\work\commandcenter-hermes-fx99\fleet\hermes'
$controllerState = 'C:\Users\derek\.hermes-fleet'
if (Get-NetTCPConnection -State Listen -LocalPort 8712 -ErrorAction SilentlyContinue) {
    throw 'Port 8712 already has an owner; inspect it rather than start a duplicate.'
}
$gatewayProcess = Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"$controllerRoot\start-hearth.ps1") -RedirectStandardOutput "$controllerState\gateway.stdout.log" -RedirectStandardError "$controllerState\gateway.stderr.log" -PassThru
$tunnelProcess = Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"$controllerRoot\tunnel-omen.ps1") -RedirectStandardOutput "$controllerState\tunnel.stdout.log" -RedirectStandardError "$controllerState\tunnel.stderr.log" -PassThru
[pscustomobject]@{gatewaySupervisor=$gatewayProcess.Id; tunnelSupervisor=$tunnelProcess.Id; createdUtc=[DateTime]::UtcNow.ToString('o')}
