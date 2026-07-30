<#
.SYNOPSIS
  Report (and optionally repair) OMEN's Ollama posture in one invocation.

.DESCRIPTION
  Written because the remediation for ADR-0028 was being handed over as pasted shell
  fragments -- a bare `python -m fleet.ollama_sentinel` with no working directory, and
  PowerShell one-liners with fragile escaping. Both failed on paste. This script takes no
  arguments, needs no working directory, and quotes nothing: it derives the repo root from
  its own location.

  Checks, in order:
    1. bind       -- is Ollama on loopback, or has OLLAMA_HOST pushed it onto every
                     interface? (the ADR-0028 drift)
    2. env        -- is the OLLAMA_HOST User variable gone, or has an installer re-seeded it?
    3. firewall   -- are the blanket program-scoped ollama.exe ALLOW rules back?
    4. runtime    -- does llama-server actually exist? Ollama answers /api/tags and
                     /api/version perfectly well without it while every generate 500s.
    5. serviceable-- a real one-token generate, via fleet.ollama_sentinel.

.PARAMETER Repair
  Run the Ollama installer already cached at %LOCALAPPDATA%\Ollama\OllamaSetup.exe (Ollama's
  own downloaded update payload -- nothing is fetched), then re-verify. This is the operator's
  action to take: it modifies the system, so the script never does it unless asked.

.EXAMPLE
  pwsh C:\work\commandcenter\tools\ops\fix-ollama.ps1
  pwsh C:\work\commandcenter\tools\ops\fix-ollama.ps1 -Repair

.NOTES
  Exit 0 = serviceable. Exit 1 = up but cannot serve, or posture drifted.
#>
[CmdletBinding()]
param(
    [switch]$Repair,
    [string]$Endpoint = 'http://127.0.0.1:11434'
)

$ErrorActionPreference = 'Continue'
$repoRoot  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installer = Join-Path $env:LOCALAPPDATA 'Ollama\OllamaSetup.exe'
$problems  = New-Object System.Collections.Generic.List[string]

function Say-Check($ok, $label, $detail) {
    $mark = if ($ok -eq $true) { '  [ ok ]' } elseif ($ok -eq $false) { '  [FAIL]' } else { '  [ -- ]' }
    Write-Host "$mark $label" -NoNewline
    if ($detail) { Write-Host "  $detail" } else { Write-Host '' }
}

