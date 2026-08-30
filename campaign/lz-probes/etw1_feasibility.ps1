<#
ETW1 - healthy-state feasibility + perturbation control for submission-cadence tracing.

REQUIRES ELEVATION. ETW session creation is privileged; a non-elevated shell gets
0x80070005 "Access is denied" (verified on OMEN 2026-08-30, token has neither
Administrators nor Performance Log Users).

This answers THREE questions and nothing else. It is deliberately a thin end-to-end
slice: if DxgKrnl emits nothing useful for this workload, the circular/triggered
recurrence machinery is not worth building.

  Q1  Are QueuePacket / DmaPacket events emitted at all for llama-server's Vulkan work?
  Q2  Does their cadence resolve individual decode iterations, or at least small
      stable groups of them?
  Q3  How much does the ETW session itself perturb the measured rate?

ACCEPTANCE CRITERIA, fixed before running:
  A1  ETW exposes a repeatable submission/execution cadence joinable to the request window.
  A2  Packet batching is acceptable ONLY if the grouping is stable enough that a
      degraded-vs-healthy comparison stays interpretable.
  A3  The ETW-on rate delta is MEASURED and carried as this probe's perturbation floor.
  A4  If ETW materially changes rate or cadence, it is NOT passive observation and no
      mechanism claim may rest on it.

Arms are INTERLEAVED (R3): control, etw, control, etw. A warm-up runs first so every
arm is equally cache-warm and prefill differences do not confound the decode comparison.

Touches nothing else: no restart, no keep-alive change, no config change.
#>
[CmdletBinding()]
param(
    [int]$Port = 8082,
    [int]$NPredict = 128,
    [string]$OutDir = "E:\work\battlemage\ff-probes\etw-20260830",
    [string]$PromptFile = "C:\work\commandcenter\campaign\lz-probes\kit\probe-prompt.txt"
)

$ErrorActionPreference = "Stop"
$probe = "C:\work\commandcenter\campaign\lz-probes\kit\probe_completion.py"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "BLOCKED: not elevated. ETW session creation returns 0x80070005 here." -ForegroundColor Red
    Write-Host "Run this from an elevated PowerShell, or host it in an S4U RunLevel=Highest task"
    Write-Host "the way ArcServeRestart / HearthGatewayRestart already are."
    exit 2
}
foreach ($f in @($probe, $PromptFile)) {
    if (-not (Test-Path $f)) { throw "missing required file: $f" }
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Invoke-Arm {
    param([string]$Label)
    $raw = & python $probe $Port $PromptFile $NPredict 2>&1
    $txt = ($raw | Out-String).Trim()
    try { $o = $txt | ConvertFrom-Json } catch { Write-Host "  $Label -> UNPARSEABLE: $txt"; return $null }
    Write-Host ("  {0,-14} decode={1,7} tok/s  prefill={2,7} tok/s  wall={3}s" -f `
        $Label, $o.decode_tps, $o.prefill_tps, $o.wall_s)
    return $o
}

Write-Host "warm-up (discarded; equalises prompt cache across arms)" -ForegroundColor DarkGray
Invoke-Arm "warmup" | Out-Null

$results = @()
$etls = @()
foreach ($i in 1, 2) {
    Write-Host "`n--- round $i ---" -ForegroundColor Cyan

    $c = Invoke-Arm "control-$i"
    if ($c) { $results += [pscustomobject]@{ arm = "control"; round = $i; decode = $c.decode_tps; prefill = $c.prefill_tps } }

    $etl = Join-Path $OutDir "etw1-round$i.etl"
    # WPR's built-in GPU profile: Microsoft chose the providers and keywords, which is
    # safer than hand-rolling DxgKrnl keyword bits and risking a firehose or silence.
    & wpr -start GPU -filemode | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "wpr -start GPU failed ($LASTEXITCODE)" }
    try {
        $e = Invoke-Arm "etw-$i"
        if ($e) { $results += [pscustomobject]@{ arm = "etw"; round = $i; decode = $e.decode_tps; prefill = $e.prefill_tps } }
    } finally {
        & wpr -stop $etl | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  wpr -stop returned $LASTEXITCODE" -ForegroundColor Yellow }
    }
    if (Test-Path $etl) {
        $etls += $etl
        Write-Host ("  trace: {0}  ({1:N1} MB)" -f $etl, ((Get-Item $etl).Length / 1MB))
    }
}

Write-Host "`n=== A3/A4: PERTURBATION FLOOR ===" -ForegroundColor Cyan
$ctl = @($results | Where-Object arm -eq "control" | Select-Object -ExpandProperty decode)
$etw = @($results | Where-Object arm -eq "etw"     | Select-Object -ExpandProperty decode)
if ($ctl.Count -and $etw.Count) {
    $mc = ($ctl | Measure-Object -Average).Average
    $me = ($etw | Measure-Object -Average).Average
    $delta = 100.0 * ($me - $mc) / $mc
    Write-Host ("  control decode {0:N2} tok/s   ETW-on decode {1:N2} tok/s   delta {2:+0.0;-0.0;0.0}%" -f $mc, $me, $delta)
    Write-Host ("  >>> PERTURBATION FLOOR = {0:N1}%. Any ETW-derived effect smaller than this is not resolvable." -f [math]::Abs($delta))
    if ([math]::Abs($delta) -gt 5.0) {
        Write-Host "  >>> A4 FAILS: ETW materially changes the rate. This is NOT passive observation." -ForegroundColor Red
    }
}

Write-Host "`n=== Q1/Q2: WHAT THE TRACE ACTUALLY CONTAINS ===" -ForegroundColor Cyan
Write-Host "  tracerpt -summary is used first because it counts events per provider without"
Write-Host "  decoding the whole ETL. WPT is NOT installed here (no xperf/wpa), so tracerpt"
Write-Host "  is the only decoder on this box."
foreach ($etl in $etls) {
    $sum = [IO.Path]::ChangeExtension($etl, ".summary.txt")
    $dmp = [IO.Path]::ChangeExtension($etl, ".dumpfile.xml")
    & tracerpt $etl -summary $sum -o $dmp -of XML -y | Out-Null
    Write-Host "`n  --- $(Split-Path $etl -Leaf) ---"
    if (Test-Path $sum) {
        Get-Content $sum | Select-String -Pattern "Dxgk|Queue|Dma|Total" |
            Select-Object -First 25 | ForEach-Object { "    " + $_.Line.Trim() }
    } else {
        Write-Host "    no summary produced" -ForegroundColor Yellow
    }
}

Write-Host "`n=== STILL TO DECIDE BY HAND, from the dumpfile ===" -ForegroundColor Yellow
Write-Host "  * Do QueuePacket/DmaPacket carry a pid/context attributable to llama-server?"
Write-Host "  * How many packets per request, and is that count STABLE across the arms?"
Write-Host "      $NPredict decode iterations per request. packets ~= iterations -> per-token cadence."
Write-Host "      packets << iterations -> the UMD batches; derive only the COARSER quantity the"
Write-Host "      trace licenses. Do NOT force a per-token reading onto batched packets."
Write-Host "  * Only if cadence is usable: build the circular-buffer recurrence capture, so the"
Write-Host "    deep probe acts as the DUMP TRIGGER and pre-transition history is preserved."
Write-Host "    A triggered START would systematically miss the onset."
