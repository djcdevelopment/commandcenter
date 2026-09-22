$ErrorActionPreference = 'Stop'
$operatorRepo = 'C:\work\commandcenter-hermes-fx99'
$operatorState = 'C:\Users\derek\.hermes-fleet'
$env:HEARTH_SCOPE = "$operatorRepo;C:\work\commandcenter\knowledge"
$env:HEARTH_ROOT = 'C:\work\commandcenter\hearth'
$env:HEARTH_BUILD_REQUEST_DIR = "$operatorRepo\artifacts\receipts"
$env:HERMES_BUILD_REPO = $operatorRepo
$env:HERMES_AM4_KEY_FILE = "$operatorState\am4.key"
$env:HERMES_CALLERS_PATH = "$operatorState\callers.json"
# Production still has a pre-lock writer loaded. Keep a separate canonical kernel
# stream until its owner deploys the same lock; never mix locking/unlocked writers.
$env:HERMES_LEDGER_DIR = "$operatorState\kernel-ledger"
Set-Location -LiteralPath $operatorRepo
while ($true) {
    & 'C:\Users\derek\AppData\Local\Programs\Python\Python312\python.exe' "$operatorRepo\fleet\hermes\serve-hearth.py"
    Write-Warning "Hermes listener exited ($LASTEXITCODE); restarting in five seconds."
    Start-Sleep -Seconds 5
}
