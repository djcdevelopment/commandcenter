# cutover.ps1 -- the ArcServeBoot -> llama-swap cutover ceremony (ADR-0045 / plan P13; Derek's
# 2026-09-03 decision). DRY-RUN BY DEFAULT: runs every read-only pre-flight check and prints the exact
# commands the live run would execute, executing none of them. -Live executes, and refuses unless every
# pre-flight item passed in the same invocation. Rollback = serve-arc-direct.cmd (the pre-cutover
# launcher, byte-identical body) on any abort criterion.
#
# What it never does: read or print the bearer (it is passed to HTTP calls only), touch :8083/:8084,
# widen any listener, or leave production down on failure without attempting the rollback.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\arcserve\cutover.ps1            # dry run
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\arcserve\cutover.ps1 -Live      # the window
param(
    [switch]$Live,
    [string]$Window = ("rot-cutover-{0}" -f (Get-Date).ToString("yyyyMMdd-HHmm")),
    [string]$Reason = "ADR-0045 P13: production omen-arc moves under llama-swap (Derek 2026-09-03)"
)
$ErrorActionPreference = "Continue"
$DryRun = -not $Live
$Repo = "C:\work\commandcenter"
$Py = "$Repo\fleet-worker-node\.venv-omen\Scripts\python.exe"
$Yaml = "$Repo\fleet\arcserve\llama-swap\omen.yaml"
$SwapExe = "E:\work\llama-swap-v251\llama-swap.exe"
$ServeArc = "$Repo\fleet\arcserve\serve-arc.cmd"
$ServeDirect = "$Repo\fleet\arcserve\serve-arc-direct.cmd"
$Sentinel = "$Repo\hearth\var\arc-maintenance.stop"
$WindowsLog = "$Repo\hearth\var\rotation-windows.jsonl"
$KeepAlive = "$Repo\hearth\var\arc-keepalive.jsonl"
$Baselines = "$Repo\campaign\ff-probes\rate-baselines.json"
$GatewayCmd = "$Repo\hearth\var\gateway.cmd"
$Swap = "http://127.0.0.1:8081"
$Prod = "http://127.0.0.1:8082"
$script:Started = Get-Date
$script:Receipts = @()
$script:Preflight = @()

