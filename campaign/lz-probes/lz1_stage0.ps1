# LZ1 Stage 0 — op-offload MECHANISM probes on the Flash experts-on-host operating point.
# Co-resident with production by design (mechanism, not clean throughput). Three cells +
# two diagnostic launches. Signed prediction for cell B: 22-token prefill REGRESSES to
# ~3-5 tok/s when GGML_OP_OFFLOAD_MIN_BATCH=16 arms offload below the prompt size.
param(
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl",
    [switch]$SkipDiagnostics
)
$ErrorActionPreference = 'Stop'
$kit = Join-Path $PSScriptRoot 'kit'
. (Join-Path $kit 'placement.ps1')
$bin = "E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
$logDir = "E:\work\battlemage\lz-probes"
$port = 18184
# -lv 5 is MANDATORY, not diagnostic noise: at the default verbosity llama-server emits
# no "using device" / "model buffer size" lines at all, so placement cannot be asserted.
# Every LZ log written before 2026-08-29 lacks them, which is how a single-card campaign
# went unnoticed (ADR-0042).
$modelArgs = @("-m","E:\work\battlemage\models\qwen38-flash-next\UD-IQ4_XS\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf",
               "--alias","lz1-flash","-ngl","99","-sm","layer","-ts","1,1","-fa","on","-fit","off",
               "-ot",".ffn_.*_exps.=CPU","--no-repack","-c","16384","-np","1",
               "-lv","5",
               "--host","127.0.0.1","--port",[string]$port,"--slots")

function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}

function Start-Flash([string]$MinBatch, [string]$LogName, [string[]]$ExtraEnv) {
    Get-Process llama-server -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $bin } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    # ADR-0042: NO index filter. With none set, ggml-vulkan selects by device TYPE
    # (ggml-vulkan.cpp:7479-7495, "all dedicated GPUs") and llama.cpp drops the iGPU at
    # placement -- order-independent, so a reshuffle cannot break it. The old
    # GGML_VK_VISIBLE_DEVICES="1,2" here resolved to [B70, iGPU] in a scheduled-task
    # context and silently cost a whole card.
    Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    if ($MinBatch) { $env:GGML_OP_OFFLOAD_MIN_BATCH = $MinBatch }
    else { Remove-Item Env:GGML_OP_OFFLOAD_MIN_BATCH -ErrorAction SilentlyContinue }
    Remove-Item Env:GGML_SCHED_DEBUG, Env:GGML_VK_PERF_LOGGER -ErrorAction SilentlyContinue
    foreach ($e in $ExtraEnv) { $k, $v = $e -split '=', 2; Set-Item "Env:$k" $v }
    $perf0 = Get-CimInstance Win32_PerfRawData_PerfOS_Memory
    $pre = [math]::Round($perf0.CommittedBytes / 1GB, 1)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $bin -ArgumentList $modelArgs `
            -RedirectStandardError "$logDir\$LogName.err.log" `
            -RedirectStandardOutput "$logDir\$LogName.out.log" -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(300); $up = $false
    do { Start-Sleep -Seconds 3
         if ($p.HasExited) { break }
         try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
               if ($h.status -eq "ok") { $up = $true } } catch {}
    } while (-not $up -and (Get-Date) -lt $deadline)
    $sw.Stop()
    if (-not $up) { Get-Content "$logDir\$LogName.err.log" -Tail 8; throw "$LogName never healthy" }
    # Liveness is NOT health and NOT placement (ADR-0041/0042). /health returning ok only
    # says a port answered; assert what actually took the weights before trusting a timing.
    $null = Assert-Placement -LogPath "$logDir\$LogName.err.log" -Expect both-b70 -Cell $LogName
    $perf1 = Get-CimInstance Win32_PerfRawData_PerfOS_Memory
    "$LogName up: load $([math]::Round($sw.Elapsed.TotalSeconds,1))s commit $pre -> $([math]::Round($perf1.CommittedBytes/1GB,1)) GB"
    return $p
}

function Invoke-Cell([string]$Cell, [string]$MinBatch, [int]$Tokens) {
    $p = Start-Flash $MinBatch "lz1-$Cell" @()
    # warm-up eval (untimed - pipeline compile) then 2 timed reps, unique prompts, no cache
    $null = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "warmup" 16 4 1
    $rows = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port $Cell $Tokens 8 2
    $rows
    foreach ($line in $rows) {
        $j = $line | ConvertFrom-Json
        Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ1-stage0'; cell = $Cell; rep = $j.rep;
                       min_batch = $(if ($MinBatch) { [int]$MinBatch } else { 32 });
                       prompt_n = $j.prompt_n; prefill_tps = $j.prefill_tps; decode_tps = $j.decode_tps;
                       coresident = $true }
    }
    Stop-Process -Id $p.Id -Force; Start-Sleep -Seconds 3
}

"--- pre-lap state ---"
$ff = Get-Process ffmpeg -ErrorAction SilentlyContinue
"render processes (ffmpeg): $(if ($ff) { $ff.Count } else { 0 })"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String

Invoke-Cell "A-22tok-default" $null 22
Invoke-Cell "B-22tok-minbatch16" "16" 22
Invoke-Cell "C-512tok-default" $null 512

if (-not $SkipDiagnostics) {
    # D: scheduler dump - proof of engagement is MUL_MAT_ID exps ops tagged "1.off" on Vulkan0
    $p = Start-Flash $null "lz1-D-scheddebug" @("GGML_SCHED_DEBUG=2")
    $null = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "sched-debug" 512 4 1
    Stop-Process -Id $p.Id -Force; Start-Sleep -Seconds 3
    $off = (Select-String -Path "$logDir\lz1-D-scheddebug.err.log" -Pattern '1\.off' -SimpleMatch -ErrorAction SilentlyContinue | Measure-Object).Count
    $offExps = (Select-String -Path "$logDir\lz1-D-scheddebug.err.log" -Pattern 'exps' | Where-Object { $_.Line -match '1\.off' } | Measure-Object).Count
    "sched-debug: $off lines tagged 1.off, $offExps of them expert tensors"
    Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ1-stage0'; cell = 'D-sched-debug';
                   off_tagged_lines = $off; off_tagged_exps = $offExps }
    # E: Vulkan perf logger - MUL_MAT_ID GPU timings during a 512-token prefill
    $p = Start-Flash $null "lz1-E-perflog" @("GGML_VK_PERF_LOGGER=1")
    $null = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "perf-logger" 512 4 1
    Stop-Process -Id $p.Id -Force; Start-Sleep -Seconds 3
    "perf-logger MUL_MAT_ID lines (top 10):"
    Select-String -Path "$logDir\lz1-E-perflog.err.log" -Pattern 'MUL_MAT_ID' | Select-Object -First 10 | ForEach-Object { $_.Line }
}

"--- post-lap production check ---"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String
