[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$randomBytes = New-Object byte[] 30
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($randomBytes)
    $webPassword = ([Convert]::ToBase64String($randomBytes).TrimEnd('=') `
        -replace '\+', '-' -replace '/', '_') + '!Aa7'

    $pinBytes = New-Object byte[] 4
    $rng.GetBytes($pinBytes)
    $pinNumber = [BitConverter]::ToUInt32($pinBytes, 0) % 10000
    $pin = $pinNumber.ToString('0000')

    & ssh fx99 'sudo systemctl stop fx99-sunshine'
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop Sunshine for credential setup.' }

    $webPassword |
        & ssh fx99 'sudo -u derek /usr/local/lib/fx99-desktop/set-web-credential-from-stdin.sh'
    if ($LASTEXITCODE -ne 0) { throw 'Could not set the temporary Sunshine credential.' }

    & ssh fx99 'sudo systemctl start fx99-sunshine'
    if ($LASTEXITCODE -ne 0) { throw 'Could not restart Sunshine.' }

    & ssh homebase "pkill -TERM -f '^/opt/moonlight/Moonlight.AppImage pair --pin ' >/dev/null 2>&1 || true"

    $pairJob = Start-Job -ScriptBlock {
        & ssh homebase "DISPLAY=:0 timeout 40 /opt/moonlight/Moonlight.AppImage pair --pin $using:pin 192.168.12.220"
        if ($LASTEXITCODE -ne 0) { throw 'Moonlight pairing failed.' }
    }

    Start-Sleep -Seconds 3
    $approval = @{
        username = 'derek'
        password = $webPassword
        pin = $pin
        name = 'AM4 monitor'
    } | ConvertTo-Json -Compress

    $approval | & ssh fx99 'sudo -u derek /usr/local/lib/fx99-desktop/approve-pairing.py'
    if ($LASTEXITCODE -ne 0) { throw 'Sunshine did not approve the pairing PIN.' }

    Start-Sleep -Seconds 2
    Stop-Job $pairJob
    Remove-Job $pairJob -Force
    $pairJob = $null
    & ssh homebase "pkill -TERM -f '^/opt/moonlight/Moonlight.AppImage pair --pin ' >/dev/null 2>&1 || true"

    & ssh homebase 'DISPLAY=:0 /opt/moonlight/Moonlight.AppImage list 192.168.12.220'
    if ($LASTEXITCODE -ne 0) { throw 'Pairing completed but Moonlight cannot list FX99 apps.' }

    & ssh fx99 'sudo systemctl stop fx99-sunshine; sudo -u derek /usr/local/lib/fx99-desktop/rotate-web-credential.sh; sudo systemctl start fx99-sunshine'
    if ($LASTEXITCODE -ne 0) { throw 'Pairing worked, but final Web UI credential rotation failed.' }
} finally {
    if ($pairJob) {
        Remove-Job $pairJob -Force -ErrorAction SilentlyContinue
    }
    & ssh homebase "pkill -TERM -f '^/opt/moonlight/Moonlight.AppImage pair --pin ' >/dev/null 2>&1 || true" 2>$null
    $webPassword = $null
    $approval = $null
    if ($randomBytes) { [Array]::Clear($randomBytes, 0, $randomBytes.Length) }
    if ($pinBytes) { [Array]::Clear($pinBytes, 0, $pinBytes.Length) }
    $rng.Dispose()
}

Write-Host 'AM4 is paired with FX99.'
