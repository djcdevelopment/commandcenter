param(
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$config = Get-Q38Config
$engine = $config.engine
$checkout = [string]$engine.campaign_checkout
$revision = [string]$engine.support_revision
$patchRevision = [string]$engine.mmv_patch_revision

$freshClone = $false
if (-not (Test-Path -LiteralPath $checkout)) {
    git clone --filter=blob:none --no-checkout ([string]$engine.source_repo) $checkout
    if ($LASTEXITCODE -ne 0) { throw 'llama.cpp clone failed' }
    $freshClone = $true
} elseif (-not (Test-Path -LiteralPath (Join-Path $checkout '.git'))) {
    throw "Campaign checkout exists but is not a Git repository: $checkout"
}

# A --no-checkout clone has an empty index and reports every HEAD path as a
# deletion. That is bootstrap state, not a dirty user worktree. Existing normal
# checkouts are still protected before any switch.
$trackedPaths = @(git -C $checkout ls-files)
$bootstrapCheckout = $freshClone -or $trackedPaths.Count -eq 0
if (-not $bootstrapCheckout) {
    $dirty = git -C $checkout status --porcelain
    if ($dirty) { throw "Campaign llama.cpp checkout is dirty; preserve or clear it before preparation:`n$dirty" }
}
git -C $checkout fetch origin ([string]$engine.source_branch)
if ($LASTEXITCODE -ne 0) { throw 'Failed to fetch the pinned Qwen3.8 support branch' }
$headParent = if (-not $bootstrapCheckout) { [string](git -C $checkout rev-parse 'HEAD^' 2>$null) } else { '' }
$headMessage = if (-not $bootstrapCheckout) { [string](git -C $checkout log -1 --format=%B) } else { '' }
$preparedHead = $headParent.Trim() -eq $revision -and $headMessage -match 'GGML_VK_MMV_MAX_COLS'
if (-not $preparedHead) {
    git -C $checkout switch --detach $revision
    if ($LASTEXITCODE -ne 0) { throw "Failed to checkout pinned support revision $revision" }
    $dirty = git -C $checkout status --porcelain
    if ($dirty) { throw "Pinned campaign checkout is unexpectedly dirty after preparation:`n$dirty" }

    # The proven Vulkan crossover patch is a real local commit in the separate
    # knee checkout. Fetch/cherry-pick preserves provenance instead of retyping it.
    git -C $checkout fetch 'E:\work\llamacpp-knee' $patchRevision
    if ($LASTEXITCODE -ne 0) { throw 'Failed to fetch the local MMV patch commit' }
    git -C $checkout cherry-pick $patchRevision
    if ($LASTEXITCODE -ne 0) {
        git -C $checkout cherry-pick --abort 2>$null
        throw 'The MMV patch no longer applies cleanly to the pinned Qwen3.8 branch; port and re-prove it before benchmarking'
    }
}
$currentBranch = [string](git -C $checkout symbolic-ref --quiet --short HEAD 2>$null)
if ($currentBranch -ne 'qwen38-campaign-pinned') {
    git -C $checkout switch -C qwen38-campaign-pinned
    if ($LASTEXITCODE -ne 0) { throw 'Failed to anchor the prepared campaign revision' }
}

if (-not $SkipBuild) {
    $vsRoot = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
    $vsDev = Join-Path $vsRoot 'Common7\Tools\VsDevCmd.bat'
    $cmake = Join-Path $vsRoot 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
    $ninja = Join-Path $vsRoot 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe'
    foreach ($path in @($vsDev, $cmake, $ninja)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Build prerequisite is missing: $path" }
    }
    $build = Join-Path $checkout 'build'
    $configure = 'call "{0}" -arch=x64 -host_arch=x64 >nul && "{1}" -S "{2}" -B "{3}" -G Ninja -DCMAKE_MAKE_PROGRAM="{4}" -DGGML_VULKAN=ON -DGGML_NATIVE=ON -DLLAMA_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release' -f $vsDev, $cmake, $checkout, $build, $ninja
    cmd.exe /d /s /c $configure
    if ($LASTEXITCODE -ne 0) { throw 'llama.cpp CMake configure failed' }
    $compile = 'call "{0}" -arch=x64 -host_arch=x64 >nul && "{1}" --build "{2}" --target llama-server llama-cli llama-bench -j 24' -f $vsDev, $cmake, $build
    cmd.exe /d /s /c $compile
    if ($LASTEXITCODE -ne 0) { throw 'llama.cpp build failed' }
}

$binary = [string]$engine.campaign_binary
if (-not $SkipBuild -and -not (Test-Path -LiteralPath $binary)) { throw "Build completed without expected binary: $binary" }
$compiler = Get-ChildItem -LiteralPath 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC' -Filter 'cl.exe' -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'Hostx64\\x64\\cl\.exe$' } | Sort-Object FullName -Descending | Select-Object -First 1
$receipt = [ordered]@{
    contract_version = 'qwen38-engine-receipt.v1'
    prepared_at = (Get-Date).ToString('o')
    checkout = $checkout
    revision = (git -C $checkout rev-parse HEAD)
    support_revision = $revision
    mmv_patch_revision = $patchRevision
    binary = $binary
    binary_sha256 = if (Test-Path -LiteralPath $binary) { (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
    compiler = if ($compiler) { [ordered]@{ path = $compiler.FullName; file_version = $compiler.VersionInfo.FileVersion } } else { $null }
}
Write-Q38JsonAtomic -Path (Join-Path (Get-Q38RuntimeRoot) 'state\engine-receipt.json') -Value $receipt
$receipt | Format-List
