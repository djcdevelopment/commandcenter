<#
ETW6 session lifecycle - THE ONLY PRIVILEGED COMPONENT. REQUIRES ELEVATION.

Creates/starts (or stops) a CIRCULAR DxgKrnl session. It does nothing else: detection,
analysis and snapshot orchestration all live in the UNELEVATED watcher (etw6_watch.py),
so production is not redesigned around privilege.

Measured inputs (ETW5, 2026-08-30, 120 s through normal keep-alive traffic):
  * sustained UNFILTERED rate  9.70 MB/s  (1,224,605,696 B / 120.39 s)
  * continuous-session perturbation: RETIRED. The +7% figure (n=5) was refuted by ETW8:
    prompt_ms 10.40 -> 10.40, +0.0%, n=117 vs n=90. Decode was separately -9.2% across
    the same boundary but is UNATTRIBUTED (R10 in mirror), and two in-session probes at
    107.70 / 105.37 refute a constant tax.

Ring sizing, per the agreed formula:
    ring = bytes_per_second * retention_seconds * 1.25
    9.70 MB/s * 1500 s (15 min pre + 10 min post) * 1.25 = ~18 GB
E: has ~2.2 TB free, so this is affordable and the ring is deliberately sized from the
UNFILTERED rate as a conservative upper bound.

KEYWORD FILTER. The events the licensed observable needs -- DmaPacket/Info eid 450
(submit) and 451 (complete) -- both carry keyword 0x4000000000000001 on the
Microsoft-Windows-DxgKrnl/Performance channel, verified against the ETW1 dump. They are
only 2.14% of all DxgKrnl events; eids 105/106 (Profiler Start/Stop) alone are 35.6% and
are not needed. Filtering to the Base keyword drops ~70% of events, which lowers both
perturbation and analysis cost. Because the ring is sized from the unfiltered rate, the
filter simply makes it hold MORE history than the 25-minute minimum.

Usage:  etw6_session.ps1 -Start | -Stop | -Status
#>
[CmdletBinding()]
param(
    [switch]$Start, [switch]$Stop, [switch]$Status,
    [string]$OutDir = "E:\work\battlemage\ff-probes\etw-recorder",
    [int]$RingMB = 18432,
    [string]$Keywords = "0x4000000000000001",
    [int]$Level = 5
)
$ErrorActionPreference = "Continue"
$SESS = "lz_dxgk_ring"
$PROV = "Microsoft-Windows-DxgKrnl"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$etl = Join-Path $OutDir "$SESS.etl"
$manifest = Join-Path $OutDir "session-manifest.json"

if ($Status) {
    $q = (logman query $SESS -ets) 2>&1
    $q | ForEach-Object { $_ }
    if (Test-Path $etl) { "{0}  {1:N1} MB" -f $etl, ((Get-Item $etl).Length / 1MB) }
    exit 0
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "BLOCKED: not elevated. ETW session lifecycle requires elevation (0x80070005)." -ForegroundColor Red
    exit 2
}

if ($Stop) {
    & logman stop $SESS -ets 2>&1 | ForEach-Object { $_ }
    Write-Host "stopped $SESS"
    exit 0
}

if ($Start) {
    & logman stop $SESS -ets 2>&1 | Out-Null   # idempotent
    Remove-Item $etl -Force -ErrorAction SilentlyContinue

    # CAPTURE IDENTITY. These traces will be read many ADRs from now; without this a
    # binary ETL is uninterpretable.
    $srvPid = $null
    try { $srvPid = (Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess } catch {}
    $proc = if ($srvPid) { Get-Process -Id $srvPid -ErrorAction SilentlyContinue } else { $null }
    $cfg = "$PROV|$Keywords|$Level|Circular|$RingMB"
    $sha = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($cfg))
    ).Replace("-", "").Substring(0, 16)

    $null = & logman create trace $SESS -ets -p $PROV $Keywords $Level -o $etl `
        -bs 64 -nb 64 256 -mode Circular -max $RingMB 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "logman create FAILED ($LASTEXITCODE)" -ForegroundColor Red; exit 1 }

    $m = [ordered]@{
        session = $SESS; provider = $PROV; keywords = $Keywords; level = $Level
        mode = "Circular"; ring_mb = $RingMB
        ring_horizon_note = "sized from the UNFILTERED 9.70 MB/s measured 2026-08-30 x 1500 s x 1.25; the Base-keyword filter makes actual horizon LONGER"
        etl = $etl
        started_utc = (Get-Date).ToUniversalTime().ToString("o")
        server_pid = $srvPid
        server_start_utc = $(if ($proc) { $proc.StartTime.ToUniversalTime().ToString("o") } else { $null })
        server_process = $(if ($proc) { $proc.ProcessName } else { $null })
        baseline_epoch = "rate-baselines.json baseline_decode_tok_s 106.00 (ADR-0044: epoch-scoped reference, NOT capacity)"
        etw_config_sha256_16 = $sha
        analyzer_commit = (git -C C:\work\commandcenter rev-parse --short HEAD 2>$null)
        healthy_floor = @{
            union_mean_depth = 3.9075; union_median_depth = 4
            union_f_depth0 = 0.1180; union_f_depth_ge3 = 0.7347
            longest_zero_ms = 1.29; agreement_band_depth = 0.005; agreement_band_pp = 0.0025
            source = "ETW4, two identical healthy arms, GPU-active span"
        }
    }
    $m | ConvertTo-Json -Depth 6 | Set-Content -Path $manifest -Encoding utf8
    Write-Host "started $SESS  ring=$RingMB MB  keywords=$Keywords  cfg=$sha"
    Write-Host "manifest: $manifest"
    exit 0
}

Write-Host "specify -Start, -Stop or -Status"
exit 1

