param(
    [switch]$IncludeFlash,
    [switch]$IncludeFlashQ2,
    [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$doc = Get-Content -LiteralPath $script:Q38ArtifactsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$selected = @($doc.artifacts | Where-Object {
    $_.id -in @('qwen38-27b', 'qwen38-27b-mtp', 'qwen38-27b-mmproj') -or
    (($IncludeFlash -or $IncludeFlashQ2) -and $_.id -eq 'qwen38-flash-next-iq4') -or
    (($IncludeFlash -or $IncludeFlashQ2) -and $_.id -eq 'qwen38-flash-next-mmproj') -or
    ($IncludeFlashQ2 -and $_.id -eq 'qwen38-flash-next-q2')
})

$hf = Get-Command hf -ErrorAction SilentlyContinue
if (-not $hf) { $hf = Get-Command huggingface-cli -ErrorAction SilentlyContinue }
if (-not $hf -and -not $WhatIf) {
    throw 'Install the Hugging Face CLI with: python -m pip install --upgrade huggingface_hub'
}
$hfCommand = if ($hf) { $hf.Source } else { 'hf' }

foreach ($artifact in $selected) {
    $destination = [string](Get-Q38Property -Object $artifact -Name 'local_dir' -Default (Split-Path -Parent ([string]$artifact.path)))
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $include = [string]$artifact.filename
    if ([int](Get-Q38Property -Object $artifact -Name 'parts' -Default 1) -gt 1) {
        $include = ($include -replace '00001-of-\d{5}', '*')
    }
    $arguments = @('download', [string]$artifact.repo, '--revision', [string]$artifact.revision, '--include', $include, '--local-dir', $destination)
    Write-Host ("{0} {1}" -f $hfCommand, ($arguments -join ' '))
    if (-not $WhatIf) {
        & $hfCommand @arguments
        if ($LASTEXITCODE -ne 0) { throw "Download failed for $($artifact.id)" }
    }
}
Write-Host 'Acquisition complete. Run qwen38_campaign.py lock before any model load.'
