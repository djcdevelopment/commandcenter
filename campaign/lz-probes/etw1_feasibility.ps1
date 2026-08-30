<#
ETW1 - healthy-state feasibility + perturbation control for submission-cadence tracing.

REQUIRES ELEVATION. Launch with:
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<this>'
A non-elevated shell gets 0x80070005 at ETW session creation (verified on OMEN
2026-08-30 by two independent routes: logman create trace -ets, and
Register-ScheduledTask -RunLevel Highest).

This answers THREE questions and nothing else. If DxgKrnl emits nothing usable for
this workload, the circular/triggered recurrence machinery is not worth building.

  Q1  Are QueuePacket / DmaPacket events emitted at all for llama-server's Vulkan work?
  Q2  Is packet cadence fine-grained and STABLE enough to resolve individual decode
      iterations, or at least repeatable small groups of them?
  Q3  What perturbation does the ETW session itself introduce?

ACCEPTANCE RULES, pre-committed:
  A1  ETW must expose a repeatable submission/execution cadence ATTRIBUTABLE to the
      probe window. (Absolute request start/end timestamps are recorded for the join.)
  A2  Per-token resolution is NOT required, but batching must be stable enough that a
      healthy-vs-degraded comparison would stay meaningful.
  A3  The ETW-on timing/throughput delta is measured and carried as the PERTURBATION FLOOR.
  A4  If ETW materially changes throughput, timing or cadence, it is an ACTIVE
      PERTURBATION, not passive observation.
  A5  Do NOT infer per-token semantics merely because event counts look numerically
      convenient. Determine the coarsest execution unit the trace actually supports.

Shape: n_predict 32 by default, matching the keep-alive deep probe exactly, so the
control arm is directly comparable to tonight's entire 13-probe record. Everything else
held constant: same model, prompt, server process, epoch, placement.

Arms INTERLEAVED (R3): control, etw, control, etw. A discarded warm-up runs first so
every arm is equally prompt-cache-warm and prefill cannot confound decode.

Touches nothing else: no restart, no keep-alive change, no config change.
#>
[CmdletBinding()]
param(
    [int]$Port = 8082,
    [int]$NPredict = 32,
    [int]$Rounds = 2,
    [string]$OutDir = "E:\work\battlemage\ff-probes\etw-20260830",
    [string]$PromptFile = "C:\work\commandcenter\campaign\lz-probes\kit\probe-prompt.txt"
)

$ErrorActionPreference = "Continue"
$probe = "C:\work\commandcenter\campaign\lz-probes\kit\probe_completion.py"
$keepalive = "C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $OutDir "etw1-$stamp.log"
$json = Join-Path $OutDir "etw1-$stamp.json"
Start-Transcript -Path $log -Force | Out-Null

$report = [ordered]@{ started = (Get-Date).ToString("o"); port = $Port; n_predict = $NPredict; arms = @(); traces = @() }