function Say([string]$m) { Write-Host ("[{0}] {1}" -f (Get-Date).ToString("HH:mm:ss"), $m) }
function Get-Mode { if ($DryRun) { return "DRY-RUN" } else { return "LIVE" } }
function Plan([string]$label, [scriptblock]$action, [string]$describe) {
    if ($DryRun) { Say ("DRY-RUN {0}: would run -> {1}" -f $label, $describe); return $true }
    Say ("LIVE {0}: {1}" -f $label, $describe)
    try { return (& $action) } catch { Say ("{0} threw: {1}" -f $label, $_.Exception.Message); return $false }
}
function Get-Bearer() {
    $t = $env:OMEN_ARC_TOKEN
    if (-not $t -and (Test-Path $GatewayCmd)) {
        foreach ($line in Get-Content $GatewayCmd) {
            if ($line -match '^\s*set\s+OMEN_ARC_TOKEN=(.*)$') { $t = $Matches[1].Trim(); break }
        }
    }
    return $t
}
function Get-Http([string]$url, [int]$timeout = 10, [string]$bearer = $null) {
    $h = @{}
    if ($bearer) { $h["Authorization"] = "Bearer $bearer" }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $h -TimeoutSec $timeout
        return @{ ok = $true; status = [int]$r.StatusCode; text = [string]$r.Content }
    } catch {
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch {}
        return @{ ok = $false; status = $status; text = $_.Exception.Message }
    }
}
function Post-Json([string]$url, [string]$body, [int]$timeout = 120, [string]$bearer = $null) {
    $h = @{}
    if ($bearer) { $h["Authorization"] = "Bearer $bearer" }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -ContentType "application/json" -Body $body -TimeoutSec $timeout
        return @{ ok = $true; status = [int]$r.StatusCode; text = [string]$r.Content }
    } catch {
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch {}
        return @{ ok = $false; status = $status; text = $_.Exception.Message }
    }
}
function Test-Listen([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c)
}
function Wait-Until([scriptblock]$test, [int]$deadlineSec, [string]$what, [int]$pollSec = 3) {
    $deadline = (Get-Date).AddSeconds($deadlineSec)
    while ((Get-Date) -lt $deadline) {
        if (& $test) { return $true }
        Start-Sleep -Seconds $pollSec
    }
    Say ("timed out after {0}s waiting for {1}" -f $deadlineSec, $what)
    return $false
}
function Newest-KeepAliveRow() {
    if (-not (Test-Path $KeepAlive)) { return $null }
    $lines = Get-Content $KeepAlive -Tail 3
    foreach ($l in ($lines | Sort-Object -Descending)) {
        try { return ($l | ConvertFrom-Json) } catch {}
    }
    return $null
}
function Append-Window([string]$event, [string]$status, [hashtable]$extra) {
    $row = [ordered]@{ ts = (Get-Date).ToString("o"); event = $event; name = $Window; reason = $Reason;
                       ports = @(8081, 8082); models = @("qwen3-30b-a3b"); status = $status; mode = (Get-Mode) }
    foreach ($k in $extra.Keys) { $row[$k] = $extra[$k] }
    $json = ($row | ConvertTo-Json -Compress -Depth 6)
    if ($DryRun) { Say ("DRY-RUN would append to rotation-windows.jsonl: {0}" -f $json); return }
    [IO.File]::AppendAllText($WindowsLog, $json + "`n", (New-Object Text.UTF8Encoding $false))
}
function Check([string]$name, [bool]$ok, [string]$detail) {
    $script:Preflight += [pscustomobject]@{ check = $name; ok = $ok; detail = $detail }
    $mark = "PASS"
    if (-not $ok) { $mark = "FAIL" }
    Say ("pre-flight {0}: {1} -- {2}" -f $mark, $name, $detail)
}
function Rollback([string]$why) {
    Say ("ROLLBACK ({0})" -f $why)
    if ($DryRun) { Say "DRY-RUN: rollback would copy serve-arc-direct.cmd over serve-arc.cmd, sentinel-stop, then ArcServeBoot"; return }
    Copy-Item -Force $ServeDirect $ServeArc
    New-Item -ItemType File -Force $Sentinel | Out-Null
    schtasks /Run /TN ArcServeRestart | Out-Null
    [void](Wait-Until { -not (Test-Listen 8082) -and -not (Get-Process llama-swap -ErrorAction SilentlyContinue) } 150 "old tree gone (rollback)")
    Remove-Item -Force $Sentinel -ErrorAction SilentlyContinue
    schtasks /Run /TN ArcServeBoot | Out-Null
    $back = Wait-Until { (Get-Http "$Prod/health" 5).status -eq 200 } 240 "production /health after rollback"
    Append-Window "window.close" "aborted" @{ rollback = $true; production_back = $back; why = $why }
    Say ("rollback finished; production_back={0}. Restore serve-arc.cmd in git before retrying." -f $back)
}
function Abort([string]$why) { Say ("ABORT: {0}" -f $why); Rollback $why; exit 1 }

Say ("cutover ceremony {0} -- window {1}" -f (Get-Mode), $Window)

# ---------------------------------------------------------------- pre-flight (read-only, both modes)
$verdict = ""
try { $verdict = (& $Py -c "from hearth.health.rungstate import live_rung_state as r; print(r()['verdict'])" 2>$null | Select-Object -Last 1) } catch {}
Check "rung state at_rate|warn" ($verdict -in @("at_rate", "warn")) ("verdict={0}" -f $verdict)

$session = ""
try { $session = (& $Py -c "from hearth.execution.coordination import GpuTenancyStore as S; s=S().active_image_session('omen-b70-pool'); print('none' if s is None else s.session_id)" 2>$null | Select-Object -Last 1) } catch { $session = "unreadable" }
Check "no image session on omen-b70-pool" ($session -eq "none") ("session={0}" -f $session)

$os = Get-CimInstance Win32_OperatingSystem
$commitFreeGb = [math]::Round($os.FreeVirtualMemory / 1MB, 1)
Check "commit free >= 6 GB" ($commitFreeGb -ge 6.0) ("free={0} GB" -f $commitFreeGb)

$row = Newest-KeepAliveRow
$kaOk = $false; $kaAge = -1
if ($row) { $kaAge = [math]::Round(((Get-Date) - [datetime]$row.ts).TotalSeconds, 0); $kaOk = ($row.ok -and $kaAge -le 120) }
Check "keep-alive ticking (newest row ok, <=120 s old)" $kaOk ("age={0}s ok={1}" -f $kaAge, $row.ok)

