$ErrorActionPreference = 'Stop'

$deskflowRoot = Join-Path $env:LOCALAPPDATA 'Programs\Deskflow\deskflow-1.26.0-win-x64-portable'
$deskflowExe = Join-Path $deskflowRoot 'deskflow.exe'

if (-not (Test-Path -LiteralPath $deskflowExe)) {
    throw "Deskflow executable not found: $deskflowExe"
}

$alreadyRunning = Get-Process -Name 'deskflow' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $deskflowExe }

if (-not $alreadyRunning) {
    Start-Process -FilePath $deskflowExe -WorkingDirectory $deskflowRoot -WindowStyle Hidden
}
