# LZ8b — 30B-A3B hybrid venue matrix (co-resident, no window).
# H1: experts -> iGPU (Vulkan0, UMA/DDR5 — passes the MMID gate: Q4_K experts fit 48KB
#     SLM), attention/router/KV -> B70 #1 (Vulkan1). Measures the cross-device hop tax.
# H2: experts -> CPU, attention on the same B70 (the classic -ot twin; the board's
#     "30B-A3B same trick" follow-up).
# Comparators already on ledger: full-iGPU 13.05 tok/s (LZ8 disambig), full-B70 solo
# ~111 (r2 canary), dual-split production ~104.
# Hypothesis: H1 decode ~20-35 tok/s (expert bytes ~1.0-1.1 GB/token vs ~36.5 GB/s UMA,
# minus 96 staged activation hops/token) — beats full-iGPU if the hop tax is smaller
# than attention-on-iGPU's cost. Byte-identity across venues is NOT expected (reduction
# order); the check is coherent greedy text.
param(
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl",
    [int]$CommitGateGB = 95
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'kit\placement.ps1')
$bin = "E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
$logDir = "E:\work\battlemage\lz-probes"
$port = 18187
$model = "E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"

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
    if ($pre -gt $CommitGateGB) { throw "commit gate: $pre GB > $CommitGateGB GB - refusing load" }
    # ADR-0042: roles, not indices. Only the iGPU cell needs a filter at all -- device-TYPE
    # selection deliberately excludes integrated GPUs, so H1 cannot reach its venue without
    # one. H2 wants a single B70, which is said with -ts 1,0 rather than by naming a device.
    if ($Roles -contains 'igpu') {
        $env:GGML_VK_VISIBLE_DEVICES = Get-DeviceFilterByRole -Roles $Roles
    } else {
        Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    }
    # -lv 5: without it there are no placement lines to assert against (see placement.ps1).
    $args = @("-m",$model,"--alias","lz8b","-ngl","99","-sm","layer","-fa","on","-fit","off",
              "--no-repack","-c","16384","-np","1","-lv","5",
              "--host","127.0.0.1","--port",[string]$port,"--slots") + $ExtraArgs
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $bin -ArgumentList $args `
            -RedirectStandardError "$logDir\$LogName.err.log" `
            -RedirectStandardOutput "$logDir\$LogName.out.log" -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(300); $up = $false
    do { Start-Sleep -Seconds 3
         if ($p.HasExited) { break }
         try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
               if ($h.status -eq "ok") { $up = $true } } catch {}
    } while (-not $up -and (Get-Date) -lt $deadline)
    $sw.Stop()
    if (-not $up) { Get-Content "$logDir\$LogName.err.log" -Tail 12; throw "$LogName never healthy (loud refusal - read the log)" }
    # /health is liveness only (ADR-0041). Placement is asserted from the load report.
    $null = Assert-Placement -LogPath "$logDir\$LogName.err.log" -Expect $Expect -Cell $LogName
    $post = Get-CommitGB
    Write-Host "$LogName up: load $([math]::Round($sw.Elapsed.TotalSeconds,1))s  commit $pre -> $post GB"
    return @{ proc = $p; commit_pre = $pre; commit_post = $post; load_s = [math]::Round($sw.Elapsed.TotalSeconds,1) }
}
function Invoke-Cell([string]$Cell, [hashtable]$Info, [int]$OvExpect, [string]$OvTarget, [string]$LogName) {
    $ov = (Select-String -Path "$logDir\$LogName.err.log" -Pattern "overridden.*$OvTarget" | Measure-Object).Count
    Write-Host "tensor overrides -> ${OvTarget}: $ov (expect $OvExpect)"
    $null = python (Join-Path $PSScriptRoot 'lz8_greedy.py') $port "warmup" 8
    $rows = @()
    foreach ($rep in 1..2) {
        $line = python (Join-Path $PSScriptRoot 'lz8_greedy.py') $port $Cell 64
        $j = $line | ConvertFrom-Json
        $rows += $j
        Write-Host "  rep ${rep}: prefill $($j.prefill_tps)  decode $($j.decode_tps) tok/s"
        Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8b'; cell = $Cell; rep = $rep;
                       decode_tps = $j.decode_tps; prefill_tps = $j.prefill_tps;
                       commit_pre = $Info.commit_pre; commit_post = $Info.commit_post;
                       load_s = $Info.load_s; overrides = $ov; coresident = $true }
    }
    [IO.File]::WriteAllText("$logDir\lz8b-$Cell.txt", $rows[0].content)
    $pre512 = python (Join-Path $PSScriptRoot 'lz_prefill_probe.py') $port "$Cell-512" 512 8 1
    Write-Host ($pre512 | Out-String).Trim()
    foreach ($line in $pre512) {
        $j = $line | ConvertFrom-Json
        Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ8b'; cell = "$Cell-512"; prompt_n = $j.prompt_n;
                       prefill_tps = $j.prefill_tps; decode_tps = $j.decode_tps; coresident = $true }
    }
}

"--- pre-lap ---"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String
$ff = Get-Process ffmpeg -ErrorAction SilentlyContinue
"ffmpeg: $(if ($ff) { $ff.Count } else { 0 })  commit: $(Get-CommitGB) GB"

# H1: experts -> iGPU (Vulkan0), everything else -> B70#1 (Vulkan1)
# --device is LOAD-BEARING: llama.cpp excludes iGPU-type devices from the default device
# list when a dGPU is present, so without it the -ot'd Vulkan0 buffers are orphaned and
# the sched aborts at the first expert leaf (ggml-backend.cpp:932) - the same failure
# previously misattributed to an MMID/SLM quant gate in LZ8 (corrected on the ledger).
$h1 = Start-Cell "lz8b-H1-igpu" @("igpu","b70") "igpu-plus-b70" @("--device","Vulkan0,Vulkan1","-ts","0,1","-ot",".ffn_.*_exps.=Vulkan0")
Invoke-Cell "H1-exps-igpu" $h1 144 "Vulkan0" "lz8b-H1-igpu"
Stop-Probe

# H2: experts -> CPU, same B70 (twin)
$h2 = Start-Cell "lz8b-H2-cpu" @("b70") "one-b70" @("-ts","1,0","-ot",".ffn_.*_exps.=CPU")
Invoke-Cell "H2-exps-cpu" $h2 144 "CPU" "lz8b-H2-cpu"
Stop-Probe

"greedy texts (coherence check, byte-identity not expected across venues):"
"--- H1 ---"; [IO.File]::ReadAllText("$logDir\lz8b-H1-exps-igpu.txt")
"--- H2 ---"; [IO.File]::ReadAllText("$logDir\lz8b-H2-exps-cpu.txt")

"--- post-lap ---"
& "C:\work\commandcenter\fleet\arcserve\arc-serviceability.ps1" 2>&1 | Out-String
"commit: $(Get-CommitGB) GB"
