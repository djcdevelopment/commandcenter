# LZ8 — iGPU-executed MoE experts (half-split, co-resident vertical slice).
# Cell A (reference): all experts CPU (this morning's config) -> greedy text + timings.
# Cell B (the probe): experts of blocks 24-47 resident+executed on the iGPU (Vulkan0,
# UMA/DDR5, no PCIe for weights, no CPU threads), blocks 0-23 stay CPU/file-backed.
# Correctness = greedy byte-diff A vs B. Decode is the target metric.
# Known wrinkle (recorded, not mitigated): with the iGPU visible, op-offload's prefill
# streaming targets Vulkan0 (first backend) instead of a B70 - prefill may drop; this
# lap's edge is decode.
#
# VERDICT 2026-08-28 (lz-receipts.jsonl, probe LZ8): cell B is QUANT-GATED on this
# hardware - the UD-IQ4_XS expert mix fails the Vulkan MMID shared-memory gate on the
# iGPU's 48KB SLM (ggml-vulkan.cpp:18152; iq-quant shaders carry codebook tables in SLM,
# Q4_K fits - the full Q4_K 30B-A3B runs on the iGPU at 13.05 tok/s). Default runs now
# execute cell A only and exit clean; pass -AttemptB to retry B (do this only once a
# Q4_K-expert requant of Flash exists, or after an engine bump that changes the gate).
param(
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl",
    [int]$CommitGateGB = 95,
    [switch]$AttemptB
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'kit\placement.ps1')
$bin = "E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
$logDir = "E:\work\battlemage\lz-probes"
$port = 18186
$model = "E:\work\battlemage\models\qwen38-flash-next\UD-IQ4_XS\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"

function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}
function Get-CommitGB {
    [math]::Round((Get-CimInstance Win32_PerfRawData_PerfOS_Memory).CommittedBytes / 1GB, 1)
}
function Stop-Probe {
    Get-Process llama-server -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $bin } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
function Start-Cell([string]$LogName, [string[]]$Roles, [string]$Expect, [string[]]$ExtraArgs) {
    Stop-Probe
    $pre = Get-CommitGB
    if ($pre -gt $CommitGateGB) { throw "commit gate: $pre GB > $CommitGateGB GB - refusing load (poisoned-load territory)" }
    # ADR-0042: indices are resolved by ROLE at run time, never hardcoded. A both-B70
    # cell takes NO filter at all (device-TYPE selection); only an iGPU cell needs one,
    # because type selection deliberately excludes integrated GPUs.
    if ($Roles -contains 'igpu') {
        $env:GGML_VK_VISIBLE_DEVICES = Get-DeviceFilterByRole -Roles $Roles
    } else {
        Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    }
    # -lv 5: without it there are no placement lines to assert against (see placement.ps1).
    $args = @("-m",$model,"--alias","lz8","-ngl","99","-sm","layer","-fa","on","-fit","off",
              "--no-repack","-c","16384","-np","1","-lv","5",
              "--host","127.0.0.1","--port",[string]$port,"--slots") + $ExtraArgs
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $bin -ArgumentList $args `
            -RedirectStandardError "$logDir\$LogName.err.log" `
            -RedirectStandardOutput "$logDir\$LogName.out.log" -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(420); $up = $false
    do { Start-Sleep -Seconds 3
         if ($p.HasExited) { break }
         try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
               if ($h.status -eq "ok") { $up = $true } } catch {}
    } while (-not $up -and (Get-Date) -lt $deadline)
    $sw.Stop()
    if (-not $up) { Get-Content "$logDir\$LogName.err.log" -Tail 12; throw "$LogName never healthy (loud refusal - read the log)" }
    # /health is liveness only (ADR-0041). Placement is asserted from the load report,
    # which is what actually catches an enumeration reshuffle.
    $null = Assert-Placement -LogPath "$logDir\$LogName.err.log" -Expect $Expect -Cell $LogName
    $post = Get-CommitGB
    "$LogName up: load $([math]::Round($sw.Elapsed.TotalSeconds,1))s  commit $pre -> $post GB"
    return @{ proc = $p; commit_pre = $pre; commit_post = $post; load_s = [math]::Round($sw.Elapsed.TotalSeconds,1) }
}
function Invoke-Greedy([string]$Cell) {
    # warm-up (untimed, pipeline compile) then 2 timed greedy reps; text saved for diff
    $null = python (Join-Path $PSScriptRoot 'lz8_greedy.py') $port "warmup" 8
    $rows = @()
    foreach ($rep in 1..2) {
        $line = python (Join-Path $PSScriptRoot 'lz8_greedy.py') $port $Cell 64
        $j = $line | ConvertFrom-Json
        $rows += $j
        # Write-Host, not pipeline: emitted strings would join the function's return
        # value and land as null-metric rows in the receipts ledger
        Write-Host "  rep ${rep}: prefill $($j.prefill_tps)  decode $($j.decode_tps) tok/s"
    }
    [IO.File]::WriteAllText("$logDir\lz8-$Cell.txt", $rows[0].content)
    return $rows
}

