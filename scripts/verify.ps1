[CmdletBinding()]
param([string]$Root = '')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Root)) {
    $scriptFolder = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($scriptFolder) -and $PSCommandPath) { $scriptFolder = [IO.Path]::GetDirectoryName($PSCommandPath) }
    if ([string]::IsNullOrWhiteSpace($scriptFolder)) { throw 'Cannot locate this script. Supply -Root with the extracted package folder.' }
    $Root = [IO.Path]::GetDirectoryName($scriptFolder)
}

$base = [IO.Path]::GetFullPath($Root).TrimEnd('\','/')
$manifest = Join-Path $base 'PACKAGE_MANIFEST_SHA256_v331.txt'
$count = 0
foreach ($line in Get-Content -LiteralPath $manifest) {
    if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw "Invalid manifest entry: $line" }
    $expected = $Matches[1]; $relative = $Matches[2]
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Unsafe manifest path.' }
    $file = [IO.Path]::GetFullPath((Join-Path $base $relative))
    if (-not $file.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Payload path is outside the package.' }
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing payload: $relative" }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $expected) { throw "SHA-256 mismatch: $relative" }
    $count++
}
if ($count -lt 10) { throw 'Payload manifest is incomplete.' }
Write-Output "Verified $count payload files."
