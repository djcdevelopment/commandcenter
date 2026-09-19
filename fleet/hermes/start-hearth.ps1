$ErrorActionPreference = 'Stop'
$operatorRepo = 'C:\work\commandcenter-hermes-fx99'
$operatorState = 'C:\Users\derek\.hermes-fleet'
$env:HEARTH_SCOPE = "$operatorRepo;C:\work\commandcenter\knowledge"
$env:HEARTH_ROOT = 'C:\work\commandcenter\hearth'
$env:HEARTH_BUILD_REQUEST_DIR = "$operatorRepo\artifacts\receipts"
$env:HERMES_BUILD_REPO = $operatorRepo
$env:HERMES_CALLERS_PATH = "$operatorState\callers.json"
$env:HERMES_LEDGER_DIR = 'C:\work\commandcenter\hearth\var\ledger'
# Deliberately absent until trusted runner routes have passed live qualification.
$env:HERMES_LOCAL_BUILDERS_QUALIFIED = '0'
Set-Location -LiteralPath $operatorRepo
& 'C:\Users\derek\AppData\Local\Programs\Python\Python312\python.exe' "$operatorRepo\fleet\hermes\serve-hearth.py"
exit $LASTEXITCODE
