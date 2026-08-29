# placement.ps1 -- assert WHICH devices a probe server actually loaded onto.
#
# Why this exists (ADR-0042): Vulkan enumeration order on this box is nondeterministic
# PER PROCESS LAUNCH. An index filter that is correct in one process is wrong in the
# next, with no error and no warning. Production ran 49/49 layers on ONE B70 for an
# unknown number of days because placement was assumed rather than observed, and the
# whole LZ venue matrix was measured in that state.
#
# Two hard-won facts shape this file:
#
#   1. At the default verbosity (3) llama-server emits NO placement lines at all --
#      no "using device", no "model buffer size". Every LZ probe log written before
#      2026-08-29 contains zero of them, which is why the defect survived a whole
#      campaign. Servers MUST be launched with `-lv 5` or there is nothing to assert.
#
#   2. Discovering the enumeration order in a HELPER process does not tell you what
#      the SERVER process will see -- that is the exact trap ADR-0042 documents
#      (an interactive shell saw [iGPU,B70,B70] while the S4U task saw [B70,B70,iGPU]).
#      So enumeration is advisory only. The load report is the authority.
#
# Usage:
#   . "$PSScriptRoot\..\kit\placement.ps1"
#   Assert-Placement -LogPath $err -Expect both-b70 -Cell "lz1-A"

function Get-VulkanDevices {
    <#
      Unfiltered enumeration, for logging and for building an iGPU filter. ADVISORY
      ONLY -- see note 2 above. Never gate a result on this; record it as context.
    #>
    param([string]$Bench = "E:\work\llamacpp-knee\build\bin\llama-bench.exe")
    $saved = $env:GGML_VK_VISIBLE_DEVICES
    Remove-Item Env:GGML_VK_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    try   { $out = & $Bench --list-devices 2>&1 | Out-String }
    finally {
        if ($null -ne $saved) { $env:GGML_VK_VISIBLE_DEVICES = $saved }
    }
    $devices = @()
    foreach ($line in ($out -split "`r?`n")) {
        if ($line -match 'ggml_vulkan:\s+(\d+)\s+=\s+(.+?)\s+\|') {
            $devices += [pscustomobject]@{
                Index = [int]$Matches[1]
                Name  = $Matches[2].Trim()
                IsB70 = $Matches[2] -match 'Arc\(TM\) Pro B70|Arc Pro B70'
            }
        }
    }
    return $devices
}

function Get-DeviceFilterByRole {
    <#
      Build a GGML_VK_VISIBLE_DEVICES string for THIS enumeration, by ROLE.

      This is the documented EXCEPTION to "never filter by index" (ADR-0042). iGPU
      cells have no alternative: device-TYPE selection deliberately keeps only
      dedicated GPUs, so an iGPU venue cannot be reached without naming devices. The
      mitigation is threefold -- indices are discovered at run time rather than
      hardcoded, the discovered order is recorded on the receipt, and the resulting
      placement is asserted from the load report, which is what actually catches a
      reshuffle. Never use this for a both-B70 cell; drop the filter there instead.

      -Roles: ordered list of 'igpu' / 'b70'. Returns e.g. "0,1".
    #>
    param(
        [Parameter(Mandatory)][string[]]$Roles,
        [string]$Bench = "E:\work\llamacpp-knee\build\bin\llama-bench.exe"
    )
    $devices = Get-VulkanDevices -Bench $Bench
    if (-not $devices) { throw "Get-DeviceFilterByRole: enumeration returned no devices" }
    $b70 = @($devices | Where-Object { $_.IsB70 })
    $igpu = @($devices | Where-Object { -not $_.IsB70 })
    $picked = @()
    foreach ($role in $Roles) {
        switch ($role) {
            'igpu' {
                if (-not $igpu) { throw "Get-DeviceFilterByRole: no iGPU in this enumeration" }
                $picked += $igpu[0].Index; $igpu = @($igpu | Select-Object -Skip 1)
            }
            'b70' {
                if (-not $b70) { throw "Get-DeviceFilterByRole: no free B70 in this enumeration" }
                $picked += $b70[0].Index; $b70 = @($b70 | Select-Object -Skip 1)
            }
            default { throw "Get-DeviceFilterByRole: unknown role '$role'" }
        }
    }
    $order = ($devices | ForEach-Object { "$($_.Index)=$($_.Name)" }) -join ', '
    Write-Host "  enumeration THIS run: $order  ->  roles [$($Roles -join ',')] = $($picked -join ',')"
    Write-Host "  ^ advisory only: the SERVER process may enumerate differently. The load-report assert is the gate."
    return ($picked -join ',')
}


