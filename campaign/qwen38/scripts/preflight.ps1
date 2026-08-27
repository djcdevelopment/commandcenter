param(
    [ValidateSet('Offline', 'Hardware')][string]$Mode = 'Offline'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$config = Get-Q38Config
$root = [string]$config.runtime_root
$checks = [ordered]@{}

& python $script:Q38Python validate
if ($LASTEXITCODE -ne 0) { throw 'Canonical campaign validation failed' }
$checks.source_validation = 'pass'

Invoke-Q38Python init
$checks.runtime_root = if (Test-Path -LiteralPath $root) { 'pass' } else { 'fail' }

$drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0, 1))
$checks.runtime_free_gb = [Math]::Round([double]$drive.Free / 1GB, 1)
if ($checks.runtime_free_gb -lt 250) { throw "Campaign drive has only $($checks.runtime_free_gb) GB free; require 250 GB" }

$productionScript = Join-Path $script:Q38RepoRoot 'fleet\arcserve\serve-arc.cmd'
$restoreProbe = Join-Path $script:Q38RepoRoot 'fleet\arcserve\arc-serviceability.ps1'
foreach ($path in @($productionScript, $restoreProbe, $script:Q38Python)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required control file is missing: $path" }
}
$checks.restore_path = 'pass'

if ($Mode -eq 'Hardware') {
    $manifest = Join-Path $root 'state\run-manifest.json'
    if (-not (Test-Path -LiteralPath $manifest)) {
        throw "Artifact lock is absent. Run: python campaign/qwen38/qwen38_campaign.py lock"
    }
    Invoke-Q38Python verify-lock --rehash-artifacts
    Invoke-Q38Python frontier-preflight
    $checks.frontier_reference = 'pass'
    $binary = [string]$config.engine.campaign_binary
    if (-not (Test-Path -LiteralPath $binary)) { throw "Campaign llama-server is missing: $binary" }

    $oldVisible = $env:GGML_VK_VISIBLE_DEVICES
    try {
        $env:GGML_VK_VISIBLE_DEVICES = '1,2'
        $banner = (& $binary --list-devices 2>&1 | Out-String)
    } finally {
        $env:GGML_VK_VISIBLE_DEVICES = $oldVisible
    }
    $b70Lines = @(($banner -split "`n") | Where-Object { $_ -match 'Arc.*Pro B70' })
    $igpuLines = @(($banner -split "`n") | Where-Object { $_ -match 'Intel\(R\) Graphics' -and $_ -notmatch 'B70' })
    if ($b70Lines.Count -ne 2 -or $igpuLines.Count -ne 0) {
        throw "Device assertion failed: expected exactly two B70s and no iGPU after filter.`n$banner"
    }
    $checks.device_banner = $banner.Trim()
    $checks.driver_versions = @(Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop |
        Where-Object { $_.DeviceName -match 'Arc.*Pro B70' } |
        Select-Object DeviceName, DriverVersion, DriverDate, InfName)

    $source = [string]$config.engine.campaign_checkout
    $featurePatterns = [ordered]@{
        qwen35 = 'qwen35'
        qwen4exp = 'qwen4exp'
        'draft-mtp' = 'draft-mtp'
        GGML_VK_MMV_MAX_COLS = 'GGML_VK_MMV_MAX_COLS'
    }
    foreach ($feature in @($config.engine.required_features)) {
        $pattern = [string]$featurePatterns[[string]$feature]
        if (-not $pattern) { throw "No source assertion is defined for required engine feature '$feature'" }
        $hit = & rg -n -m 1 $pattern $source 2>$null
        if (-not $hit) { throw "Campaign checkout does not contain required engine feature '$feature'" }
    }
    $checks.engine_features = 'pass'
    $engineHead = [string](git -C $source rev-parse HEAD)
    $engineParent = [string](git -C $source rev-parse 'HEAD^')
    if ($engineParent.Trim() -ne [string]$config.engine.support_revision) {
        throw "Prepared engine parent $($engineParent.Trim()) does not match pinned support revision $($config.engine.support_revision)"
    }
    $checks.engine_revision = $engineHead.Trim()
    $checks.engine_binary_sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()

    $locked = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $currentConfigHash = (Get-FileHash -LiteralPath $script:Q38ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $currentArtifactsHash = (Get-FileHash -LiteralPath $script:Q38ArtifactsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $currentTasksHash = (Get-FileHash -LiteralPath (Join-Path $script:Q38SourceRoot 'assay\tasks.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([string]$locked.campaign_config_sha256 -ne $currentConfigHash -or
        [string]$locked.artifacts_config_sha256 -ne $currentArtifactsHash -or
        [string]$locked.task_set_sha256 -ne $currentTasksHash) {
        throw 'Run manifest config/task hashes do not match the current campaign snapshot'
    }
    $requiredStates = @($locked.artifacts | Where-Object { $_.required -and $_.state -ne 'locked' })
    if ($requiredStates.Count) { throw 'Run manifest contains required artifacts that are not byte-locked' }
    $lockedCampaign = @($locked.engines | Where-Object { $_.role -eq 'campaign' }) | Select-Object -First 1
    if ($null -eq $lockedCampaign -or [string]$lockedCampaign.revision -ne $checks.engine_revision -or [string]$lockedCampaign.binary_sha256 -ne $checks.engine_binary_sha256) {
        throw 'Run manifest engine revision/hash does not match the prepared campaign binary'
    }

    $adapters = @(Resolve-Q38B70Adapters -Refresh)
    $checks.adapters = @($adapters | ForEach-Object { $_.bdf })
    foreach ($server in @($config.topologies.'qwen27-replica-production'.servers)) {
        $adapter = @($adapters | Where-Object { [int]$_.vulkan_index -eq [int]$server.device_filter }) | Select-Object -First 1
        if ($null -eq $adapter) { throw "No B70 adapter maps to Vulkan index $($server.device_filter)" }
        $actualCard = 'pci-' + ([string]$adapter.bdf -replace '^0000:', '')
        if ($actualCard -ne [string]$server.card) { throw "Replica card mapping drifted: Vulkan $($server.device_filter) is $actualCard, config says $($server.card)" }
    }
    $checks.replica_card_mapping = 'pass'
    $checks.shared_gb = Get-Q38SharedGB -Adapters $adapters
    $checks.commit_free_gb = Get-Q38CommitFreeGB
    if ($checks.commit_free_gb -lt [double]$config.safety.commit_min_free_gb) {
        throw "Commit headroom $($checks.commit_free_gb) GB is below campaign floor"
    }

    $task = schtasks /Query /TN ArcServeBoot /FO LIST 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ArcServeBoot scheduled task is unavailable: $task" }
    $checks.arcserve_task = 'pass'
    $null = Get-WinEvent -LogName System -MaxEvents 1 -ErrorAction Stop
    $checks.system_event_log = 'pass'
}

$receiptPath = Join-Path $root ("state\preflight-{0}.json" -f $Mode.ToLowerInvariant())
Write-Q38JsonAtomic -Path $receiptPath -Value ([ordered]@{
    contract_version = 'qwen38-preflight.v1'
    mode = $Mode
    checked_at = (Get-Date).ToString('o')
    status = 'passed'
    checks = $checks
})
Write-Host "Qwen 3.8 $Mode preflight PASSED -> $receiptPath"
