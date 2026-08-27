param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

# Single-stream comparability sweep.
#
# The campaign measured completed jobs/hour at sixteen saturated clients. The
# published corpus compares models on single-stream pp512/tg128, so nothing the
# campaign produced can sit in that table. llama-bench is the harness behind the
# published figures, and running the incumbent through it on BOTH binaries is
# what makes the new models' rows admissible: if the control misses its known
# value, the harness is not comparable and no row from this sweep publishes.

$stage = '08-llama-bench'
if ((Test-Q38ReceiptPassed $stage) -and -not $Force) { Write-Host "$stage already passed; skipping"; exit 0 }
Assert-Q38Maintenance | Out-Null
$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$receipt = New-Q38Receipt $stage
$bench = $config.bench
$outDir = Join-Path $root 'results\bench'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$adapters = @(Resolve-Q38B70Adapters)
$armResults = @()

function Resolve-BenchBinary([string]$Which) {
    $path = if ($Which -eq 'production') { [string]$config.engine.production_binary } else { [string]$config.engine.campaign_binary }
    # llama-bench lives beside llama-server in the same build output.
    $candidate = Join-Path (Split-Path -Parent $path) 'llama-bench.exe'
    if (-not (Test-Path -LiteralPath $candidate)) { throw "llama-bench is missing beside $Which binary: $candidate" }
    $candidate
}

