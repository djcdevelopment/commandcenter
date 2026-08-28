# LZ2 decomposition — replicate the llama.cpp slot-save file term in isolation:
# 2.68 GB written in <=64 MiB buffered WriteFile chunks (src\llama-mmap.cpp:152-167 shape),
# then read back the same way. residual = measured save/restore - this file term.
# Read-back is page-cache-warm (no unelevated way to flush); labeled as such.
param(
    [string]$TargetDir = "E:\work\battlemage\lz-probes\kv-nvme",
    [long]$Bytes = 2882743596,   # exact size of the P2 slot file
    [int]$Reps = 3,
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl"
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
$path = Join-Path $TargetDir "lz2-filewrite-replica.bin"
$chunk = 64MB
$buf = New-Object byte[] $chunk
(New-Object Random 42).NextBytes($buf)

function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}

foreach ($rep in 1..$Reps) {
    # llama-faithful variant: buffered WriteFile + close, NO flush-to-disk (the real save
    # path never calls FlushFileBuffers - Windows writes back lazily). This is the file
    # term the 1.74s receipt actually paid, and approximates a RAM-backed target.
    $fs = [IO.File]::Open($path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $done = [long]0
    while ($done -lt $Bytes) {
        $n = [int][math]::Min([long]$chunk, [long]($Bytes - $done))
        $fs.Write($buf, 0, $n)
        $done += $n
    }
    $fs.Close()
    $sw.Stop()
    $wNoFlushS = [math]::Round($sw.Elapsed.TotalSeconds, 3)

    # disk-honest variant: same write + Flush(true) before close
    $fs = [IO.File]::Open($path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $done = [long]0
    while ($done -lt $Bytes) {
        $n = [int][math]::Min([long]$chunk, [long]($Bytes - $done))
        $fs.Write($buf, 0, $n)
        $done += $n
    }
    $fs.Flush($true)
    $fs.Close()
    $sw.Stop()
    $wS = [math]::Round($sw.Elapsed.TotalSeconds, 3)

    $fs = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($fs.Read($buf, 0, $chunk) -gt 0) {}
    $fs.Close()
    $sw.Stop()
    $rS = [math]::Round($sw.Elapsed.TotalSeconds, 3)

    $gbW = [math]::Round($Bytes / $wS / 1e9, 2); $gbR = [math]::Round($Bytes / $rS / 1e9, 2)
    $gbNF = [math]::Round($Bytes / $wNoFlushS / 1e9, 2)
    "rep ${rep}: write-noflush $wNoFlushS s ($gbNF GB/s)  write-flush $wS s ($gbW GB/s)  read-warm $rS s ($gbR GB/s)  -> $TargetDir"
    Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ2-filewrite'; target = $TargetDir; rep = $rep;
                   write_noflush_s = $wNoFlushS; write_noflush_gbps = $gbNF;
                   write_flush_s = $wS; write_flush_gbps = $gbW;
                   read_warm_s = $rS; read_warm_gbps = $gbR }
}
Remove-Item $path -Force
