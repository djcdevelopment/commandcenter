param(
    [Parameter(Mandatory=$true)][string]$Label,
    [Parameter(Mandatory=$true)][string]$ModelPath,
    [string]$Device = "1",
    [int]$Port = 18160
)
$ErrorActionPreference = 'Stop'
$bin = "E:\work\llamacpp-knee\build\bin\llama-server.exe"
$sp = "C:\Users\derek\AppData\Local\Temp\claude\C--work-commandcenter\a58fb70c-0863-4e23-8ae7-78e99128084d\scratchpad"
$outDir = "E:\work\battlemage\rotation-phase1"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$receipts = Join-Path $outDir "r2-receipts.jsonl"

function Invoke-Trial([string]$variant, [string[]]$loadFlags) {
    $mem = Get-CimInstance Win32_PerfRawData_PerfOS_Memory
    $standbyPre = [math]::Round(($mem.StandbyCacheNormalPriorityBytes + $mem.StandbyCacheReserveBytes + $mem.StandbyCacheCoreBytes)/1GB, 1)
    $args = @("-m", $ModelPath, "--alias", "r2-$Label", "-ngl", "99", "-fa", "on") + $loadFlags + @("-fit", "off", "-c", "16384", "-np", "1", "--host", "127.0.0.1", "--port", [string]$Port, "--slots")
    $env:GGML_VK_VISIBLE_DEVICES = $Device
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $bin -ArgumentList $args -RedirectStandardError "$outDir\r2-$Label-$variant.err.log" -RedirectStandardOutput "$outDir\r2-$Label-$variant.out.log" -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(180); $up = $false
    do {
        Start-Sleep -Milliseconds 500
        try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2; if ($h.status -eq "ok") { $up = $true } } catch {}
    } while (-not $up -and (Get-Date) -lt $deadline)
    $sw.Stop()
    $loadS = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    $canary = $null
    if ($up) {
        $raw = & python "$sp\probe_openai.py" $Port "r2-$Label" 64 2>$null
        try { $canary = $raw | ConvertFrom-Json } catch {}
    }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $row = [ordered]@{
        ts = (Get-Date).ToString('o'); probe = "R2"; label = $Label; variant = $variant
        model = Split-Path $ModelPath -Leaf; device = $Device
        healthy = $up; load_to_health_s = $loadS
        canary_decode_tps = if ($canary) { $canary.decode_tps } else { $null }
        canary_prefill_tps = if ($canary) { $canary.prefill_tps } else { $null }
        standby_cache_gb_pre = $standbyPre
    }
    ($row | ConvertTo-Json -Compress) | Add-Content -Path $receipts -Encoding utf8
    "{0,-22} load_s={1,-7} decode_tps={2}" -f "$Label/$variant", $loadS, $(if ($canary) { $canary.decode_tps } else { "FAIL" })
}

foreach ($i in 1..3) { Invoke-Trial "dio-cold-$i" @("--no-mmap", "-dio") }
Invoke-Trial "mmap-cold-1" @()
foreach ($i in 1..3) { Invoke-Trial "mmap-warm-$i" @() }
"DONE $Label"