function Test-OllamaPosture {
    Write-Host ''
    Write-Host "Ollama posture  (repo: $repoRoot)" -ForegroundColor Cyan
    Write-Host ('-' * 72)

    # 2. env is read first: it decides what an off-loopback bind MEANS ----------
    $envHost = [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User')

    # 1. bind -------------------------------------------------------------------
    # A process keeps the environment it was started with, so an off-loopback bind with
    # OLLAMA_HOST already unset is a STALE PROCESS, not a regression. Saying "the drift
    # is back" in that case would send the reader chasing a problem that is already
    # fixed and merely waiting on a restart. Distinguish the two.
    $listen = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
    if (-not $listen) {
        Say-Check $false 'bind      ' 'nothing listening on 11434 -- Ollama is not running'
        $problems.Add('ollama not running')
    } else {
        $addrs = ($listen.LocalAddress | Sort-Object -Unique) -join ', '
        $offLoopback = @($listen.LocalAddress | Where-Object { $_ -ne '127.0.0.1' -and $_ -ne '::1' })
        if ($offLoopback.Count -eq 0) {
            Say-Check $true 'bind      ' $addrs
        } elseif ($null -eq $envHost) {
            Say-Check $null 'bind      ' ("$addrs -- off-loopback, but OLLAMA_HOST is already unset: " +
                                          "this process predates the change and will bind loopback on restart")
            $problems.Add('bind off-loopback (stale process, restart pending)')
        } else {
            Say-Check $false 'bind      ' "$addrs -- off-loopback AND OLLAMA_HOST is set: the ADR-0028 drift is back"
            $problems.Add('bind is off-loopback')
        }
    }

    # env verdict ---------------------------------------------------------------
    if ($null -eq $envHost) {
        Say-Check $true 'env       ' 'OLLAMA_HOST unset (loopback default)'
    } else {
        Say-Check $false 'env       ' "OLLAMA_HOST=$envHost -- an installer likely re-seeded it; remove it"
        $problems.Add('OLLAMA_HOST is set')
    }

    # 3. firewall ---------------------------------------------------------------
    $blanket = @(Get-NetFirewallRule -DisplayName 'ollama.exe' -ErrorAction SilentlyContinue)
    if ($blanket.Count -eq 0) {
        Say-Check $true 'firewall  ' 'no blanket ollama.exe rules'
    } else {
        Say-Check $false 'firewall  ' "$($blanket.Count) blanket ollama.exe rule(s) back -- program-scoped, all ports"
        $problems.Add('blanket firewall rules present')
    }
    foreach ($rule in Get-NetFirewallRule -ErrorAction SilentlyContinue |
                      Where-Object { $_.DisplayName -match 'ollama' -and $_.DisplayName -ne 'ollama.exe' }) {
        $remote = ($rule | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue).RemoteAddress -join ','
        Say-Check $null 'firewall  ' "kept: $($rule.DisplayName)  remote=$remote"
    }

    # 4. runtime ----------------------------------------------------------------
    $installDir = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    $runtime = Get-ChildItem -Path $installDir -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue |
               Select-Object -First 1
    if ($runtime) {
        Say-Check $true 'runtime   ' $runtime.FullName
    } else {
        Say-Check $false 'runtime   ' "llama-server.exe missing under $installDir -- every generate will 500"
        $problems.Add('llama-server runtime missing')
    }

    # 5. serviceable ------------------------------------------------------------
    Push-Location $repoRoot
    try {
        $sentinel = & python -m fleet.ollama_sentinel --probe-generate 2>&1
        $serviceOk = ($LASTEXITCODE -eq 0)
    } catch {
        $sentinel = "sentinel failed to run: $($_.Exception.Message)"
        $serviceOk = $false
    } finally {
        Pop-Location
    }
    Say-Check $serviceOk 'serviceable' ($sentinel | Select-Object -First 1)
    if (-not $serviceOk) {
        $sentinel | Select-Object -Skip 1 | ForEach-Object { Write-Host "         $_" }
        $problems.Add('not serviceable')
    }

    Write-Host ('-' * 72)
    return $serviceOk
}

$before = Test-OllamaPosture

if ($Repair) {
    Write-Host ''
    if (-not (Test-Path $installer)) {
        Write-Host "Cannot repair: no cached installer at $installer" -ForegroundColor Red
        Write-Host 'Either install from ollama.com, or: winget upgrade --id Ollama.Ollama'
        exit 1
    }
    $sizeGb = [math]::Round((Get-Item $installer).Length / 1GB, 2)
    Write-Host "Repairing from the cached payload ($sizeGb GB, nothing downloaded):" -ForegroundColor Yellow
    Write-Host "  $installer"
    Write-Host '  Let it finish -- an interrupted update is the leading suspect for how'
    Write-Host '  lib\ollama was emptied in the first place.'
    Write-Host ''
    Start-Process -FilePath $installer -Wait
    Write-Host 'Installer exited. Re-checking...' -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    $after = Test-OllamaPosture
    if ($after) {
        Write-Host 'REPAIRED: Ollama is serviceable.' -ForegroundColor Green
        exit 0
    }
    Write-Host "STILL BROKEN: $($problems -join '; ')" -ForegroundColor Red
    exit 1
}

Write-Host ''
if ($before -and $problems.Count -eq 0) {
    Write-Host 'Ollama is healthy and on loopback.' -ForegroundColor Green
    exit 0
}

Write-Host "Outstanding: $($problems -join '; ')" -ForegroundColor Yellow
if ($problems -contains 'llama-server runtime missing') {
    Write-Host ''
    Write-Host 'To repair (runs the already-cached installer, downloads nothing):'
    Write-Host "  pwsh $PSCommandPath -Repair" -ForegroundColor Cyan
}
exit 1
