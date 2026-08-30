# W-A — SOLO CONTROLS. The venue matrix, measured with NO incumbent at all.
#
# Why this window exists. Every venue-matrix number on the ledger was taken
# co-resident with a production server whose topology was unknown (ADR-0042: it was
# running 49/49 layers on ONE card for an unknown number of days) and whose health was
# never gated (ADR-0041: a co-tenant drives the incumbent to ~0.27x and only a restart
# clears it). Exactly one solo receipt exists in the whole campaign. So the matrix rests
# on a foundation that two ADRs from the same day marked as unprovable.
#
# With production DOWN there is no incumbent to poison and no gate to pass. The
# co-residency gate is replaced by a single stronger assertion: *nothing else is holding
# a GPU*, taken from ff_census.py before the first cell and after the last.
#
# ONE BINARY FOR EVERY CELL (E:\work\llamacpp-qwen38). Flash requires the fork -- the
# knee build does not know the qwen4exp architecture (finding A11) -- so a mixed-binary
# matrix would confound the venue comparison with a build comparison. Cell S2K measures
# that cross-binary delta once, deliberately, on the one config both builds can run.
#
# ADR-0042 applies throughout: no index filter except the iGPU cell, which cannot reach
# its venue any other way (device-TYPE selection deliberately keeps only dedicated GPUs).
# There the index is discovered at run time, recorded, and -- the part that actually
# catches a reshuffle -- the resulting placement is asserted from the server's own load
# report before any timing is kept.
param(
    [string]$Receipts = "E:\work\battlemage\ff-probes\ff-receipts.jsonl",
    [int]$CommitGateGB = 100,
    [string[]]$Only = @()
)
$ErrorActionPreference = 'Stop'
. "C:\work\commandcenter\campaign\lz-probes\kit\placement.ps1"

$qwen38 = "E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
$knee   = "E:\work\llamacpp-knee\build\bin\llama-server.exe"
$logDir = "E:\work\battlemage\ff-probes\wa-solo"
$probes = "C:\work\commandcenter\campaign\lz-probes"
$port   = 18191
$m30    = "E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
$mFlash = "E:\work\battlemage\models\qwen38-flash-next\UD-IQ4_XS\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$script:Rows = @()
function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}
function Get-CommitGB { [math]::Round((Get-CimInstance Win32_PerfRawData_PerfOS_Memory).CommittedBytes / 1GB, 1) }

function Assert-NoIncumbent {
    # The solo window's whole premise. A production server that came back on its own --
    # the ArcServeBoot task has a boot trigger -- would silently turn every cell below
    # into another co-resident measurement, which is the exact defect this window exists
    # to repair. So it is checked, not assumed.
    $l = netstat -ano | Select-String ":8082 .*LISTENING"
    if ($l) { throw "NOT SOLO: something is listening on 8082 -- production came back. Window void." }
    $stray = Get-Process llama-server -ErrorAction SilentlyContinue |
             Where-Object { $_.Id -ne $script:CurrentPid }
    if ($stray) { throw "NOT SOLO: foreign llama-server pid(s) $($stray.Id -join ','). Window void." }
}

function Stop-Probe {
    # WAIT for the exit, do not assume it. A fixed 4 s sleep was not enough for the Flash
    # server: with ~88 GB of experts mmap'd, teardown outlived the sleep and the NEXT
    # cell's Assert-NoIncumbent saw the dying process as a foreign tenant and voided the
    # window. The guard was right to fail closed; the stop was wrong to be impatient.
    if ($script:CurrentPid) {
        $target = $script:CurrentPid
        $script:CurrentPid = $null
        Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Process -Id $target -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
        }
        if (Get-Process -Id $target -ErrorAction SilentlyContinue) {
            throw "probe pid $target did not exit within 120s - refusing to start another cell beside it"
        }
        Start-Sleep -Seconds 2
    }
}

