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
                # llama-bench separates the values WITHIN one tensor-split with '/'
                # and uses ',' to request SEVERAL configurations. '1,1' therefore
                # ran the whole matrix twice at tensor_split=1.00, which puts every
                # layer on the first card - single-card numbers wearing a dual-card
                # label. '1/1' is the even two-way split production actually uses.
                '-ts', [string]$bench.tensor_split,
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

    # The control gate is BUILD EQUIVALENCE between the two control arms, not
    # agreement with the published 95.2 tok/s. That figure is a llama-server
    # single-stream measurement and llama-bench runs without the serving path, so
    # comparing them measures the harness, not the hardware. It is recorded as
    # context below and never gated on.
    function Get-ShallowTg([string]$ArmId) {
        $path = Join-Path $outDir ("{0}.json" -f $ArmId)
        if (-not (Test-Path -LiteralPath $path)) { throw "Control arm $ArmId produced no output" }
        # PowerShell 5.1's ConvertFrom-Json hands a JSON array to the pipeline as a
        # SINGLE object, so @(pipeline) wraps it rather than unrolling it and every
        # member access then returns an array. Assign first, wrap second.
        $parsed = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        $tests = @($parsed)
        $tg = @($tests | Where-Object { [int]$_.n_prompt -eq 0 -and [int]$_.n_gen -gt 0 -and [int]$_.n_depth -eq 0 }) |
            Select-Object -First 1
        if ($null -eq $tg) { throw "Control arm $ArmId has no shallow tg measurement" }
        [double]$tg.avg_ts
    }
    $pair = @($bench.control_pair | ForEach-Object { [string]$_ })
    $tolerance = [double]$bench.control_tolerance_fraction
    $productionTg = Get-ShallowTg $pair[0]
    $campaignTg = Get-ShallowTg $pair[1]
    $drift = [Math]::Abs($productionTg - $campaignTg) / $productionTg
    $controlPassed = $drift -le $tolerance
    $observed = $productionTg
    $expected = $campaignTg

    $verdict = [ordered]@{
        contract_version = 'qwen38-bench-sweep.v2'
        completed_at = (Get-Date).ToString('o')
        gate = 'build-equivalence'
        control_pair = $pair
        production_binary_tg128 = $productionTg
        campaign_binary_tg128 = $campaignTg
        control_drift_fraction = [Math]::Round($drift, 6)
        control_tolerance_fraction = $tolerance
        control_passed = $controlPassed
        publishable = $controlPassed
        tensor_split = [string]$bench.tensor_split
        server_reference_tg_tokens_per_s = [double]$bench.server_reference_tg_tokens_per_s
        server_reference_note = [string]$bench.server_reference_note
        arms = $armResults
    }
    Write-Q38JsonAtomic -Path (Join-Path $outDir 'sweep-verdict.json') -Value $verdict

    if (-not $controlPassed) {
        Write-Warning "BUILD EQUIVALENCE FAILED: production $productionTg tok/s vs campaign $campaignTg ($([Math]::Round(100*$drift,1))% apart). The two binaries do not measure the same, so the new models' rows carry that caveat."
    } else {
        Write-Host "Build equivalence held: production $productionTg tok/s vs campaign $campaignTg ($([Math]::Round(100*$drift,1))% apart)."
    }
    $detail = if ($controlPassed) {
        "Four arms measured; the two binaries agree within $([Math]::Round(100*$tolerance,0))% ($productionTg vs $campaignTg tok/s tg128)."
    } else {
        "Four arms measured but the binaries disagree: $productionTg vs $campaignTg tok/s. New rows are not build-neutral."
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
