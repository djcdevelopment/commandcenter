<#
ETW1 - healthy-state feasibility + perturbation control for submission-cadence tracing.

REQUIRES ELEVATION:
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<this>'

Answers THREE questions and nothing else:
  Q1  Does DxgKrnl emit usable QueuePacket / DmaPacket events for this Vulkan/Intel workload?
  Q2  Is packet cadence fine-grained and STABLE enough to resolve individual decode
      iterations, or at least repeatable small groups of them?
  Q3  What perturbation does the ETW session itself introduce?

ACCEPTANCE RULES, pre-committed:
  A1  cadence must be ATTRIBUTABLE to the probe window (absolute UTC epochs + server PID recorded)
  A2  batching is fine only if the grouping is STABLE enough to keep healthy-vs-degraded meaningful
  A3  the ETW-on delta is MEASURED and carried as the perturbation floor
  A4  if ETW materially changes throughput/timing/cadence it is ACTIVE PERTURBATION, not observation
  A5  do NOT infer per-token semantics from numerically convenient counts

REVISION 2, informed by run 1 (04:26:46) which failed usefully:
  * run 1 fired probe_completion.py, which predates the server's bearer -> five HTTP 401s, so the
    traces captured an IDLE machine and answered nothing. Now uses etw1_probe.py (sends the bearer,
    never prints it).
  * run 1 used the kit's probe-prompt.txt = 29,313 tokens. Now uses etw1-prompt.txt (5 bytes), so
    the arm matches the keep-alive deep probe's shape and stays comparable to the night's record.
  * run 1 used `wpr -start GPU`, a FIREHOSE: 346 MB in ~2 s with the dGPUs idle, 3.55M events,
    4.4 GB of XML. Now captures Microsoft-Windows-DxgKrnl ALONE via logman: one provider, far less
    volume, far less perturbation, decodable output.

Touches nothing else: no restart, no keep-alive change, no config change.
#>
[CmdletBinding()]
param(
    [int]$Port = 8082,
    [int]$NPredict = 32,
    [int]$Rounds = 2,
    [string]$OutDir = "E:\work\battlemage\ff-probes\etw-20260830",
    [string]$PromptFile = "C:\work\commandcenter\campaign\lz-probes\etw1-prompt.txt",
    [int]$DumpMaxMB = 250
)

$ErrorActionPreference = "Continue"
$probe = "C:\work\commandcenter\campaign\lz-probes\etw1_probe.py"
$keepalive = "C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
$DXGK = "Microsoft-Windows-DxgKrnl"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $OutDir "etw1-$stamp.log"
$json = Join-Path $OutDir "etw1-$stamp.json"
Start-Transcript -Path $log -Force | Out-Null

$report = [ordered]@{ started = (Get-Date).ToString("o"); rev = 2; port = $Port; n_predict = $NPredict; provider = $DXGK; arms = @(); traces = @() }