function Read-Placement {
    <#
      Parse a server's stderr log into the placement it ACTUALLY achieved.
      Returns $null when the log carries no placement lines at all -- which means
      the server was not launched with -lv 5, not that placement was fine.
    #>
    param([Parameter(Mandatory)][string]$LogPath)
    if (-not (Test-Path $LogPath)) { return $null }
    $text = Get-Content -Raw -Path $LogPath -ErrorAction SilentlyContinue
    if (-not $text) { return $null }

    $used = @()
    # Greedy to the LAST ')' on the line, not the first. Device names contain nested
    # parens -- "Intel(R) Arc(TM) Pro B70 Graphics" -- so a lazy [^)]* captures "Intel(R"
    # and the B70 test then fails on every real line. A dual-B70 log read as zero B70s.
    foreach ($m in [regex]::Matches($text, 'using device\s+(Vulkan\d+)\s*\((.*)\)')) {
        $used += [pscustomobject]@{ Handle = $m.Groups[1].Value; Name = $m.Groups[2].Value.Trim() }
    }
    $buffers = @()
    # Deliberately NOT line-anchored: llama-server prefixes each line with an elapsed
    # stamp and a subsystem tag ("1.11.449.041 I mod load_tensors: ..."), and an anchored
    # pattern silently matches nothing against that format -- which would look exactly
    # like "no placement lines" and fail open into a false UNPROVABLE.
    foreach ($m in [regex]::Matches($text, '(\S+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB')) {
        $buffers += [pscustomobject]@{ Handle = $m.Groups[1].Value; MiB = [double]$m.Groups[2].Value }
    }
    if ($used.Count -eq 0 -and $buffers.Count -eq 0) { return $null }

    $b70Used = @($used | Where-Object { $_.Name -match 'B70' })
    $igpuUsed = @($used | Where-Object { $_.Name -notmatch 'B70' })
    # A device with a real weight buffer is CARRYING the model; a device that merely
    # appears in "using device" may still hold nothing (the one-card defect looked
    # exactly like this from the outside).
    $loaded = @($buffers | Where-Object { $_.MiB -gt 1.0 })

    return [pscustomobject]@{
        UsedDevices   = $used
        Buffers       = $buffers
        B70Count      = $b70Used.Count
        IGpuCount     = $igpuUsed.Count
        LoadedCount   = $loaded.Count
        LoadedMiB     = @($loaded | ForEach-Object { $_.MiB })
        Summary       = (($used | ForEach-Object { "$($_.Handle)=$($_.Name)" }) -join ', ')
        BufferSummary = (($buffers | ForEach-Object { "$($_.Handle)=$([math]::Round($_.MiB,1))MiB" }) -join ', ')
    }
}

function Assert-Placement {
    <#
      Verify a cell got the placement it intended. THROWS on mismatch -- a cell that
      cannot prove its placement must not contribute a timing, because a
      correct-but-degraded run returns entirely plausible numbers.

      -Expect both-b70        : two B70s, both carrying weights
              one-b70         : exactly one device carrying weights, and it is a B70
              igpu-plus-b70   : at least one iGPU and at least one B70 in service
    #>
    param(
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][ValidateSet('both-b70', 'one-b70', 'igpu-plus-b70')][string]$Expect,
        [string]$Cell = '(unnamed)'
    )
    $p = Read-Placement -LogPath $LogPath
    if ($null -eq $p) {
        throw ("PLACEMENT UNPROVABLE [$Cell]: no 'using device' or 'model buffer size' lines in " +
               "$LogPath. llama-server emits none at the default verbosity -- launch it with " +
               "'-lv 5'. Refusing to trust a timing whose placement cannot be asserted (ADR-0042).")
    }

    $ok = switch ($Expect) {
        'both-b70'      { $p.B70Count -ge 2 -and $p.LoadedCount -ge 2 }
        'one-b70'       { $p.B70Count -ge 1 -and $p.LoadedCount -eq 1 }
        'igpu-plus-b70' { $p.IGpuCount -ge 1 -and $p.B70Count -ge 1 }
    }
    if (-not $ok) {
        throw ("PLACEMENT MISMATCH [$Cell]: expected '$Expect' but the server reported " +
               "B70s=$($p.B70Count) iGPUs=$($p.IGpuCount) devices-carrying-weights=$($p.LoadedCount). " +
               "using: $($p.Summary). buffers: $($p.BufferSummary). " +
               "Per ADR-0042 this is what a reshuffled enumeration looks like -- the run is void.")
    }
    Write-Host ("  placement OK [$Cell] expect=$Expect :: $($p.Summary) | $($p.BufferSummary)")
    return $p
}
