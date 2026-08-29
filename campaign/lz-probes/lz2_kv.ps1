# LZ2 — KV slot save/restore reps against a canary 30B server (exact W0 P2 argv, card 1).
# Baseline: -SavePath on NVMe. RAM-disk rung: -SavePath T:\kv\ (volume must already exist).
# Coexists with production (:8082 untouched); ~2 min of card-1 prefill to fill the slot.
param(
    [string]$SavePath = "E:\work\battlemage\lz-probes\kv-nvme\",
    [string]$LabelSuffix = "nvme",
    [int]$Port = 18150,
    [int]$Reps = 3,
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl"
)
$ErrorActionPreference = 'Stop'
$kit = Join-Path $PSScriptRoot 'kit'
. (Join-Path $kit 'placement.ps1')
$bin = "E:\work\llamacpp-knee\build\bin\llama-server.exe"
New-Item -ItemType Directory -Force -Path $SavePath | Out-Null
$logDir = "E:\work\battlemage\lz-probes"

function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}

# ADR-0042: this cell wants ONE card, but "one card" cannot be named by index -- the
# old GGML_VK_VISIBLE_DEVICES="1" meant whichever device happened to be second in THIS
# process's enumeration, which is not stable and was sometimes the iGPU. Say it with a
# split proportion instead: no filter (device-TYPE selection picks both B70s), then
# -ts 1,0 puts every layer on one of them. We do not control WHICH physical card gets
# them -- only that it is a single B70 -- and that is all this cell requires.
Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
$args = @("-m","E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
          "--alias","lz2-qwen30b","-ngl","99","-sm","layer","-ts","1,0",
          "-fa","on","--no-mmap","-dio","-fit","off",
          "-c","32768","-np","1","-lv","5","--host","127.0.0.1","--port",[string]$Port,
          "--slots","--slot-save-path",$SavePath)
$p = Start-Process -FilePath $bin -ArgumentList $args `
        -RedirectStandardError "$logDir\lz2-$LabelSuffix-server.log" `
        -RedirectStandardOutput "$logDir\lz2-$LabelSuffix-server.out.log" -PassThru -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds(120); $up = $false
do { Start-Sleep -Seconds 2
     try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
           if ($h.status -eq "ok") { $up = $true } } catch {}
} while (-not $up -and (Get-Date) -lt $deadline)
if (-not $up) { Get-Content "$logDir\lz2-$LabelSuffix-server.log" -Tail 8; throw "server never healthy" }
# /health said a port answered. Assert what actually holds the weights (ADR-0042).
$null = Assert-Placement -LogPath "$logDir\lz2-$LabelSuffix-server.log" -Expect one-b70 -Cell "lz2-$LabelSuffix"
"server up (pid $($p.Id)) save path $SavePath"

# fill slot 0 with the proven ~29K-token corpus
# ReadAllText, not Get-Content -Raw: PS5.1 attaches ETS members to the string and
# ConvertTo-Json then emits {"value":...} instead of a JSON string -> server 400s.
$prompt = [IO.File]::ReadAllText((Join-Path $kit 'probe-prompt.txt'))
$body = @{ prompt = $prompt; n_predict = 8; cache_prompt = $true; id_slot = 0; temperature = 0 } | ConvertTo-Json -Compress
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/completion" -Method Post `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" -TimeoutSec 600
$sw.Stop()
"fill: prompt_n=$($r.timings.prompt_n) in $([math]::Round($sw.Elapsed.TotalSeconds,1))s"

foreach ($rep in 1..$Reps) {
    $fn = "lz2-$LabelSuffix-rep$rep.bin"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/slots/0?action=save" -Method Post `
            -Body ('{"filename":"' + $fn + '"}') -ContentType "application/json" -TimeoutSec 300
    $sw.Stop(); $saveS = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    $sz = [math]::Round((Get-Item (Join-Path $SavePath $fn)).Length / 1GB, 2)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/slots/0?action=restore" -Method Post `
            -Body ('{"filename":"' + $fn + '"}') -ContentType "application/json" -TimeoutSec 300
    $sw.Stop(); $restS = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    "rep ${rep}: save $saveS s  restore $restS s  ($sz GB)"
    Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ2-kv'; target = $LabelSuffix; rep = $rep;
                   save_s = $saveS; restore_s = $restS; file_gb = $sz; prompt_n = $r.timings.prompt_n }
    if ($rep -lt $Reps) { Remove-Item (Join-Path $SavePath $fn) -Force }
}
Stop-Process -Id $p.Id -Force
"done; server stopped. Note: in-place restores (no server restart) - P3's 1.19s baseline included a restart-fresh process; comparable but labeled."