function Start-Cell {
    param(
        [Parameter(Mandatory)][string]$Cell,
        [Parameter(Mandatory)][string]$Bin,
        [Parameter(Mandatory)][string]$Model,
        [Parameter(Mandatory)][string]$Expect,
        [string[]]$ExtraArgs = @(),
        [string[]]$Roles = @(),
        [int]$LoadTimeoutSec = 420
    )
    $script:CurrentPid = $null
    Assert-NoIncumbent
    $pre = Get-CommitGB
    if ($pre -gt $CommitGateGB) { throw "commit gate: $pre GB > $CommitGateGB GB - refusing to load $Cell" }

    if ($Roles -contains 'igpu') {
        $env:GGML_VK_VISIBLE_DEVICES = Get-DeviceFilterByRole -Roles $Roles
        $script:EnumFilter = $env:GGML_VK_VISIBLE_DEVICES
    } else {
        Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
        $script:EnumFilter = $null
    }
    Remove-Item Env:GGML_OP_OFFLOAD_MIN_BATCH, Env:GGML_SCHED_DEBUG, Env:GGML_VK_PERF_LOGGER -ErrorAction SilentlyContinue

    # -lv 5 is mandatory: at the default verbosity there are NO placement lines to assert
    # against, and their absence is indistinguishable from a healthy load (ADR-0042).
    $args = @("-m",$Model,"--alias","wa-$Cell","-ngl","99","-sm","layer","-fa","on","-fit","off",
              "--no-repack","-c","16384","-np","1","-lv","5",
              "--host","127.0.0.1","--port",[string]$port,"--slots") + $ExtraArgs
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $Bin -ArgumentList $args `
            -RedirectStandardError "$logDir\$Cell.err.log" `
            -RedirectStandardOutput "$logDir\$Cell.out.log" -PassThru -WindowStyle Hidden
    $script:CurrentPid = $p.Id
    $deadline = (Get-Date).AddSeconds($LoadTimeoutSec); $up = $false
    do { Start-Sleep -Seconds 3
         if ($p.HasExited) { break }
         try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
               if ($h.status -eq "ok") { $up = $true } } catch {}
    } while (-not $up -and (Get-Date) -lt $deadline)
    $sw.Stop()
    if (-not $up) {
        Write-Host "  --- last 15 log lines ---"
        Get-Content "$logDir\$Cell.err.log" -Tail 15 -ErrorAction SilentlyContinue
        throw "$Cell never became healthy within ${LoadTimeoutSec}s (loud refusal - read the log)"
    }
    # /health is liveness only. Placement is the gate.
    $pl = Assert-Placement -LogPath "$logDir\$Cell.err.log" -Expect $Expect -Cell $Cell
    $post = Get-CommitGB
    Write-Host "  $Cell up: load $([math]::Round($sw.Elapsed.TotalSeconds,1))s  commit $pre -> $post GB"
    return @{ commit_pre = $pre; commit_post = $post; load_s = [math]::Round($sw.Elapsed.TotalSeconds,1);
              placement = $pl; expect = $Expect; bin = $Bin }
}

function Invoke-Decode([string]$Cell, [hashtable]$Info, [int]$Reps = 2) {
    $null = python "$probes\lz8_greedy.py" $port "warmup" 8
    foreach ($rep in 1..$Reps) {
        $j = (python "$probes\lz8_greedy.py" $port $Cell 64) | ConvertFrom-Json
        Write-Host "    decode rep ${rep}: prefill $($j.prefill_tps)  decode $($j.decode_tps) tok/s (prompt_n=$($j.prompt_n))"
        $row = @{ ts = (Get-Date).ToString('o'); probe = 'W-A-SOLO'; cell = $Cell; measure = 'decode64';
                  rep = $rep; prompt_n = $j.prompt_n; prefill_tps = $j.prefill_tps;
                  decode_tps = $j.decode_tps; predicted_n = $j.predicted_n;
                  binary = $Info.bin; placement_assertion = $Info.expect;
                  placement_evidence = $Info.placement.BufferSummary;
                  placement_devices = $Info.placement.Summary;
                  enum_filter = $script:EnumFilter;
                  commit_pre = $Info.commit_pre; commit_post = $Info.commit_post; load_s = $Info.load_s;
                  coresident = $false; incumbent_running = $false;
                  receipt_status = 'SOLO_CONTROL';
                  receipt_status_reason = 'production stopped for the whole window; no other GPU consumer per FF-CENSUS' }
        Add-Receipt $row
        $script:Rows += $row
    }
    [IO.File]::WriteAllText("$logDir\$Cell.greedy.txt",
        ((python "$probes\lz8_greedy.py" $port "$Cell-text" 64) | ConvertFrom-Json).content)
}