$yamlOk = Test-Path $Yaml
$leak = @()
if ($yamlOk) { $leak = Get-Content $Yaml | Where-Object { $_ -notmatch '^\s*#' } | Where-Object { $_ -match 'api-key|OMEN_ARC_TOKEN\s*=|Bearer ' } }
Check "omen.yaml present and free of key literals" ($yamlOk -and $leak.Count -eq 0) ("present={0} leaks={1}" -f $yamlOk, $leak.Count)
Check "llama-swap binary present" (Test-Path $SwapExe) $SwapExe
Check "serve-arc-direct.cmd (rollback) present" (Test-Path $ServeDirect) $ServeDirect
Check "serve-arc-swap.cmd (the new launcher, parked) present" (Test-Path $ServeSwap) $ServeSwap
$liveIsDirect = ((Get-Content $ServeArc -Raw) -notmatch "llama-swap")
Check "serve-arc.cmd is still the pre-cutover launcher" $liveIsDirect ("mentions llama-swap={0}" -f (-not $liveIsDirect))
Check ":8081 free" (-not (Test-Listen 8081)) ("listening={0}" -f (Test-Listen 8081))
Check ":8082 listening (incumbent)" (Test-Listen 8082) ("listening={0}" -f (Test-Listen 8082))
$bearer = Get-Bearer
Check "bearer available to the ceremony (never printed)" ([bool]$bearer) ("present={0}" -f [bool]$bearer)
Check "sentinel absent" (-not (Test-Path $Sentinel)) $Sentinel

$failed = @($script:Preflight | Where-Object { -not $_.ok })
if ($failed.Count -gt 0) {
    Say ("{0} pre-flight check(s) failed: {1}" -f $failed.Count, (($failed | ForEach-Object { $_.check }) -join "; "))
    if ($Live) { Say "LIVE refused."; exit 2 } else { Say "(dry-run continues to print the plan)" }
}

# ---------------------------------------------------------------- the window
Append-Window "window.open" "open" @{ preflight = ($script:Preflight | ForEach-Object { $_.check + "=" + $_.ok }) }

# Step A: stop the incumbent (stop-only via the sentinel), wait for the port and process to be gone.
$a = Plan "A stop incumbent" {
    New-Item -ItemType File -Force $Sentinel | Out-Null
    schtasks /Run /TN ArcServeRestart | Out-Null
    $gone = Wait-Until { -not (Test-Listen 8082) -and -not (Get-Process llama-server -ErrorAction SilentlyContinue) } 150 ":8082 closed and llama-server gone"
    Remove-Item -Force $Sentinel -ErrorAction SilentlyContinue
    return $gone
} "New-Item $Sentinel; schtasks /Run /TN ArcServeRestart; wait :8082 closed + no llama-server (150 s); Remove-Item $Sentinel"
if (-not $a) { Abort "incumbent did not stop" }

# Step B: start the new shape and wait for real readiness (health + a completion with timings).
$b = Plan "B install the llama-swap launcher and start it via ArcServeBoot" {
    Copy-Item -Force $ServeSwap $ServeArc
    schtasks /Run /TN ArcServeBoot | Out-Null
    if (-not (Wait-Until { (Get-Http "$Swap/health" 5).status -eq 200 } 60 "llama-swap /health on :8081")) { return $false }
    if (-not (Wait-Until { (Get-Http "$Prod/health" 5).status -eq 200 } 240 "production /health on :8082 (preload; first-in-window 19-27 s expected)")) { return $false }
    $body = '{"prompt":"ok","n_predict":1,"temperature":0,"cache_prompt":false}'
    $ready = Wait-Until {
        $r = Post-Json "$Prod/completion" $body 120 $bearer
        ($r.ok -and $r.text -match '"timings"')
    } 120 "a real 1-token completion with a timings block"
    return $ready
} "Copy-Item serve-arc-swap.cmd -> serve-arc.cmd; schtasks /Run /TN ArcServeBoot; wait $Swap/health 200 (60 s); wait $Prod/health 200 (240 s); POST $Prod/completion 1 token with bearer until timings present (120 s)"
if (-not $b) { Abort "new shape not ready" }

