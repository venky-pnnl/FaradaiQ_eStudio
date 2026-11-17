# Install the official portable GitHub CLI for the current Windows user.
function Install-PortableGitHubCli([string]$Target) {
    $ErrorActionPreference = 'Stop'
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    $assetArchitecture = switch ($architecture) {
        'AMD64' { 'amd64' }
        'ARM64' { 'arm64' }
        'x86' { '386' }
        default { throw "Unsupported Windows processor architecture: $architecture." }
    }

    $directory = Split-Path -Parent $Target
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $stage = Join-Path $directory ('.download-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $oldTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            [Net.ServicePointManager]::SecurityProtocol = $oldTls -bor [Net.SecurityProtocolType]::Tls12
        }
        Write-Host 'Checking the official GitHub CLI release...'
        $headers = @{ 'User-Agent' = 'FaradaIQ-eStudio'; 'Accept' = 'application/vnd.github+json' }
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/cli/cli/releases/latest' -Headers $headers -TimeoutSec 30
        $tag = [string]$release.tag_name
        if ($tag -cnotmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Unexpected GitHub CLI release tag.' }
        $assetName = 'gh_' + $tag.Substring(1) + "_windows_$assetArchitecture.zip"
        $assets = @($release.assets | Where-Object { $_.name -ceq $assetName })
        if ($assets.Count -ne 1) { throw "Could not identify the official asset $assetName." }
        $digest = $assets[0].PSObject.Properties['digest']
        if ($null -eq $digest -or [string]$digest.Value -notmatch '^sha256:([0-9a-f]{64})$') {
            throw "GitHub did not publish a usable SHA-256 digest for $assetName."
        }
        $expected = $Matches[1]
        $zip = Join-Path $stage $assetName
        Write-Host "Downloading $assetName from GitHub..."
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/cli/cli/releases/download/$tag/$assetName" -OutFile $zip -TimeoutSec 180
        Write-Host 'Verifying the GitHub CLI archive SHA-256...'
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $expected) {
            throw "SHA-256 verification failed for $assetName."
        }

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($zip)
        $stagedExe = Join-Path $stage 'gh.exe'
        try {
            $entries = @($archive.Entries | Where-Object { $_.FullName -ceq 'bin/gh.exe' })
            if ($entries.Count -ne 1 -or $entries[0].Length -le 0) {
                throw 'The verified archive does not contain the expected gh.exe entry.'
            }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $stagedExe)
        } finally {
            $archive.Dispose()
        }
        if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
            try {
                [IO.File]::Move($stagedExe, $Target)
            } catch {
                if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw }
            }
        }
        Write-Host "GitHub CLI is ready at $Target"
    } finally {
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            [Net.ServicePointManager]::SecurityProtocol = $oldTls
        }
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $Target
}