function Invoke-Prefill([string]$Cell, [hashtable]$Info, [int]$Tokens, [int]$Reps = 2) {
    # Untimed warm-up FIRST. The very first eval on a fresh server pays pipeline
    # compilation, and it lands entirely in rep 1: S1's opening 22-token probe read
    # 2.16 tok/s against 9.93 on the next rep of the identical request. LZ1 warmed up
    # for exactly this reason; omitting it here would have published a cold number as
    # the solo Flash prefill figure.
    $null = python "$probes\lz_prefill_probe.py" $port "warmup" $Tokens 4 1
    foreach ($line in (python "$probes\lz_prefill_probe.py" $port "$Cell-p$Tokens" $Tokens 8 $Reps)) {
        $j = $line | ConvertFrom-Json
        Write-Host "    prefill@${Tokens} rep $($j.rep): prompt_n=$($j.prompt_n) prefill $($j.prefill_tps) tok/s ($($j.prefill_ms) ms)  decode $($j.decode_tps)"
        $row = @{ ts = (Get-Date).ToString('o'); probe = 'W-A-SOLO'; cell = $Cell; measure = "prefill$Tokens";
                  rep = $j.rep; prompt_n = $j.prompt_n; prefill_tps = $j.prefill_tps; prefill_ms = $j.prefill_ms;
                  decode_tps = $j.decode_tps; predicted_n = $j.predicted_n;
                  binary = $Info.bin; placement_assertion = $Info.expect;
                  placement_evidence = $Info.placement.BufferSummary;
                  placement_devices = $Info.placement.Summary;
                  enum_filter = $script:EnumFilter;
                  commit_pre = $Info.commit_pre; commit_post = $Info.commit_post; load_s = $Info.load_s;
                  coresident = $false; incumbent_running = $false;
                  receipt_status = 'SOLO_CONTROL';
                  receipt_status_reason = 'production stopped for the whole window; no other GPU consumer per FF-CENSUS' }
        Add-Receipt $row
        $script:Rows += $row
    }
}

function Should-Run([string]$Cell) { return ($Only.Count -eq 0 -or $Only -contains $Cell) }