# Step C: placement assertion from the -lv 5 load report via llama-swap /logs (ADR-0042).
$c = Plan "C assert dual-split placement" {
    $logs = Get-Http "$Swap/logs" 15
    if (-not $logs.ok) { Say "could not read /logs"; return $false }
    $lines = $logs.text -split "`n" | Where-Object { $_ -match 'model buffer size' }
    $b70 = @($lines | Where-Object { $_ -match 'Vulkan\d+' -and $_ -notmatch 'CPU' }).Count
    $igpu = @($logs.text -split "`n" | Where-Object { $_ -match 'using device' -and $_ -match 'Intel\(R\) Graphics' }).Count
    $devs = @($logs.text -split "`n" | Where-Object { $_ -match 'using device Vulkan\d+ \(Intel\(R\) Arc\(TM\) Pro B70' }).Count
    $script:Receipts += ("placement: model-buffer lines={0} B70-using-lines={1} iGPU-using-lines={2}" -f $b70, $devs, $igpu)
    Say ("placement: {0} Vulkan model-buffer line(s), {1} 'using device ... B70' line(s), {2} iGPU" -f $b70, $devs, $igpu)
    return ($devs -eq 2 -and $igpu -eq 0 -and $b70 -ge 2)
} "GET $Swap/logs; require exactly 2 'using device VulkanN (Intel(R) Arc(TM) Pro B70' lines, 0 'Intel(R) Graphics', >=2 model-buffer lines"
if (-not $c) { Abort "placement is not dual-split B70 (ADR-0042)" }

# Step D: the api key is enforced (LLAMA_API_KEY inherited) -- a bare request must be refused.
$d = Plan "D api key enforced" {
    $r = Post-Json "$Prod/completion" '{"prompt":"ok","n_predict":1}' 20 $null
    $script:Receipts += ("auth: bare request status={0}" -f $r.status)
    return ($r.status -eq 401 -or $r.status -eq 403)
} "POST $Prod/completion WITHOUT bearer -> expect 401/403"
if (-not $d) { Abort "api key not enforced on :8082 (LLAMA_API_KEY not inherited?)" }

# Step E: known-good rate, warm burst immediately after load (ADR-0043 rule 1).
$e = Plan "E ff_ratecheck PASS" {
    & $Py "$Repo\campaign\ff-probes\ff_ratecheck.py" --rung omen-arc --note ("cutover " + $Window)
    $rc = $LASTEXITCODE
    $script:Receipts += ("ratecheck rc={0}" -f $rc)
    return ($rc -eq 0)
} "$Py campaign\ff-probes\ff_ratecheck.py --rung omen-arc --note 'cutover $Window' (exit 0 = PASS)"
if (-not $e) { Abort "rate check did not PASS" }

# Step F: the keep-alive resumes on its own (fx99 owns it; nothing to do but watch).
$f = Plan "F keep-alive resumes" {
    $ok = Wait-Until {
        $n = Newest-KeepAliveRow
        ($n -and ([datetime]$n.ts) -gt $script:Started -and $n.ok)
    } 100 "a keep-alive row newer than the window start with ok:true"
    $n = Newest-KeepAliveRow
    if ($n) { $script:Receipts += ("keepalive: ts={0} ok={1} prompt_ms={2}" -f $n.ts, $n.ok, $n.prompt_ms) }
    return $ok
} "watch $KeepAlive for a row newer than the window start with ok:true (100 s)"
if (-not $f) { Abort "keep-alive did not resume" }

# Step G: stamp the epoch boundary and close the window.
Plan "G stamp epoch boundary" {
    $doc = Get-Content $Baselines -Raw | ConvertFrom-Json
    $last = $doc.epoch_boundaries[$doc.epoch_boundaries.Count - 1]
    $last.ts = (Get-Date).ToString("o")
    ($doc | ConvertTo-Json -Depth 12) | Set-Content -Path $Baselines -Encoding UTF8
    return $true
} "set epoch_boundaries[-1].ts in $Baselines (baseline 106.0 untouched)" | Out-Null

Append-Window "window.close" "done" @{ receipts = $script:Receipts; elapsed_s = [math]::Round(((Get-Date) - $script:Started).TotalSeconds, 0) }
Say ("cutover {0} complete. Door proof (local_generate backend=omen-arc) is the operator's next line; llama-swap :8081 now fronts the side seats (omen-swap rung)." -f (Get-Mode))
exit 0
