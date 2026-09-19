$ErrorActionPreference = 'Stop'
$controllerScript = 'C:\work\commandcenter-hermes-fx99\fleet\hermes\start-controller.ps1'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$entryName = 'HermesFleetController'
$command = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$controllerScript`""
$properties = Get-ItemProperty -LiteralPath $runKey
$existing = $properties.PSObject.Properties[$entryName].Value
if ($existing -and $existing -ne $command) {
    throw 'A different Hermes logon entry exists; refusing to overwrite it.'
}
New-ItemProperty -LiteralPath $runKey -Name $entryName -Value $command -PropertyType String -Force | Out-Null
Write-Output 'Installed current-user logon startup; no elevated service or GPU startup.'
