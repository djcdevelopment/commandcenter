# Take a paired tenant-cost sample every -IntervalSeconds, -Count times.
#
# WHY POWERSHELL AND NOT A .cmd LOOP. The first version of this used `timeout /t` and it FAILED
# under a redirected stdin -- "ERROR: Input redirection is not supported, exiting the process
# immediately" -- so all 15 iterations fired inside one second. The sampler's own 60 s spacing
# guard refused every premature call and the machine took no extra load, which is exactly why
# that guard exists. Start-Sleep has no such dependency on a console.
#
# Runs each sample through with-gateway-env.cmd because ff_ratecheck needs OMEN_ARC_TOKEN, which
# lives only in the gitignored hearth\var\gateway.cmd and is never echoed.
#
# Cost per sample: three short completions plus one passive b70tools read. The operator is doing
# real work on this box; the instrument must not become the load.

param(
    [int]$IntervalSeconds = 600,
    [int]$Count = 15,
    [string]$Note = "valheim window"
)

$ErrorActionPreference = 'Continue'
$py      = 'C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe'
$sampler = 'C:\work\commandcenter\campaign\ff-probes\tenant_sample.py'
$wrap    = 'C:\work\commandcenter\hearth\etc\with-gateway-env.cmd'

for ($i = 1; $i -le $Count; $i++) {
    $stamp = (Get-Date).ToString('HH:mm:ss')
    Write-Output "[tenant-series] sample $i of $Count at $stamp"
    & cmd /c "`"$wrap`" `"$py`" `"$sampler`" --note `"$Note (auto $i/$Count)`""
    if ($i -lt $Count) { Start-Sleep -Seconds $IntervalSeconds }
}
Write-Output "[tenant-series] done"