try {
    $oldVisible = $env:GGML_VK_VISIBLE_DEVICES
    $oldMmv = $env:GGML_VK_MMV_MAX_COLS
    try {
        # llama-bench enumerates three Vulkan devices with the iGPU at index 0.
        # The filter is as load-bearing here as it is for the servers.
        $env:GGML_VK_VISIBLE_DEVICES = '1,2'
        $env:GGML_VK_MMV_MAX_COLS = [string]$config.engine.mmv_max_cols

        foreach ($arm in @($bench.arms)) {
            $armId = [string]$arm.id
            $armOut = Join-Path $outDir ("{0}.json" -f $armId)
            if ((Test-Path -LiteralPath $armOut) -and -not $Force) {
                Write-Host "Arm $armId already has output; preserving and skipping."
                $armResults += [pscustomobject]@{ id = $armId; status = 'preserved'; output = $armOut }
                continue
            }

            $artifact = Get-Q38Artifact -Id ([string]$arm.model_artifact)
            if (-not (Test-Path -LiteralPath ([string]$artifact.path))) {
                throw "Model artifact for arm $armId is missing: $($artifact.path)"
            }
            $binary = Resolve-BenchBinary ([string]$arm.binary)

            # Every arm starts from a proven-cool box. Two topologies died at 96 C
            # today; llama-bench cannot be wrapped by the server watchdog, so the
            # cooldown gate plus between-arm sampling is the guard.
            Wait-Q38ThermalHeadroom -Adapters $adapters -Label $armId -FatalOnTimeout $false | Out-Null
            $commitBefore = Get-Q38CommitFreeGB
            if ($commitBefore -lt [double]$config.safety.commit_min_free_gb) {
                throw "FATAL SAFETY: commit headroom is only $commitBefore GB before arm $armId"
            }

            $depths = @($bench.depths | ForEach-Object { [string]$_ }) -join ','
            $arguments = @(
                '-m', [string]$artifact.path,
                '-p', [string]$bench.prompt_tokens,
                '-n', [string]$bench.gen_tokens,
                '-d', $depths,
                '-r', [string]$bench.repetitions,
                '-ngl', [string]$arm.gpu_layers,
                '-sm', 'layer',
                '-ts', '1,1',
                '-fa', 'on',
                '-lm', [string]$arm.load_mode,
                '-o', 'json'
            )
            Write-Host "Arm $armId :: $(Split-Path -Leaf $binary) $($arguments -join ' ')"
            $startedAt = Get-Date
            $stdout = Join-Path $outDir ("{0}.stdout.json" -f $armId)
            $stderrPath = Join-Path $outDir ("{0}.stderr.log" -f $armId)
            $process = Start-Process -FilePath $binary -ArgumentList $arguments `
                -RedirectStandardOutput $stdout -RedirectStandardError $stderrPath `
                -WindowStyle Hidden -PassThru -Wait
            if ($process.ExitCode -ne 0) {
                throw "Arm $armId failed with exit code $($process.ExitCode); inspect $stderrPath"
            }

            $sample = Get-Q38B70TelemetrySample -Adapters $adapters -Label "post-$armId"
            if ([double]$sample.max_temperature_c -ge [double]$config.safety.vram_temperature_abort_c) {
                throw "FATAL SAFETY: $($sample.max_temperature_c) C after arm $armId"
            }
            $commitAfter = Get-Q38CommitFreeGB

            $payload = Get-Content -LiteralPath $stdout -Raw -Encoding UTF8 | ConvertFrom-Json
            if (@($payload).Count -lt 1) { throw "Arm $armId produced no measurements" }
            Move-Item -LiteralPath $stdout -Destination $armOut -Force

            $armResults += [pscustomobject]@{
                id = $armId
                status = 'measured'
                output = $armOut
                tests = @($payload).Count
                binary = $binary
                model = [string]$artifact.path
                gpu_layers = [int]$arm.gpu_layers
                load_mode = [string]$arm.load_mode
                started_at = $startedAt.ToString('o')
                completed_at = (Get-Date).ToString('o')
                commit_free_before_gb = $commitBefore
                commit_free_after_gb = $commitAfter
                max_temperature_c = $sample.max_temperature_c
            }
            Write-Host "Arm $armId completed: $(@($payload).Count) tests, peak $($sample.max_temperature_c) C"
        }
    } finally {
        $env:GGML_VK_VISIBLE_DEVICES = $oldVisible
        $env:GGML_VK_MMV_MAX_COLS = $oldMmv
    }

    # The control gate. tg128 for the incumbent on the production binary must land
    # near the published figure or the sweep is not commensurable with the corpus.
    $controlId = [string]$bench.control_arm
    $controlPath = Join-Path $outDir ("{0}.json" -f $controlId)
    if (-not (Test-Path -LiteralPath $controlPath)) { throw "Control arm $controlId produced no output" }
    $controlTests = @(Get-Content -LiteralPath $controlPath -Raw -Encoding UTF8 | ConvertFrom-Json)
    $controlTg = @($controlTests | Where-Object { [int]$_.n_prompt -eq 0 -and [int]$_.n_gen -gt 0 -and [int]$_.n_depth -eq 0 }) |
        Select-Object -First 1
    if ($null -eq $controlTg) { throw "Control arm has no shallow tg measurement" }
    $expected = [double]$bench.control_expected_tg128
    $tolerance = [double]$bench.control_tolerance_fraction
    $observed = [double]$controlTg.avg_ts
    $drift = [Math]::Abs($observed - $expected) / $expected
    $controlPassed = $drift -le $tolerance

    $verdict = [ordered]@{
        contract_version = 'qwen38-bench-sweep.v1'
        completed_at = (Get-Date).ToString('o')
        control_arm = $controlId
        control_expected_tg128 = $expected
        control_observed_tg128 = $observed
        control_drift_fraction = [Math]::Round($drift, 6)
        control_tolerance_fraction = $tolerance
        control_passed = $controlPassed
        publishable = $controlPassed
        arms = $armResults
    }
    Write-Q38JsonAtomic -Path (Join-Path $outDir 'sweep-verdict.json') -Value $verdict

    if (-not $controlPassed) {
        Write-Warning "CONTROL GATE FAILED: $observed tok/s against an expected $expected ($([Math]::Round(100*$drift,1))% drift). These rows are NOT comparable to the published corpus."
    } else {
        Write-Host "Control gate passed: $observed tok/s against an expected $expected."
    }
    $detail = if ($controlPassed) {
        "Four arms measured; control tg128 $observed tok/s within $([Math]::Round(100*$tolerance,0))% of the published $expected."
    } else {
        "Four arms measured but the control missed: $observed vs $expected. Rows are not corpus-comparable."
    }
    # PowerShell 5.1 rejects a bare (if ...) in an argument position at RUNTIME even
    # though it parses, so the status is resolved into a variable first.
    $receiptStatus = if ($controlPassed) { 'passed' } else { 'failed' }
    Complete-Q38Receipt -Receipt $receipt -Status $receiptStatus -Detail $detail
    if (-not $controlPassed) { throw $detail }
} catch {
    Complete-Q38Receipt -Receipt $receipt -Status failed -Detail $_.Exception.Message
    throw
}
