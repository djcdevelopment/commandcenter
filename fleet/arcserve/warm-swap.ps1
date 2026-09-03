# warm-swap.ps1 -- the post-launch warm step for serve-arc.cmd under llama-swap (ADR-0045 / P13).
#
# llama-swap's on_startup preload brings production (qwen3-30b-a3b on :8082) resident, but a freshly
# loaded server still pays the ~11.5 s first-request stall and, if left idle >60 s, collapses to the
# ADR-0043 cold state. This script waits for the upstream to answer /health, then fires ONE 1-token
# completion so the rung is warm before the fx99 keep-alive's next tick. It never restarts anything.
#
# Runs in the background from serve-arc.cmd (which then blocks on llama-swap in the foreground, keeping
# the ArcServeBoot task 'Running' exactly as before). The bearer comes from the environment the launcher
# exported (LLAMA_API_KEY / OMEN_ARC_TOKEN); it is never printed.
param(
    [string]$SwapBase = "http://127.0.0.1:8081",
    [string]$Model = "qwen3-30b-a3b",
    [int]$DeadlineSec = 240,
    [string]$LogPath = "C:\work\commandcenter\hearth\var\arc-swap-warm.log"
)
$ErrorActionPreference = "Continue"
function Log([string]$m) {
    $line = "{0} warm-swap: {1}" -f (Get-Date).ToString("o"), $m
    try { Add-Content -Path $LogPath -Value $line -Encoding ASCII } catch {}
}
$token = $env:OMEN_ARC_TOKEN
if (-not $token) { $token = $env:LLAMA_API_KEY }
$headers = @{}
if ($token) { $headers["Authorization"] = "Bearer $token" }

$deadline = (Get-Date).AddSeconds($DeadlineSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$SwapBase/upstream/$Model/health" -TimeoutSec 10
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 3
}
if (-not $ready) { Log "upstream /health not 200 within ${DeadlineSec}s -- not warming (llama-swap log has the reason)"; exit 1 }

$body = @{ prompt = "ok"; n_predict = 1; temperature = 0; cache_prompt = $false } | ConvertTo-Json -Compress
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-RestMethod -Method Post -Uri "$SwapBase/upstream/$Model/completion" -Headers $headers `
        -ContentType "application/json" -Body $body -TimeoutSec 180
    $sw.Stop()
    $t = $resp.timings
    Log ("warm ok: wall={0}ms prompt_ms={1} predicted_n={2}" -f [math]::Round($sw.Elapsed.TotalMilliseconds, 1), $t.prompt_ms, $t.predicted_n)
    exit 0
} catch {
    $sw.Stop()
    Log ("warm FAILED after {0}ms: {1}" -f [math]::Round($sw.Elapsed.TotalMilliseconds, 1), $_.Exception.Message)
    exit 1
}
