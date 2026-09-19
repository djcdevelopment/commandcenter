$ErrorActionPreference = 'Stop'
while ($true) {
    & ssh.exe -NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -R 127.0.0.1:8712:127.0.0.1:8712 fx99
    Start-Sleep -Seconds 5
}