"--- pre-lap ---"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String
$ff = Get-Process ffmpeg -ErrorAction SilentlyContinue
"ffmpeg: $(if ($ff) { $ff.Count } else { 0 })  commit: $(Get-CommitGB) GB"

# ---- Cell A: reference, all experts CPU (identical to the morning's lz1 config) ----
$a = Start-Cell "lz8-A-cpu" @("b70","b70") "both-b70" @("-ts","1,1","-ot",".ffn_.*_exps.=CPU")
$aRows = Invoke-Greedy "A-cpu"
# receipts written PER CELL, immediately - a later cell's throw must not eat these rows
foreach ($j in $aRows) { Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8'; cell = 'A-cpu'; decode_tps = $j.decode_tps; prefill_tps = $j.prefill_tps; commit_pre = $a.commit_pre; commit_post = $a.commit_post; load_s = $a.load_s; coresident = $true } }
$aPre = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "A-cpu-512" 512 8 1
$aPre
Stop-Probe

# ---- Cell B: experts blk 24-47 -> iGPU (Vulkan0), blk 0-23 -> CPU ----
if (-not $AttemptB) {
    ""
    "cell B SKIPPED (known quant gate): UD-IQ4_XS expert tensors fail the Vulkan MMID"
    "shared-memory check on the iGPU's 48KB SLM (ggml-vulkan.cpp:18152). Verdict row is"
    "in lz-receipts.jsonl (probe LZ8, 2026-08-28). Re-attempt with -AttemptB once a"
    "Q4_K-expert Flash requant exists or after an engine bump."
} else {
    $ot = 'blk\.(2[4-9]|3[0-9]|4[0-7])\.ffn_.*_exps\.=Vulkan0,\.ffn_.*_exps\.=CPU'
    try {
        $b = Start-Cell "lz8-B-igpu" @("igpu","b70","b70") "igpu-plus-b70" @("-ts","0,1,1","-ot",$ot)
        # verify enumeration + placement out of the launch log, loudly
        Select-String -Path "$logDir\lz8-B-igpu.err.log" -Pattern 'ggml_vulkan.*=' | Select-Object -First 4 | ForEach-Object { $_.Line }
        $ovVk = (Select-String -Path "$logDir\lz8-B-igpu.err.log" -Pattern 'overridden.*Vulkan0' | Measure-Object).Count
        $ovCpu = (Select-String -Path "$logDir\lz8-B-igpu.err.log" -Pattern 'overridden' | Measure-Object).Count - $ovVk
        "tensor overrides: $ovVk -> Vulkan0 (expect 72 = 3 x 24 layers), $ovCpu -> CPU"
        $bRows = Invoke-Greedy "B-igpu"
        foreach ($j in $bRows) { Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8'; cell = 'B-igpu-half'; decode_tps = $j.decode_tps; prefill_tps = $j.prefill_tps; commit_pre = $b.commit_pre; commit_post = $b.commit_post; load_s = $b.load_s; overrides_vulkan0 = $ovVk; coresident = $true } }
        $bPre = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "B-igpu-512" 512 8 1
        $bPre
        $same = ((Get-FileHash "$logDir\lz8-A-cpu.txt").Hash -eq (Get-FileHash "$logDir\lz8-B-igpu.txt").Hash)
        "greedy outputs byte-identical: $same"
        if (-not $same) {
            "--- A ---"; [IO.File]::ReadAllText("$logDir\lz8-A-cpu.txt")
            "--- B ---"; [IO.File]::ReadAllText("$logDir\lz8-B-igpu.txt")
        }
        Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8'; cell = 'diff'; greedy_identical = $same }
    } catch {
        "cell B refused: $($_.Exception.Message)"
        "expected while the quant gate stands - see the LZ8 verdict row"
        Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8'; cell = 'B-igpu-half'; result_raw = 'REFUSED (re-attempt)'; detail = "$($_.Exception.Message)" }
    } finally {
        Stop-Probe
    }
}

"--- post-lap ---"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String
"commit: $(Get-CommitGB) GB"
