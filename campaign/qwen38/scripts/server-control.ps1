param(
    [Parameter(Mandatory = $true)][ValidateSet('Start', 'Stop', 'Status')][string]$Action,
    [string]$Topology = '',
    [switch]$Mtp,
    [switch]$Vision,
    [switch]$QuantFallback,
    [int]$ParallelPerServer = 0,
    [int]$SlotDepth = 0,
    [int]$GpuLayers = -1,
    [int]$LoadTimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$config = Get-Q38Config
$root = Get-Q38RuntimeRoot
$statePath = Join-Path $root 'state\servers.json'

function Stop-RecordedServers {
    if (-not (Test-Path -LiteralPath $statePath)) { return }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($server in @($state.servers)) {
        Stop-Q38RecordedServer -Server $server
    }
    Start-Sleep -Seconds 2
    $state.status = 'stopped'
    $state | Add-Member -NotePropertyName stopped_at -NotePropertyValue (Get-Date).ToString('o') -Force
    Write-Q38JsonAtomic -Path $statePath -Value $state
}

if ($Action -eq 'Stop') {
    Stop-RecordedServers
    Write-Host 'Recorded campaign servers stopped.'
    exit 0
}
if ($Action -eq 'Status') {
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No campaign server state.'; exit 0 }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($server in @($state.servers)) {
        $alive = $null -ne (Get-Process -Id ([int]$server.pid) -ErrorAction SilentlyContinue)
        [pscustomobject]@{ topology = $state.topology; port = $server.port; pid = $server.pid; alive = $alive; device_filter = $server.device_filter }
    }
    exit 0
}

Assert-Q38Maintenance | Out-Null
if (-not $Topology) { throw '-Topology is required for Start' }
$topologyNode = $config.topologies.PSObject.Properties[$Topology]
if ($null -eq $topologyNode) { throw "Unknown topology '$Topology'" }
$spec = $topologyNode.Value
Stop-RecordedServers

$candidateId = [string]$spec.candidate
if ($QuantFallback -and $candidateId -eq 'qwen38-flash-next-iq4') { $candidateId = 'qwen38-flash-next-q2' }
$modelArtifact = Get-Q38Artifact -Id $candidateId
if (-not (Test-Path -LiteralPath ([string]$modelArtifact.path))) { throw "Model artifact is missing: $($modelArtifact.path)" }

$binary = if ($candidateId -eq 'qwen3-30b-baseline') { [string]$config.engine.production_binary } else { [string]$config.engine.campaign_binary }
if (-not (Test-Path -LiteralPath $binary)) { throw "llama-server binary is missing: $binary" }
$engineCheckout = if ($candidateId -eq 'qwen3-30b-baseline') { [string]$config.engine.production_checkout } else { [string]$config.engine.campaign_checkout }
$engineRevision = [string](git -C $engineCheckout rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or -not $engineRevision) { throw "Unable to resolve engine revision in $engineCheckout" }
$binarySha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
$alias = switch -Regex ($candidateId) {
    '^qwen3-30b' { 'qwen3-30b-a3b'; break }
    '^qwen38-27b' { 'qwen38-27b'; break }
    '^qwen38-flash' { 'qwen38-flash-next'; break }
    default { $candidateId }
}
$mmproj = $null
if ($Vision) {
    $mmprojId = if ($candidateId -match 'flash') { 'qwen38-flash-next-mmproj' } else { 'qwen38-27b-mmproj' }
    $mmproj = Get-Q38Artifact -Id $mmprojId
    if (-not (Test-Path -LiteralPath ([string]$mmproj.path))) { throw "Vision projector is missing: $($mmproj.path)" }
}
$mtpArtifact = $null
if ($Mtp) {
    if ($candidateId -ne 'qwen38-27b') { throw 'Separate draft-MTP is supported only for qwen38-27b in this campaign' }
    $mtpArtifact = Get-Q38Artifact -Id 'qwen38-27b-mtp'
    if (-not (Test-Path -LiteralPath ([string]$mtpArtifact.path))) { throw "MTP artifact is missing: $($mtpArtifact.path)" }
}

