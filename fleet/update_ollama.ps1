<#
update_ollama.ps1 -- the deliberate Ollama update lane on OMEN (ADR-0032).

Ollama's tray app self-updater is retired from autostart because its silent
in-place upgrade aborts against the ollama.exe that OllamaBoot keeps in use,
and the Inno Setup rollback then uninstalls lib/ollama -- a zombie install that
answers /api/tags but 500s every generate (2026-07-17, 2026-07-30, 2026-08-13).

This script is the sanctioned replacement: stop serve FIRST so no file is in
use, install, put the tray back to bed, bring serve back under OllamaBoot, and
prove serviceability with a real one-token generate. Run it as derek
(non-elevated is fine); it exits non-zero if the post-update probe fails.

  powershell -ExecutionPolicy Bypass -File fleet\update_ollama.ps1
  powershell ... -File fleet\update_ollama.ps1 -SetupExe C:\path\OllamaSetup.exe
#>
[CmdletBinding()]
param(
    # Optional cached installer; default is winget (which verifies its download).
    [string]$SetupExe,
    # Resident model for the post-update generate probe.
    [string]$ProbeModel = 'qwen3-coder:30b'
)

$ErrorActionPreference = 'Stop'
function Log([string]$m) { Write-Host "[update_ollama] $m" }

$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupLnk = Join-Path $startupDir 'Ollama.lnk'
$repoRoot   = Split-Path $PSScriptRoot -Parent

function Stop-OllamaProcesses {
    # Two names: the server ("ollama") and the tray app ("ollama app").
    Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue |
        Stop-Process -Force -Confirm:$false
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue) -and
           ((Get-Date) -lt $deadline)) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue) {
        throw 'ollama processes would not exit; aborting before the installer can hit an in-use file'
    }
}

# --- 1. Quiesce: nothing may hold ollama.exe while the installer runs -------
Log 'stopping OllamaBoot and all ollama processes'
try { Stop-ScheduledTask -TaskName OllamaBoot -ErrorAction Stop } catch {}
Stop-OllamaProcesses

# --- 2. Install --------------------------------------------------------------
if ($SetupExe) {
    if (-not (Test-Path $SetupExe)) { throw "SetupExe not found: $SetupExe" }
    Log "running installer: $SetupExe"
    $p = Start-Process -FilePath $SetupExe `
        -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' `
        -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "installer exited $($p.ExitCode)" }
} else {
    Log 'running winget upgrade Ollama.Ollama'
    winget upgrade --id Ollama.Ollama --exact --silent `
        --accept-package-agreements --accept-source-agreements
    # 0x8A15002B: no applicable upgrade -- already current is a success here.
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        throw "winget exited $LASTEXITCODE"
    }
}

# --- 3. Put the tray back to bed ---------------------------------------------
# The installer's post-install step relaunches "ollama app.exe" (which spawns
# its own serve) and re-creates the Startup shortcut. Undo both so OllamaBoot
# stays the sole owner of serve and no ambient updater returns at next logon.
Start-Sleep -Seconds 3
Stop-OllamaProcesses
if (Test-Path $startupLnk) {
    $parked = Join-Path $startupDir 'Ollama.lnk.disabled'
    if (Test-Path $parked) { Remove-Item $startupLnk -Force -Confirm:$false }
    else { Rename-Item $startupLnk $parked }
    Log 'startup shortcut re-parked (tray app will not autostart)'
}

# --- 4. Serve comes back under OllamaBoot ------------------------------------
Log 'starting OllamaBoot'
Start-ScheduledTask -TaskName OllamaBoot
$deadline = (Get-Date).AddSeconds(60)
$version = $null
while (-not $version -and (Get-Date) -lt $deadline) {
    try {
        $version = (Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3).version
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $version) { throw 'ollama serve did not answer /api/version within 60s of OllamaBoot start' }
Log "serve is up, version $version"

# --- 5. Serviceability proof: runtime on disk AND a real generate ------------
# /api/version answering is exactly what a zombie install also does; only a
# completion proves the update left a working runtime behind.
Log "probing with a one-token generate on $ProbeModel (cold load can take a while)"
Push-Location $repoRoot
try {
    python -m fleet.ollama_sentinel --json --probe-generate --probe-model $ProbeModel
    $probeExit = $LASTEXITCODE
} finally { Pop-Location }
if ($probeExit -ne 0) {
    Log "FAILED: sentinel serviceability probe exited $probeExit -- Ollama is up but cannot serve"
    exit $probeExit
}
Log "OK: updated to $version and verified serving"