# ---------------------------------------------------------------------------
try {
    "=== W-A SOLO CONTROLS ===  commit at start: $(Get-CommitGB) GB"
    Assert-NoIncumbent
    "no incumbent: confirmed (8082 free, no foreign llama-server)"

    # S1 -- Flash, experts->CPU, attention on both B70s. LZ1-A argv verbatim.
    # This is the cell the signed prediction was written against; solo re-scores it as
    # "does the 10.6 solo receipt reproduce?", which is the cleaner question anyway.
    if (Should-Run 'S1') {
        "`n--- S1: Flash-Next experts->CPU (LZ1-A argv), both B70 ---"
        $i = Start-Cell -Cell 'S1-flash-expcpu' -Bin $qwen38 -Model $mFlash -Expect 'both-b70' `
             -ExtraArgs @("-ts","1,1","-ot",".ffn_.*_exps.=CPU") -LoadTimeoutSec 900
        Invoke-Prefill 'S1-flash-expcpu' $i 22 2
        Invoke-Prefill 'S1-flash-expcpu' $i 512 2
        Invoke-Decode  'S1-flash-expcpu' $i 2
        Stop-Probe
    }

    # S2 -- 30B-A3B dual-split across both B70s. The top of the venue matrix.
    if (Should-Run 'S2') {
        "`n--- S2: 30B-A3B full dual-split B70 (qwen38 build) ---"
        $i = Start-Cell -Cell 'S2-30b-dual' -Bin $qwen38 -Model $m30 -Expect 'both-b70' `
             -ExtraArgs @("-ts","1,1")
        Invoke-Decode  'S2-30b-dual' $i 2
        Invoke-Prefill 'S2-30b-dual' $i 512 2
        Stop-Probe
    }

    # S2K -- the same config on the knee build. Not a venue: this is the cross-binary
    # control that keeps the one-binary matrix honest, and it is the only cell that can
    # be compared directly to production's own numbers.
    if (Should-Run 'S2K') {
        "`n--- S2K: 30B-A3B full dual-split B70 (KNEE build - cross-binary control) ---"
        $i = Start-Cell -Cell 'S2K-30b-dual-knee' -Bin $knee -Model $m30 -Expect 'both-b70' `
             -ExtraArgs @("-ts","1,1")
        Invoke-Decode  'S2K-30b-dual-knee' $i 2
        Invoke-Prefill 'S2K-30b-dual-knee' $i 512 2
        Stop-Probe
    }

    # S3 -- 30B-A3B experts->CPU on ONE B70. The CPU-expert seat (~22.7-24.1 on record).
    if (Should-Run 'S3') {
        "`n--- S3: 30B-A3B experts->CPU, one B70 ---"
        $i = Start-Cell -Cell 'S3-30b-expcpu' -Bin $qwen38 -Model $m30 -Expect 'one-b70' `
             -ExtraArgs @("-ts","1,0","-ot",".ffn_.*_exps.=CPU")
        Invoke-Decode  'S3-30b-expcpu' $i 2
        Invoke-Prefill 'S3-30b-expcpu' $i 512 2
        Stop-Probe
    }

    # S5 -- 30B-A3B on ONE B70, all layers. Not a new venue: this is the dual-vs-single
    # control, and it is the cell the campaign never actually had. The "121.6 full-B70"
    # comparator turns out to be `tensor_split: "1.00"` -- a SINGLE-CARD llama-bench
    # tg128 run, mislabeled as dual-split (finding A4: llama-bench splits -ts on [;/]+
    # and reads a comma as its config separator, so `-ts 1,1` there means two separate
    # single-card runs). llama-bench also has no -np, so it cannot express a serving
    # topology at all. Measuring it on the server, solo, is the only way to compare.
    if (Should-Run 'S5') {
        "`n--- S5: 30B-A3B single-card B70 (dual-vs-single control) ---"
        $i = Start-Cell -Cell 'S5-30b-single' -Bin $qwen38 -Model $m30 -Expect 'one-b70' `
             -ExtraArgs @("-ts","1,0")
        Invoke-Decode  'S5-30b-single' $i 2
        Invoke-Prefill 'S5-30b-single' $i 512 2
        Stop-Probe
    }

    # S4 -- 30B-A3B entirely on the iGPU (~13.05 on record). The one cell that needs an
    # index filter; see the header and Get-DeviceFilterByRole for why that is allowed here.
    if (Should-Run 'S4') {
        "`n--- S4: 30B-A3B full iGPU ---"
        $i = Start-Cell -Cell 'S4-30b-igpu' -Bin $qwen38 -Model $m30 -Expect 'igpu-only' `
             -Roles @('igpu') -LoadTimeoutSec 600
        Invoke-Decode  'S4-30b-igpu' $i 2
        Invoke-Prefill 'S4-30b-igpu' $i 512 2
        Stop-Probe
    }
}
finally {
    Stop-Probe
    Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    "`n=== summary ==="
    $script:Rows | ForEach-Object {
        "{0,-22} {1,-11} rep{2}  prompt_n={3,-5} prefill={4,-8} decode={5}" -f `
            $_.cell, $_.measure, $_.rep, $_.prompt_n, $_.prefill_tps, $_.decode_tps
    }
    "commit at end: $(Get-CommitGB) GB"
    "receipts -> $Receipts"
}
