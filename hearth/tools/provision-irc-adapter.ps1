[CmdletBinding()]
param(
    [string]$CallerId = "botherder-am4",
    [string]$Am4Host = "am4",
    [string]$Registry = "C:\work\commandcenter\hearth\var\callers.json",
    [string]$RemoteEnvironmentFile = "/etc/omen-irc/hearth-bot.env"
)

$ErrorActionPreference = "Stop"
$profiles = (Resolve-Path (Join-Path $PSScriptRoot "..\etc\profiles.toml")).Path
if (-not (Test-Path -LiteralPath $Registry)) {
    throw "HEARTH caller registry not found: $Registry"
}

$document = Get-Content -LiteralPath $Registry -Raw | ConvertFrom-Json
$exists = $false
foreach ($property in $document.PSObject.Properties) {
    if ($property.Value.id -eq $CallerId) {
        $exists = $true
        break
    }
}

$temporary = [System.IO.Path]::GetTempFileName()
try {
    $common = @(
        "-m", "hearth.callers.callerctl",
        "--registry", $Registry,
        "--profiles", $profiles
    )
    if ($exists) {
        & python @common rotate --id $CallerId --profile irc-adapter `
            --secret-file $temporary | Out-Null
    } else {
        & python @common mint --id $CallerId --runner-class local --node am4 `
            --profile irc-adapter --secret-file $temporary | Out-Null
    }
    if ($LASTEXITCODE -ne 0) {
        throw "callerctl failed with exit code $LASTEXITCODE"
    }
    $secret = (Get-Content -LiteralPath $temporary -Raw).Trim()
    if (-not $secret) {
        throw "callerctl did not write a secret"
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "ssh"
    $startInfo.Arguments = (
        "$Am4Host sudo install -o root -g root -m 0600 /dev/stdin " +
        $RemoteEnvironmentFile
    )
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "could not start ssh"
    }
    $process.StandardInput.WriteLine("HEARTH_API_KEY=$secret")
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        $detail = $process.StandardError.ReadToEnd().Trim()
        throw "could not install the AM4 environment file: $detail"
    }
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Force
    }
}

Write-Host "Provisioned $CallerId with profile=irc-adapter."
Write-Host "Installed the secret directly as $Am4Host`:$RemoteEnvironmentFile."
Write-Host "The secret was not printed. Restart HEARTH, then recreate BotHerder."
