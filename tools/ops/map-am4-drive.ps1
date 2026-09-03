[CmdletBinding()]
param(
    [switch]$ResetCredential
)

$ErrorActionPreference = 'Stop'

$server = '192.168.12.233'
$remotePath = "\\$server\AM4"
$localPath = 'A:'
$userName = 'derek'

$current = Get-SmbMapping -LocalPath $localPath -ErrorAction SilentlyContinue
if ($current -and $current.RemotePath -ne $remotePath) {
    throw "$localPath is already mapped to $($current.RemotePath)."
}

if ($current -and $current.Status -eq 'OK' -and -not $ResetCredential) {
    Write-Host "$localPath is already connected to $remotePath."
    return
}

if ($current) {
    $current | Remove-SmbMapping -Force -UpdateProfile
}

if (-not $ResetCredential) {
    New-SmbMapping `
        -LocalPath $localPath `
        -RemotePath $remotePath `
        -Persistent $true | Out-Null
} else {
    $randomBytes = New-Object byte[] 30
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($randomBytes)
    } finally {
        $rng.Dispose()
    }

    $password = ([Convert]::ToBase64String($randomBytes).TrimEnd('=') `
        -replace '\+', '-' -replace '/', '_') + '!Aa7'

    try {
        $passwordInput = "$password`n$password`n"
        $passwordResult = $passwordInput |
            & ssh homebase 'sudo smbpasswd -s -a derek' 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the AM4 Samba credential: $passwordResult"
        }

        $credentialResult = & cmdkey.exe "/add:$server" "/user:$userName" "/pass:$password" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Could not save the AM4 credential in Windows Credential Manager: $credentialResult"
        }

        New-SmbMapping `
            -LocalPath $localPath `
            -RemotePath $remotePath `
            -Persistent $true | Out-Null
    } finally {
        $password = $null
        $passwordInput = $null
        [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    }
}

$mapping = Get-SmbMapping -LocalPath $localPath
if ($mapping.Status -ne 'OK') {
    throw 'The AM4 mapping did not establish a usable SMB connection.'
}

# The server refuses protocols below SMB 3 and requires transport encryption;
# reaching the share is therefore the encryption check as well as a liveness check.
Get-ChildItem "$localPath\" -Force | Select-Object -First 1 | Out-Null
Write-Host "$localPath -> $remotePath (SMB3, server-required encryption)"