try {
    $adapters = @(Resolve-Q38B70Adapters)
    $sharedBefore = Get-Q38SharedGB -Adapters $adapters
    $commitBefore = Get-Q38CommitFreeGB
} catch {
    throw "FATAL SAFETY: pre-load admission telemetry failed: $($_.Exception.Message)"
}
if ($commitBefore -lt [double]$config.safety.commit_min_free_gb) { throw "FATAL SAFETY: commit headroom is only $commitBefore GB before load" }
$started = @()
$loadSafetySamples = @()
$loadStartedAt = Get-Date
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$oldVisible = $env:GGML_VK_VISIBLE_DEVICES
$oldMmv = $env:GGML_VK_MMV_MAX_COLS
try {
    foreach ($server in @($spec.servers)) {
        $parallel = if ($ParallelPerServer -gt 0) { $ParallelPerServer } else { [int]$server.parallel }
        $depth = if ($SlotDepth -gt 0) { $SlotDepth } else { [int]$server.slot_depth }
        $configuredLayers = Get-Q38Property -Object $server -Name 'gpu_layers'
        $layers = if ($GpuLayers -ge 0) { $GpuLayers } elseif ($null -ne $configuredLayers) { [int]$configuredLayers } elseif ($candidateId -eq 'qwen3-30b-baseline') { 99 } else { 999 }
        $context = $parallel * $depth
        $port = [int]$server.port
        $env:GGML_VK_VISIBLE_DEVICES = [string]$server.device_filter
        $env:GGML_VK_MMV_MAX_COLS = [string]$config.engine.mmv_max_cols
        # --no-mmap is right for a model that fits in VRAM: it avoids double-buffering
        # and was measured that way for the dense rungs. It is exactly wrong for a
        # deliberately host-placed model, because it forces every byte into commit —
        # 88.1 GiB of Flash-Next against ~61 GB of commit headroom would trip the
        # FATAL load-time commit floor before the first token. Topologies whose shape
        # is host placement opt into mmap and page from disk instead.
        $useMmap = [bool](Get-Q38Property -Object $spec -Name 'mmap' -Default $false)
        $arguments = @(
            '-m', [string]$modelArtifact.path,
            '--alias', $alias,
            '-ngl', [string]$layers,
            '-fa', 'on'
        )
        if (-not $useMmap) { $arguments += '--no-mmap' }
        $arguments += @(
            '-dio', '-fit', 'off',
            '-c', [string]$context,
            '-np', [string]$parallel,
            '--host', '127.0.0.1', '--port', [string]$port,
            '--slots', '--jinja', '--metrics'
        )
        if ($candidateId -ne 'qwen3-30b-baseline') { $arguments += @('--reasoning-format', 'deepseek') }
        if ([string]$server.split_mode -and [string]$server.split_mode -ne 'none') {
            $arguments += @('-sm', [string]$server.split_mode)
        }
        $tensorSplit = Get-Q38Property -Object $server -Name 'tensor_split'
        if ($null -ne $tensorSplit -and [string]$tensorSplit) {
            $arguments += @('-ts', [string]$tensorSplit)
        }
        if ($mmproj) { $arguments += @('--mmproj', [string]$mmproj.path) }
        if ($mtpArtifact) {
            $arguments += @('-md', [string]$mtpArtifact.path, '--spec-type', 'draft-mtp', '--spec-default', '--spec-draft-n-max', '3')
        }
        $recordedArguments = @($arguments)
        if ($env:QWEN38_API_KEY) {
            $arguments += @('--api-key', $env:QWEN38_API_KEY)
            $recordedArguments += @('--api-key', '<redacted:QWEN38_API_KEY>')
        }
        $logBase = Join-Path $root ("results\serverlogs\{0}-{1}-p{2}" -f $Topology, $stamp, $port)
        $process = Start-Process -FilePath $binary -ArgumentList $arguments -RedirectStandardOutput "$logBase.stdout.log" -RedirectStandardError "$logBase.stderr.log" -WindowStyle Hidden -PassThru
        $started += [pscustomobject]@{
            pid = $process.Id
            port = $port
            endpoint = "http://127.0.0.1:$port"
            health_url = "http://127.0.0.1:$port/health"
            device_filter = [string]$server.device_filter
            card = [string](Get-Q38Property -Object $server -Name 'card' -Default '')
            parallel = $parallel
            slot_depth = $depth
            context = $context
            gpu_layers = $layers
            log_base = $logBase
            binary = $binary
            binary_sha256 = $binarySha256
            engine_revision = $engineRevision
            artifact_id = [string]$modelArtifact.id
            artifact_revision = [string]$modelArtifact.revision
            model_quant = [string](Get-Q38Property -Object $modelArtifact -Name 'quant' -Default '')
            placement = [string]$spec.shape
            arguments = $recordedArguments
            command_line = $binary + ' ' + ($recordedArguments -join ' ')
            api_key_source = if ($env:QWEN38_API_KEY) { 'QWEN38_API_KEY' } else { $null }
            chat_template = $null
            chat_template_caps = $null
            modalities = $null
        }
    }
} finally {
    $env:GGML_VK_VISIBLE_DEVICES = $oldVisible
    $env:GGML_VK_MMV_MAX_COLS = $oldMmv
}