try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    $elev = $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-Host "elevated=$elev  user=$($id.Name)  session=$((Get-Process -Id $PID).SessionId)"
    $report.elevated = $elev
    if (-not $elev) { Write-Host "BLOCKED: not elevated." -ForegroundColor Red; $report.result = "BLOCKED"; return }
    foreach ($f in @($probe, $PromptFile)) { if (-not (Test-Path $f)) { throw "missing: $f" } }

    # Prompt-size guard. Run 1's 29k-token prompt injected a fake degraded episode into the
    # observation record. Never fire an unverified prompt at production again.
    $pbytes = (Get-Item $PromptFile).Length
    Write-Host "prompt file: $PromptFile ($pbytes bytes)"
    $report.prompt_bytes = $pbytes
    if ($pbytes -gt 4096) { throw "PROMPT TOO LARGE ($pbytes bytes). Refusing: this is how run 1 contaminated the record." }

    # Server PID, for A1 attribution of packets.
    $srvPid = $null
    try { $srvPid = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess } catch {}
    $report.server_pid = $srvPid
    if ($srvPid) { Write-Host "server pid on :$Port = $srvPid ($((Get-Process -Id $srvPid -ErrorAction SilentlyContinue).ProcessName))" }

    function Invoke-Arm {
        param([string]$Label, [string]$Kind)
        $t0 = (Get-Date).ToUniversalTime()
        $raw = & python $probe $Port $PromptFile $NPredict 2>&1
        $t1 = (Get-Date).ToUniversalTime()
        $txt = ($raw | Out-String).Trim()
        $o = $null
        try { $o = $txt | ConvertFrom-Json } catch { Write-Host "  $Label UNPARSEABLE: $txt" -ForegroundColor Yellow }
        if ($o -and $o.error) { Write-Host "  $Label SERVER ERROR: $($o.error) (auth_sent=$($o.auth_sent))" -ForegroundColor Red; $o = $null }
        $rec = [ordered]@{
            label = $Label; kind = $Kind
            start_utc = $t0.ToString("o"); end_utc = $t1.ToString("o")
            # [datetime]'1970-01-01Z' parses as LOCAL in PS 5.1 and silently offset every
            # epoch by 8 h in run 2. Use DateTimeOffset, which cannot be misread.
            start_epoch = [Math]::Round(([DateTimeOffset]$t0).ToUnixTimeMilliseconds() / 1000.0, 3)
            end_epoch = [Math]::Round(([DateTimeOffset]$t1).ToUnixTimeMilliseconds() / 1000.0, 3)
            decode_tps = $(if ($o) { $o.decode_tps }); prefill_tps = $(if ($o) { $o.prefill_tps })
            prompt_n = $(if ($o) { $o.prompt_n }); prompt_ms = $(if ($o) { $o.prompt_ms })
            predicted_n = $(if ($o) { $o.predicted_n }); predicted_ms = $(if ($o) { $o.predicted_ms })
            wall_s = $(if ($o) { $o.wall_s })
        }
        if ($o) {
            $rec.wall_per_token_ms = [Math]::Round([double]$o.predicted_ms / [double]$o.predicted_n, 4)
            Write-Host ("  {0,-12} decode={1,7} tok/s  wall/token={2,7} ms  prompt_n={3}  prompt_ms={4}" -f $Label, $o.decode_tps, $rec.wall_per_token_ms, $o.prompt_n, $o.prompt_ms)
        }
        return $rec
    }

    Write-Host "`nwarm-up (discarded)" -ForegroundColor DarkGray
    Invoke-Arm "warmup" "discard" | Out-Null

    for ($i = 1; $i -le $Rounds; $i++) {
        Write-Host "`n--- round $i ---" -ForegroundColor Cyan
        $report.arms += Invoke-Arm "control-$i" "control"

        $etl = Join-Path $OutDir "etw1-$stamp-r$i.etl"
        $sess = "lz_etw1_r$i"
        & logman stop $sess -ets 2>&1 | Out-Null   # in case a prior run left it
        $tStart = (Get-Date).ToUniversalTime()
        $null = & logman create trace $sess -ets -p $DXGK 0xFFFFFFFFFFFFFFFF 5 -o $etl -bs 64 -nb 32 128 -mode Sequential -max 512 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Host "  logman create FAILED ($LASTEXITCODE); skipping etw arm" -ForegroundColor Red; continue }
        try {
            $report.arms += Invoke-Arm "etw-$i" "etw"
        } finally {
            & logman stop $sess -ets 2>&1 | Out-Null
            $stopRc = $LASTEXITCODE
        }
        $tStop = (Get-Date).ToUniversalTime()
        $sz = if (Test-Path $etl) { (Get-Item $etl).Length } else { 0 }
        Write-Host ("  trace {0}  {1:N2} MB  session_wall={2:N1}s  stop_rc={3}" -f (Split-Path $etl -Leaf), ($sz / 1MB), ($tStop - $tStart).TotalSeconds, $stopRc)
        $report.traces += [ordered]@{ path = $etl; bytes = $sz; session_wall_s = [Math]::Round(($tStop - $tStart).TotalSeconds, 2); stop_rc = $stopRc }
    }

    # ---- A3/A4 perturbation floor ----
    Write-Host "`n=== A3/A4  PERTURBATION FLOOR ===" -ForegroundColor Cyan
    $ctl = @($report.arms | Where-Object { $_.kind -eq "control" -and $_.decode_tps } | ForEach-Object { [double]$_.decode_tps })
    $etw = @($report.arms | Where-Object { $_.kind -eq "etw" -and $_.decode_tps } | ForEach-Object { [double]$_.decode_tps })
    if ($ctl.Count -and $etw.Count) {
        $mc = ($ctl | Measure-Object -Average).Average; $me = ($etw | Measure-Object -Average).Average
        $d = 100.0 * ($me - $mc) / $mc
        $report.perturbation = [ordered]@{ control_decode = [Math]::Round($mc, 2); etw_decode = [Math]::Round($me, 2); delta_pct = [Math]::Round($d, 2); control_n = $ctl.Count; etw_n = $etw.Count }
        Write-Host ("  control {0:N2} tok/s (n={1})   ETW-on {2:N2} tok/s (n={3})   delta {4:+0.0;-0.0;0.0}%" -f $mc, $ctl.Count, $me, $etw.Count, $d)
        Write-Host ("  >>> PERTURBATION FLOOR = {0:N1}%" -f [Math]::Abs($d))
        if ([Math]::Abs($d) -gt 5.0) { Write-Host "  >>> A4 FAILS: ACTIVE PERTURBATION, not passive observation." -ForegroundColor Red; $report.a4 = "FAIL" }
        else { $report.a4 = "PASS_within_5pct" }
    } else { Write-Host "  cannot compute: missing arm results" -ForegroundColor Yellow }

    # ---- contamination audit ----
    $t0 = [datetime]::Parse($report.arms[0].start_utc).ToUniversalTime()
    $t1 = [datetime]::Parse($report.arms[-1].end_utc).ToUniversalTime()
    $ka = @()
    if (Test-Path $keepalive) {
        Get-Content $keepalive -Tail 60 | ForEach-Object {
            try { $r = $_ | ConvertFrom-Json } catch { return }
            if ($r.ts) { $ts = ([datetime]$r.ts).ToUniversalTime()
                if ($ts -ge $t0.AddSeconds(-5) -and $ts -le $t1.AddSeconds(5)) {
                    $ka += [ordered]@{ ts = $r.ts; predicted_n = $r.predicted_n; prompt_ms = $r.prompt_ms; decode_tok_s = $r.decode_tok_s } } }
        }
    }
    $report.keepalive_during_run = $ka
    Write-Host "`n=== CONTAMINATION AUDIT ===" -ForegroundColor Cyan
    Write-Host "  $($ka.Count) keep-alive request(s) inside the run window (shared server, -np 2)"
    $ka | ForEach-Object { Write-Host ("    {0}  predicted_n={1}  prompt_ms={2}  decode={3}" -f $_.ts, $_.predicted_n, $_.prompt_ms, $_.decode_tok_s) }

    # ---- Q1/Q2 decode the traces ----
    Write-Host "`n=== Q1/Q2  TRACE CONTENTS ===" -ForegroundColor Cyan
    foreach ($t in $report.traces) {
        if (-not (Test-Path $t.path)) { continue }
        $sum = [IO.Path]::ChangeExtension($t.path, ".summary.txt")
        Write-Host "`n  --- $(Split-Path $t.path -Leaf) ---"
        & tracerpt $t.path -summary $sum -o nul -of XML -y 2>&1 | Out-Null
        if (Test-Path $sum) {
            $t.summary = $sum
            Get-Content $sum | Select-String -Pattern "Total Events|Total Buffers|Events  Lost|DxgKrnl" |
                Select-Object -First 40 | ForEach-Object { "    " + $_.Line.Trim() }
        }
        if (($t.bytes / 1MB) -le $DumpMaxMB) {
            $dmp = [IO.Path]::ChangeExtension($t.path, ".dump.xml")
            & tracerpt $t.path -o $dmp -of XML -y 2>&1 | Out-Null
            if (Test-Path $dmp) { $t.dump = $dmp; $t.dump_bytes = (Get-Item $dmp).Length
                Write-Host ("    dump: {0:N1} MB" -f ((Get-Item $dmp).Length / 1MB)) }
        } else { Write-Host "    dump SKIPPED (etl > $DumpMaxMB MB)" -ForegroundColor Yellow }
    }

    $report.result = "COMPLETED"
}
catch { Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red; $report.result = "ERROR"; $report.error = $_.Exception.Message }
finally {
    $report.finished = (Get-Date).ToString("o")
    $report | ConvertTo-Json -Depth 6 | Set-Content -Path $json -Encoding utf8
    Write-Host "`nreport: $json"
    Stop-Transcript | Out-Null
}