try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    $elev = $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-Host "elevated=$elev  user=$($id.Name)  session=$((Get-Process -Id $PID).SessionId)"
    $report.elevated = $elev
    if (-not $elev) {
        Write-Host "BLOCKED: not elevated. ETW session creation returns 0x80070005 here." -ForegroundColor Red
        $report.result = "BLOCKED_NOT_ELEVATED"
        return
    }
    foreach ($f in @($probe, $PromptFile)) {
        if (-not (Test-Path $f)) { throw "missing required file: $f" }
    }

    function Invoke-Arm {
        param([string]$Label, [string]$Kind)
        $t0 = (Get-Date).ToUniversalTime()
        $raw = & python $probe $Port $PromptFile $NPredict 2>&1
        $t1 = (Get-Date).ToUniversalTime()
        $txt = ($raw | Out-String).Trim()
        $o = $null
        try { $o = $txt | ConvertFrom-Json } catch { Write-Host "  $Label UNPARSEABLE: $txt" -ForegroundColor Yellow }
        $rec = [ordered]@{
            label = $Label; kind = $Kind
            start_utc = $t0.ToString("o"); end_utc = $t1.ToString("o")
            start_epoch = [Math]::Round(($t0 - [datetime]'1970-01-01Z').TotalSeconds, 3)
            end_epoch = [Math]::Round(($t1 - [datetime]'1970-01-01Z').TotalSeconds, 3)
            decode_tps = $(if ($o) { $o.decode_tps }); prefill_tps = $(if ($o) { $o.prefill_tps })
            prompt_n = $(if ($o) { $o.prompt_n }); prompt_ms = $(if ($o) { $o.prompt_ms })
            wall_s = $(if ($o) { $o.wall_s })
        }
        if ($o) {
            $rec.wall_per_token_ms = [Math]::Round(1000.0 / [double]$o.decode_tps, 4)
            Write-Host ("  {0,-12} decode={1,7} tok/s  wall/token={2,7} ms  prompt_ms={3}" -f $Label, $o.decode_tps, $rec.wall_per_token_ms, $o.prompt_ms)
        }
        return $rec
    }

    Write-Host "`nwarm-up (discarded; equalises prompt cache across arms)" -ForegroundColor DarkGray
    Invoke-Arm "warmup" "discard" | Out-Null

    for ($i = 1; $i -le $Rounds; $i++) {
        Write-Host "`n--- round $i ---" -ForegroundColor Cyan
        $report.arms += Invoke-Arm "control-$i" "control"

        $etl = Join-Path $OutDir "etw1-$stamp-r$i.etl"
        # WPR's built-in GPU profile: Microsoft chose providers+keywords, safer than
        # hand-rolling DxgKrnl keyword bits and risking a firehose or silence.
        $tStart = (Get-Date).ToUniversalTime()
        & wpr -start GPU -filemode 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  wpr -start FAILED ($LASTEXITCODE); skipping etw arm" -ForegroundColor Red; continue }
        try {
            $report.arms += Invoke-Arm "etw-$i" "etw"
        } finally {
            & wpr -stop $etl 2>&1 | Out-Null
            $stopRc = $LASTEXITCODE
        }
        $tStop = (Get-Date).ToUniversalTime()
        $sz = if (Test-Path $etl) { (Get-Item $etl).Length } else { 0 }
        Write-Host ("  trace {0}  {1:N1} MB  session_wall={2:N1}s  stop_rc={3}" -f (Split-Path $etl -Leaf), ($sz / 1MB), ($tStop - $tStart).TotalSeconds, $stopRc)
        $report.traces += [ordered]@{ path = $etl; bytes = $sz; session_wall_s = [Math]::Round(($tStop - $tStart).TotalSeconds, 2); stop_rc = $stopRc }
    }

    # ---- A3/A4 perturbation floor ----
    Write-Host "`n=== A3/A4  PERTURBATION FLOOR ===" -ForegroundColor Cyan
    $ctl = @($report.arms | Where-Object { $_.kind -eq "control" -and $_.decode_tps } | ForEach-Object { [double]$_.decode_tps })
    $etw = @($report.arms | Where-Object { $_.kind -eq "etw" -and $_.decode_tps } | ForEach-Object { [double]$_.decode_tps })
    if ($ctl.Count -and $etw.Count) {
        $mc = ($ctl | Measure-Object -Average).Average
        $me = ($etw | Measure-Object -Average).Average
        $d = 100.0 * ($me - $mc) / $mc
        $report.perturbation = [ordered]@{ control_decode = [Math]::Round($mc, 2); etw_decode = [Math]::Round($me, 2); delta_pct = [Math]::Round($d, 2); control_n = $ctl.Count; etw_n = $etw.Count }
        Write-Host ("  control {0:N2} tok/s (n={1})   ETW-on {2:N2} tok/s (n={3})   delta {4:+0.0;-0.0;0.0}%" -f $mc, $ctl.Count, $me, $etw.Count, $d)
        Write-Host ("  >>> PERTURBATION FLOOR = {0:N1}%  - no ETW-derived effect smaller than this is resolvable." -f [Math]::Abs($d))
        if ([Math]::Abs($d) -gt 5.0) {
            Write-Host "  >>> A4 FAILS: ETW materially changes the rate. ACTIVE PERTURBATION, not passive observation." -ForegroundColor Red
            $report.a4 = "FAIL_ACTIVE_PERTURBATION"
        } else { $report.a4 = "PASS_within_5pct" }
    }

    # ---- contamination audit: keep-alive traffic during the run ----
    $t0 = [datetime]::Parse($report.arms[0].start_utc).ToUniversalTime()
    $t1 = [datetime]::Parse($report.arms[-1].end_utc).ToUniversalTime()
    $ka = @()
    if (Test-Path $keepalive) {
        Get-Content $keepalive -Tail 200 | ForEach-Object {
            try { $r = $_ | ConvertFrom-Json } catch { return }
            if ($r.ts) {
                $ts = ([datetime]$r.ts).ToUniversalTime()
                if ($ts -ge $t0.AddSeconds(-5) -and $ts -le $t1.AddSeconds(5)) {
                    $ka += [ordered]@{ ts = $r.ts; predicted_n = $r.predicted_n; prompt_ms = $r.prompt_ms; decode_tok_s = $r.decode_tok_s }
                }
            }
        }
    }
    $report.keepalive_during_run = $ka
    Write-Host "`n=== CONTAMINATION AUDIT: keep-alive requests inside the run window ===" -ForegroundColor Cyan
    Write-Host "  $($ka.Count) keep-alive request(s) landed during the arms. They share the server"
    Write-Host "  (-np 2), so any that overlapped an arm add contention to THAT arm only."
    $ka | ForEach-Object { Write-Host ("    {0}  predicted_n={1}  prompt_ms={2}  decode={3}" -f $_.ts, $_.predicted_n, $_.prompt_ms, $_.decode_tok_s) }

    # ---- Q1/Q2 what the trace contains ----
    Write-Host "`n=== Q1/Q2  WHAT THE TRACE ACTUALLY CONTAINS ===" -ForegroundColor Cyan
    Write-Host "  tracerpt -summary counts events per provider without decoding the whole ETL."
    Write-Host "  WPT is NOT installed here (no xperf/wpa), so tracerpt is the only decoder."
    foreach ($t in $report.traces) {
        if (-not (Test-Path $t.path)) { continue }
        $sum = [IO.Path]::ChangeExtension($t.path, ".summary.txt")
        $dmp = [IO.Path]::ChangeExtension($t.path, ".dump.xml")
        Write-Host "`n  --- $(Split-Path $t.path -Leaf) ---"
        & tracerpt $t.path -summary $sum -o $dmp -of XML -y 2>&1 | Out-Null
        $t.tracerpt_rc = $LASTEXITCODE
        if (Test-Path $sum) {
            $t.summary = $sum
            Get-Content $sum | Select-String -Pattern "Dxgk|Queue|Dma|Total|Event" |
                Select-Object -First 30 | ForEach-Object { "    " + $_.Line.Trim() }
        } else { Write-Host "    no summary produced (tracerpt rc=$LASTEXITCODE)" -ForegroundColor Yellow }
        if (Test-Path $dmp) { $t.dump = $dmp; $t.dump_bytes = (Get-Item $dmp).Length }
    }

    $report.result = "COMPLETED"
    Write-Host "`n=== A5 REMINDER, decide by hand from the dump ===" -ForegroundColor Yellow
    Write-Host "  $NPredict decode iterations per arm."
    Write-Host "  packets ~= iterations  -> per-token cadence may be supportable."
    Write-Host "  packets << iterations  -> the Intel UMD BATCHES. Carry the COARSER unit"
    Write-Host "                            explicitly; do NOT force a per-token reading."
    Write-Host "  Join test (A1): do packet timestamps fall inside the recorded start/end epochs?"
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    $report.result = "ERROR"; $report.error = $_.Exception.Message
}
finally {
    $report.finished = (Get-Date).ToString("o")
    $report | ConvertTo-Json -Depth 6 | Set-Content -Path $json -Encoding utf8
    Write-Host "`nreport: $json"
    Write-Host "log:    $log"
    Stop-Transcript | Out-Null
}
