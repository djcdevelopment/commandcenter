param(
    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 5904
)

$ErrorActionPreference = 'Stop'

$sshExe = (Get-Command ssh.exe -ErrorAction Stop).Source
$viewerExe = Join-Path $env:LOCALAPPDATA 'Programs\TigerVNC\vncviewer.exe'
if (-not (Test-Path -LiteralPath $viewerExe)) {
    throw "TigerVNC Viewer not found: $viewerExe"
}

if (Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Local TCP port $LocalPort is already in use"
}

$remoteCommand = 'exec env DISPLAY=:0 x11vnc -display :0 -localhost -nopw -once -timeout 15 -shared -rfbport 5900'
$sshArguments = "-o ExitOnForwardFailure=yes -L ${LocalPort}:127.0.0.1:5900 homebase `"$remoteCommand`""
$sshProcess = Start-Process -FilePath $sshExe -ArgumentList $sshArguments -PassThru -WindowStyle Hidden

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        if ($sshProcess.HasExited) {
            throw "SSH tunnel exited before x11vnc became ready (exit $($sshProcess.ExitCode))"
        }

        if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 100
    }

    if (-not $ready) {
        throw "Timed out waiting for the AM4 VNC tunnel on port $LocalPort"
    }

    # The SSH listener appears just before the remote x11vnc process is ready.
    # A short grace period avoids consuming x11vnc's one allowed client with a
    # synthetic readiness probe.
    Start-Sleep -Milliseconds 750
    $viewer = Start-Process -FilePath $viewerExe -ArgumentList "127.0.0.1::$LocalPort" -PassThru
    $viewer.WaitForExit()
}
finally {
    if ($sshProcess -and -not $sshProcess.HasExited) {
        Stop-Process -Id $sshProcess.Id
        $sshProcess.WaitForExit()
    }

    # x11vnc can survive an abruptly closed SSH channel. Clean only the process
    # owning AM4's dedicated loopback VNC port; this cannot touch another port.
    $cleanupArguments = '-o BatchMode=yes -o ConnectTimeout=5 homebase fuser -k 5900/tcp'
    $cleanup = Start-Process -FilePath $sshExe -ArgumentList $cleanupArguments -PassThru -Wait -WindowStyle Hidden
    if ($cleanup.ExitCode -gt 1) {
        Write-Warning "Could not confirm remote x11vnc cleanup (SSH exit $($cleanup.ExitCode))"
    }
}
