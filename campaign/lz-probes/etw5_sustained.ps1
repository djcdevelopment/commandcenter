<#
ETW5 - measure the two unknowns that gate the circular recorder. REQUIRES ELEVATION.

  U1  Sustained DxgKrnl-only bytes/sec under NORMAL traffic (keep-alive only, no probe
      requests of ours). This is the input to
          ring_size ~= measured_bytes_per_second * retention_seconds * 1.25
      The 7.5 MB/0.72 s figure from ETW1 is NOT this: that window was dominated by an
      active 32-token request. Extrapolating it to a 30-minute ring would be exactly the
      kind of extrapolation this campaign keeps punishing.

  U2  CONTINUOUS-session perturbation. The 0.68% floor was measured over ~0.5 s sessions.
      A session left running for minutes is a different animal - buffer pressure, flush
      behaviour, disk contention - and that number does not transfer.

Method: sample the keep-alive ledger BEFORE, run a DxgKrnl session for -Seconds through
normal traffic, sample the ledger DURING, and compare. We issue NO requests of our own;
the keep-alive's 30 s ping is the probe, and its prompt_ms is the sensitive instrument
(median 10.2 ms, ~4% spread over the night).

Writes a JSON result. Touches nothing else: no restart, no config change.
#>
[CmdletBinding()]
param(
    [int]$Seconds = 120,
    [string]$OutDir = "E:\work\battlemage\ff-probes\etw-20260830",
    [string]$Keepalive = "C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
)
$ErrorActionPreference = "Continue"
$DXGK = "Microsoft-Windows-DxgKrnl"
$sess = "lz_etw5_rate"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$etl = Join-Path $OutDir "etw5-$stamp.etl"
$json = Join-Path $OutDir "etw5-$stamp.json"
$log = Join-Path $OutDir "etw5-$stamp.log"
Start-Transcript -Path $log -Force | Out-Null

$r = [ordered]@{ started = (Get-Date).ToString("o"); seconds = $Seconds; provider = $DXGK }

function Read-KA {
    param([datetime]$From, [datetime]$To)
    $out = @()
    if (-not (Test-Path $Keepalive)) { return $out }
    Get-Content $Keepalive -Tail 300 | ForEach-Object {
        try { $o = $_ | ConvertFrom-Json } catch { return }
        if (-not $o.ts) { return }
        $ts = [datetime]$o.ts
        if ($ts -ge $From -and $ts -le $To) {
            $out += [ordered]@{ ts = $o.ts; prompt_ms = $o.prompt_ms; predicted_n = $o.predicted_n
                                decode_tok_s = $o.decode_tok_s; wall_ms = $o.wall_ms }
        }
    }
    return $out
}

try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "BLOCKED: not elevated." -ForegroundColor Red; $r.result = "BLOCKED"; return
    }
    $r.elevated = $true

    $now = Get-Date
    $before = Read-KA -From $now.AddSeconds(-$Seconds - 30) -To $now
    $r.keepalive_before = $before
    $bp = @($before | Where-Object { $_.prompt_ms } | ForEach-Object { [double]$_.prompt_ms })
    Write-Host ("BEFORE: {0} keep-alive rows, prompt_ms mean {1:N2} ms" -f $before.Count,
        $(if ($bp.Count) { ($bp | Measure-Object -Average).Average } else { [double]::NaN }))

    & logman stop $sess -ets 2>&1 | Out-Null
    $t0 = Get-Date
    $null = & logman create trace $sess -ets -p $DXGK 0xFFFFFFFFFFFFFFFF 5 -o $etl `
        -bs 64 -nb 32 128 -mode Sequential -max 4096 2>&1
    if ($LASTEXITCODE -ne 0) { throw "logman create failed ($LASTEXITCODE)" }
    Write-Host "session up; recording $Seconds s through normal keep-alive traffic..."
    Start-Sleep -Seconds $Seconds
    & logman stop $sess -ets 2>&1 | Out-Null
    $t1 = Get-Date

    $bytes = if (Test-Path $etl) { (Get-Item $etl).Length } else { 0 }
    $elapsed = ($t1 - $t0).TotalSeconds
    $bps = if ($elapsed -gt 0) { $bytes / $elapsed } else { 0 }
    $r.etl = $etl; $r.bytes = $bytes; $r.elapsed_s = [Math]::Round($elapsed, 2)
    $r.bytes_per_second = [Math]::Round($bps, 1)
    Write-Host ("`n=== U1 SUSTAINED RATE ===") -ForegroundColor Cyan
    Write-Host ("  {0:N0} bytes over {1:N1} s = {2:N2} MB/s" -f $bytes, $elapsed, ($bps / 1MB))

    Write-Host "`n  ring_size = bytes_per_second * retention * 1.25" -ForegroundColor Cyan
    $r.ring_sizing = @()
    foreach ($mins in 15, 25, 30, 45) {
        $mb = $bps * $mins * 60 * 1.25 / 1MB
        $r.ring_sizing += [ordered]@{ retention_min = $mins; ring_mb = [Math]::Round($mb, 0) }
        Write-Host ("    {0,2} min retention -> ring {1,8:N0} MB" -f $mins, $mb)
    }

    $during = Read-KA -From $t0 -To $t1
    $r.keepalive_during = $during
    $dp = @($during | Where-Object { $_.prompt_ms } | ForEach-Object { [double]$_.prompt_ms })
    Write-Host ("`n=== U2 CONTINUOUS-SESSION PERTURBATION ===") -ForegroundColor Cyan
    if ($bp.Count -and $dp.Count) {
        $mb2 = ($bp | Measure-Object -Average).Average
        $md = ($dp | Measure-Object -Average).Average
        $delta = 100.0 * ($md - $mb2) / $mb2
        $r.perturbation = [ordered]@{ before_prompt_ms = [Math]::Round($mb2, 3); during_prompt_ms = [Math]::Round($md, 3)
                                      delta_pct = [Math]::Round($delta, 2); before_n = $bp.Count; during_n = $dp.Count }
        Write-Host ("  prompt_ms before {0:N2} ms (n={1})   during {2:N2} ms (n={3})   delta {4:+0.0;-0.0;0.0}%" -f $mb2, $bp.Count, $md, $dp.Count, $delta)
        if ([Math]::Abs($delta) -gt 10.0) {
            Write-Host "  >>> CONTINUOUS SESSION MATERIALLY PERTURBS. Do not run it for hours." -ForegroundColor Red
            $r.u2 = "FAIL"
        } else { $r.u2 = "PASS_within_10pct"; Write-Host "  >>> within 10%: a continuous session is tolerable." }
    } else { Write-Host "  insufficient keep-alive rows to compare" -ForegroundColor Yellow; $r.u2 = "INSUFFICIENT" }
    $during | ForEach-Object { Write-Host ("    {0}  prompt_ms={1}  pred_n={2}  decode={3}" -f $_.ts, $_.prompt_ms, $_.predicted_n, $_.decode_tok_s) }
    $r.result = "COMPLETED"
}
catch { Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red; $r.result = "ERROR"; $r.error = $_.Exception.Message }
finally {
    $r.finished = (Get-Date).ToString("o")
    $r | ConvertTo-Json -Depth 6 | Set-Content -Path $json -Encoding utf8
    Write-Host "`nreport: $json"
    Stop-Transcript | Out-Null
}
