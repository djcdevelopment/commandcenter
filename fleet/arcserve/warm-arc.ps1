# warm-arc.ps1 — keep the omen-arc rung out of its cold state, and record what it cost.
#
# WHY THIS EXISTS (ADR-0043). Left idle for more than ~60 s the rung transitions to a
# stable degraded state ~3.9x down (106.5 -> 39.5 at 120 s idle, flat ~27.5 after), and
# the FIRST request after an idle gap pays a prefill stall of ~11 SECONDS regardless of
# prompt size — 10 735 ms to prefill 11 tokens, measured twice, while decode on that same
# request was a healthy 96.7 tok/s. A 1-token request every 20 s was measured to hold the
# rate at 104.83 tok/s, indistinguishable from a freshly loaded server.
#
# WHY IT LIVES HERE BUT IS DRIVEN FROM FX99. A keep-alive that runs on the box it is
# keeping alive dies with that box. FX99 (`ai-1`, 192.168.12.220) owns the schedule and
# invokes this script over SSH; see fleet/fx99-keepalive/. The script stays on OMEN so the
# bearer token never leaves this machine — FX99 needs no secret at all.
#
# IT IS ALSO THE MONITOR. Every ping records the server's own prefill and decode timings,
# so the ledger accumulates continuous evidence of what the rung actually serves at. That
# answers the open question ADR-0043 raised and could not settle: whether real traffic has
# been running in the cold regime all along.
#
# Exit codes: 0 = warm ping served. 1 = rung did not answer (do NOT restart it from here;
# this script's job is to observe and warm, never to actuate).
param(
    [int]$Port = 8082,
    # 1 token is enough to keep the rung warm, but it generates no measurable decode, so a
    # 1-token ping CANNOT SEE the decode collapse it is preventing. A periodic deeper probe
    # (-Tokens 32, driven by arc-keepalive-deep.timer) closes that blind spot for ~0.3 s.
    [int]$Tokens = 1,
    # 80% of the 106.0 baseline -- the same gate ff_ratecheck uses.
    [double]$DegradedBelow = 84.0,
    [string]$LogPath = "C:\work\commandcenter\hearth\var\arc-keepalive.jsonl",
    [int]$TimeoutSec = 120,
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'

# The bearer lives in the gitignored fragment the gateway itself sources. Read, never printed.
$token = $null
$fragment = "C:\work\commandcenter\hearth\var\gateway.cmd"
if (Test-Path $fragment) {
    foreach ($line in Get-Content $fragment) {
        if ($line -match '^\s*set\s+OMEN_ARC_TOKEN=(.*)$') { $token = $Matches[1].Trim() }
    }
}

$headers = @{ "Content-Type" = "application/json" }
if ($token) { $headers["Authorization"] = "Bearer $token" }

# n_predict 1 is the cheapest thing that counts as "not idle". cache_prompt off so the
# ping exercises a real prefill rather than a cache hit — otherwise it would stop being
# evidence about the prefill path, which is where the 11 s stall lives.
$body = @{ prompt = "ok"; n_predict = $Tokens; temperature = 0; cache_prompt = $false } | ConvertTo-Json -Compress

$row = [ordered]@{
    ts        = (Get-Date).ToString('o')
    probe     = 'ARC-KEEPALIVE'
    port      = $Port
    ok        = $false
}
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/completion" -Method Post `
            -Headers $headers -Body $body -TimeoutSec $TimeoutSec
    $sw.Stop()
    $t = $r.timings
    $row.ok            = $true
    $row.wall_ms       = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
    $row.prompt_n      = $t.prompt_n
    $row.prompt_ms     = [math]::Round($t.prompt_ms, 1)
    $row.predicted_n   = $t.predicted_n
    $row.predicted_ms  = [math]::Round($t.predicted_ms, 1)
    # A prefill stall is the ADR-0043 signature and the thing this script exists to
    # prevent. If it ever shows up in the log, the keep-alive is not keeping up.
    $row.prefill_stall = ($t.prompt_ms -gt 2000)
    # Decode rate is only meaningful with enough tokens to average over; below that the
    # figure is dominated by per-request overhead and would raise false alarms.
    if ($t.predicted_n -ge 8 -and $t.predicted_ms -gt 0) {
        $row.decode_tok_s     = [math]::Round($t.predicted_n / ($t.predicted_ms / 1000.0), 2)
        $row.decode_degraded  = ($row.decode_tok_s -lt $DegradedBelow)
    }
} catch {
    $sw.Stop()
    $row.wall_ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
    $row.error   = $_.Exception.Message
}

New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
# NOT Add-Content -Encoding utf8: in PowerShell 5.1 that writes UTF-8 WITH a BOM, and a
# BOM at the head of a .jsonl file makes the first line fail to parse in every ordinary
# reader. This log is meant to be the monitor feed, so it has to be machine-readable
# without a special-case decode. Caught the day it was written -- json.loads choked on
# char 0. UTF8Encoding($false) is the BOM-less form.
[IO.File]::AppendAllText($LogPath, ($row | ConvertTo-Json -Compress) + [Environment]::NewLine,
                         (New-Object Text.UTF8Encoding $false))

if (-not $Quiet) {
    if ($row.ok) {
        $stall = if ($row.prefill_stall) { "  *** PREFILL STALL ***" } else { "" }
        $dec = if ($null -ne $row.decode_tok_s) {
            "  decode=$($row.decode_tok_s)tok/s" + $(if ($row.decode_degraded) { " *** DEGRADED ***" } else { "" })
        } else { "" }
        "arc-keepalive ok  wall=$($row.wall_ms)ms  prefill=$($row.prompt_ms)ms/$($row.prompt_n)tok$dec$stall"
    } else {
        "arc-keepalive FAILED after $($row.wall_ms)ms: $($row.error)"
    }
}
if ($row.ok) { exit 0 } else { exit 1 }
