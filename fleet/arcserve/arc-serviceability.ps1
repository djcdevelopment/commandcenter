# arc-serviceability.ps1 - the omen-arc zombie tripwire (ADR-0032 lesson applied to
# the new rung): /health answering is exactly what a zombie also does, so the probe
# is a REAL 1-token completion. Exit 0 = serviceable; 1 = up-but-cannot-serve
# (zombie); 2 = down/unreachable (ArcServeBoot's RestartCount owns that case).
# Usable standalone, from a watchdog, or as a doorcheck adjunct.
param(
    [string]$Endpoint = 'http://127.0.0.1:8082',
    [int]$TimeoutS = 60
)
$ErrorActionPreference = 'Continue'
# token: same gitignored fragment the gateway and serve-arc.cmd use
$gw = 'C:\work\commandcenter\hearth\var\gateway.cmd'
$token = $null
if (Test-Path $gw) {
    $line = Select-String -Path $gw -Pattern '^\s*set\s+OMEN_ARC_TOKEN=(.+)$' | Select-Object -First 1
    if ($line) { $token = $line.Matches[0].Groups[1].Value.Trim() }
}

try {
    $h = Invoke-WebRequest -Uri "$Endpoint/health" -UseBasicParsing -TimeoutSec 5
    if ($h.StatusCode -ne 200) { Write-Output "DOWN: /health $($h.StatusCode)"; exit 2 }
} catch { Write-Output "DOWN: /health unreachable ($($_.Exception.Message))"; exit 2 }

$headers = @{}
if ($token) { $headers['Authorization'] = "Bearer $token" }
try {
    $body = '{"prompt":"ok","n_predict":1}'
    $r = Invoke-WebRequest -Uri "$Endpoint/completion" -Method Post -Body $body `
        -ContentType 'application/json' -Headers $headers -UseBasicParsing -TimeoutSec $TimeoutS
    $j = $r.Content | ConvertFrom-Json
    if ($null -ne $j.tokens_predicted -and $j.tokens_predicted -ge 1) {
        Write-Output "SERVICEABLE: 1-token generate ok ($([math]::Round($j.timings.predicted_per_second,1)) tok/s)"
        exit 0
    }
    Write-Output "ZOMBIE: completion returned no tokens"; exit 1
} catch {
    Write-Output "ZOMBIE: /health up but completion failed ($($_.Exception.Message))"
    exit 1
}