try {
    foreach ($server in $started) {
        $deadline = (Get-Date).AddSeconds($LoadTimeoutSeconds)
        $healthy = $false
        $lastSafetyProbe = [datetime]::MinValue
        do {
            Start-Sleep -Seconds 3
            if ($null -eq (Get-Process -Id $server.pid -ErrorAction SilentlyContinue)) { break }
            if (((Get-Date) - $lastSafetyProbe).TotalSeconds -ge 30) {
                $lastSafetyProbe = Get-Date
                $loadCommit = Get-Q38CommitFreeGB
                if ($loadCommit -lt [double]$config.safety.commit_min_free_gb) { throw "FATAL SAFETY: commit headroom fell to $loadCommit GB during model load" }
                $loadEvents = @(Get-Q38BadEvents -Since $loadStartedAt)
                if ($loadEvents.Count) { throw "FATAL SAFETY: system event during model load: $($loadEvents[0].ProviderName) / $($loadEvents[0].Id)" }
                try { $loadTelemetry = Get-Q38B70TelemetrySample -Adapters $adapters -Label ("load-{0}" -f $Topology) } catch { throw "FATAL SAFETY: load telemetry unavailable: $($_.Exception.Message)" }
                if ([double]$loadTelemetry.max_temperature_c -ge [double]$config.safety.vram_temperature_abort_c) {
                    throw "FATAL SAFETY: GPU/VRAM temperature reached $($loadTelemetry.max_temperature_c) C during model load"
                }
                $loadSafetySamples += [pscustomobject]@{
                    timestamp = (Get-Date).ToString('o')
                    commit_free_gb = $loadCommit
                    max_temperature_c = $loadTelemetry.max_temperature_c
                    energy_j_counter = $loadTelemetry.energy_j_counter
                    local_vram_used_gb = $loadTelemetry.local_vram_used_gb
                    host_ram_used_gb = $loadTelemetry.host_ram_used_gb
                }
            }
            try {
                $health = Invoke-WebRequest -Uri $server.health_url -UseBasicParsing -TimeoutSec 5
                if ($health.StatusCode -eq 200) { $healthy = $true; break }
            } catch {}
        } while ((Get-Date) -lt $deadline)
        if (-not $healthy) { throw "Server on port $($server.port) failed health during load; inspect $($server.log_base).stderr.log" }
        try {
            $headers = @{}
            if ($env:QWEN38_API_KEY) { $headers.Authorization = "Bearer $($env:QWEN38_API_KEY)" }
            $props = Invoke-RestMethod -Uri ("{0}/props" -f $server.endpoint) -Headers $headers -TimeoutSec 10
            $template = Get-Q38Property -Object $props -Name 'chat_template'
            $server.chat_template = if ($null -ne $template) { [string]$template } else { $null }
            $server.chat_template_caps = Get-Q38Property -Object $props -Name 'chat_template_caps'
            $server.modalities = Get-Q38Property -Object $props -Name 'modalities'
            if ($Vision -and ($null -eq $server.modalities -or -not [bool](Get-Q38Property -Object $server.modalities -Name 'vision' -Default $false))) {
                throw 'the loaded server does not advertise vision support'
            }
        } catch {
            throw "Server on port $($server.port) failed its /props template/modality contract: $($_.Exception.Message)"
        }
    }
    $templates = @($started.chat_template | Sort-Object -Unique)
    if ($templates.Count -ne 1 -or -not [string]$templates[0]) { throw 'Campaign replicas did not expose one identical, nonempty GGUF chat template' }
    Start-Sleep -Seconds 3
    try {
        $sharedAfter = Get-Q38SharedGB -Adapters $adapters
        $commitAfter = Get-Q38CommitFreeGB
    } catch {
        throw "FATAL SAFETY: post-load admission telemetry failed: $($_.Exception.Message)"
    }
    $growth = [Math]::Round($sharedAfter - $sharedBefore, 3)
    $isQuarantined = [bool](Get-Q38Property -Object $spec -Name 'quarantined' -Default $false)
    if (-not $isQuarantined -and $growth -gt [double]$config.safety.shared_growth_abort_gb) {
        throw "Unplanned shared-memory growth after load is $growth GB (limit $($config.safety.shared_growth_abort_gb) GB)"
    }
    if ($commitAfter -lt [double]$config.safety.commit_min_free_gb) {
        throw "FATAL SAFETY: commit headroom after load is only $commitAfter GB"
    }
    $state = [ordered]@{
        contract_version = 'qwen38-server-state.v1'
        status = 'running'
        started_at = (Get-Date).ToString('o')
        topology = $Topology
        candidate = $candidateId
        alias = $alias
        mtp = [bool]$Mtp
        vision = [bool]$Vision
        intentional_host_placement = $isQuarantined
        shared_preload_gb = $sharedBefore
        shared_postload_gb = $sharedAfter
        shared_growth_gb = $growth
        commit_preload_gb = $commitBefore
        commit_postload_gb = $commitAfter
        engine_revision = $engineRevision
        binary_sha256 = $binarySha256
        artifact_id = [string]$modelArtifact.id
        artifact_revision = [string]$modelArtifact.revision
        model_quant = [string](Get-Q38Property -Object $modelArtifact -Name 'quant' -Default '')
        placement = [string]$spec.shape
        load_safety_samples = $loadSafetySamples
        servers = $started
    }
    Write-Q38JsonAtomic -Path $statePath -Value $state
    $state | ConvertTo-Json -Compress -Depth 20 | Add-Content -LiteralPath (Join-Path $root 'state\server-launches.jsonl') -Encoding UTF8
    Write-Host "Started $Topology ($candidateId), shared growth $growth GB"
} catch {
    $failure = $_
    foreach ($server in $started) { Stop-Q38RecordedServer -Server $server }
    $loadEvents = @(Get-Q38BadEvents -Since $loadStartedAt)
    if ($loadEvents.Count) { throw "FATAL SAFETY: system event during model load: $($loadEvents[0].ProviderName) / $($loadEvents[0].Id)" }
    throw $failure
}
