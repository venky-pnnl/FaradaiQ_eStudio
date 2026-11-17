param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Manifest
)

$ErrorActionPreference = 'Stop'
$rootPath = [System.IO.Path]::GetFullPath($Root)
$manifestPath = [System.IO.Path]::GetFullPath($Manifest)

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Write-Error "Package payload manifest is missing: $manifestPath"
    exit 2
}

$bad = New-Object System.Collections.Generic.List[string]
$checked = 0
foreach ($line in Get-Content -LiteralPath $manifestPath -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $match = [regex]::Match($trimmed, '^([0-9A-Fa-f]{64})\s{2}(.+)$')
    if (-not $match.Success) {
        $bad.Add("MALFORMED MANIFEST LINE: $trimmed")
        continue
    }
    $expected = $match.Groups[1].Value.ToLowerInvariant()
    $relative = $match.Groups[2].Value.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $rootPath $relative))
    if (-not $candidate.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        $bad.Add("PATH ESCAPES PACKAGE ROOT: $relative")
        continue
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $bad.Add("MISSING PAYLOAD FILE: $relative")
        continue
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash.ToLowerInvariant()
    $checked += 1
    if ($actual -ne $expected) {
        $bad.Add("PAYLOAD HASH MISMATCH: $relative expected=$expected actual=$actual")
    }
}

if ($checked -eq 0) {
    Write-Error "Payload manifest contains no verifiable files."
    exit 3
}
if ($bad.Count -gt 0) {
    foreach ($item in $bad) { Write-Error $item }
    Write-Error "FaradaIQ Studio v320 package integrity failed for $($bad.Count) item(s)."
    exit 4
}

Write-Output "FaradaIQ Studio v320 package payload integrity PASS: $checked file(s) verified."
exit 0
