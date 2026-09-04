[CmdletBinding()]
param(
    [string]$CommandCenterRoot = 'C:\work\commandcenter',
    [string]$ResumeRoot = 'E:\resume'
)

$ErrorActionPreference = 'Stop'
$candidateRoot = Join-Path $CommandCenterRoot 'hearth\var\public-portfolio'
$systemCandidate = Join-Path $candidateRoot 'public-system-proof.v1.json'
$careerCandidate = Join-Path $candidateRoot 'public-career-proof.v1.json'

New-Item -ItemType Directory -Force -Path $candidateRoot | Out-Null

Push-Location $CommandCenterRoot
try {
    python -m hearth.projection.public_portfolio --out $systemCandidate
    if ($LASTEXITCODE -ne 0) { throw 'HEARTH public projection failed' }
}
finally {
    Pop-Location
}

Push-Location $ResumeRoot
try {
    python -m resume_pipeline export-public --output $careerCandidate
    if ($LASTEXITCODE -ne 0) { throw 'Public career claim export failed' }
}
finally {
    Pop-Location
}

$systemHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $systemCandidate).Hash.ToLowerInvariant()
$careerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $careerCandidate).Hash.ToLowerInvariant()
$stagedAt = [DateTimeOffset]::UtcNow.ToString('o')

Write-Output "Public portfolio candidates staged at $stagedAt"
Write-Output "  system $systemHash  $systemCandidate"
Write-Output "  career $careerHash  $careerCandidate"
Write-Output 'No public repository was changed. Review and promote explicitly from the site repository.'
