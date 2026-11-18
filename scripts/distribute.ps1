[CmdletBinding()]
param([ValidateSet('Configure','Update','Upload')][string]$Action='Update', [string]$Repository='venky-pnnl/FaradaiQ_eStudio', [ValidateSet('public','private')][string]$Visibility='public', [switch]$NoLaunch)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=Split-Path $PSScriptRoot -Parent
$configRoot=Join-Path $env:LOCALAPPDATA 'FaradaIQ\eStudio'
$configFile=Join-Path $configRoot 'github.json'
$portableGh=Join-Path $configRoot 'tools\GitHubCLI\gh.exe'
. (Join-Path $PSScriptRoot 'github_cli_portable.ps1')
if ($PSBoundParameters.ContainsKey('Repository') -eq $false -and (Test-Path -LiteralPath $configFile)) {
    $Repository=(Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json).repository
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Repository must have the form owner/name.' }

function Find-Application([string]$Name) {
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
}

function Add-ProcessPath([string[]]$Paths) {
    $separator = [IO.Path]::PathSeparator
    $pathsToAdd = @($Paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $env:Path = (@(($env:Path -split $separator) + $pathsToAdd) |
        Where-Object { $_ } | Select-Object -Unique) -join $separator
}

function Update-ProcessPath {
    $separator = [IO.Path]::PathSeparator
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    Add-ProcessPath -Paths (($machine -split $separator) + ($user -split $separator))
}

function Find-GitHubTool([string]$Name, [string]$RelativePath) {
    $path = Find-Application $Name
    if ($path) { return $path }

    Update-ProcessPath
    $path = Find-Application $Name
    if ($path) { return $path }

    $installRoots = @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)},
        (Join-Path $env:LOCALAPPDATA 'Programs'))
    foreach ($installRoot in $installRoots) {
        if ([string]::IsNullOrWhiteSpace($installRoot)) { continue }
        $candidate = Join-Path $installRoot $RelativePath
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Add-ProcessPath -Paths (Split-Path -LiteralPath $candidate)
            return $candidate
        }
    }
    if ($Name -eq 'gh' -and (Test-Path -LiteralPath $portableGh -PathType Leaf)) {
        Add-ProcessPath -Paths (Split-Path -LiteralPath $portableGh)
        return $portableGh
    }
}

function Require-GitHubTool([string]$Name, [string]$RelativePath, [string]$PackageId, [string]$DownloadPage) {
    $path = Find-GitHubTool $Name $RelativePath
    if ($path) {
        Write-Host "Found $Name at $path"
        return $path
    }

    $winget = Find-Application 'winget'
    if (-not $winget) {
        $aliasPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
        if (Test-Path -LiteralPath $aliasPath -PathType Leaf -ErrorAction SilentlyContinue) { $winget = $aliasPath }
    }
    $reason = 'WinGet is unavailable'
    if ($winget) {
        Write-Host "Installing $PackageId with WinGet. Respond to any Windows installation prompt."
        $ErrorActionPreference = 'Continue'
        $PSNativeCommandUseErrorActionPreference = $false
        try {
            & $winget install --id $PackageId --exact --source winget --accept-source-agreements --accept-package-agreements | Out-Host
            $reason = "WinGet exit code $LASTEXITCODE and $Name is still unavailable"
        } catch {
            $reason = "WinGet could not start: $($_.Exception.Message)"
        } finally {
            $ErrorActionPreference = 'Stop'
        }
        $path = Find-GitHubTool $Name $RelativePath
        if ($path) {
            Write-Host "Found $Name at $path"
            return $path
        }
    }
    if ($Name -eq 'gh') {
        Write-Host "$reason. Trying the official portable GitHub CLI for this Windows user."
        try {
            $path = Install-PortableGitHubCli -Target $portableGh
        } catch {
            throw "GitHub CLI setup failed: $($_.Exception.Message) Install the Windows MSI from https://github.com/cli/cli/releases/latest and rerun this batch file."
        }
        Add-ProcessPath -Paths (Split-Path -LiteralPath $path)
        return $path
    }
    throw "Could not find $Name. $reason. Install $PackageId from $DownloadPage, then rerun this batch file."
}

function Test-NativeCommand([string]$Executable, [string[]]$Arguments) {
    # Windows PowerShell 5.1 can turn redirected native stderr into a terminating error under Stop.
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    & $Executable @Arguments *> $null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-Gh([string[]]$Arguments) {
    & $script:gh @Arguments
    if ($LASTEXITCODE -ne 0) { throw "GitHub command failed: gh $($Arguments[0]) $($Arguments[1])" }
}
function Configure-Repository {
    $script:git = Require-GitHubTool 'git' 'Git\cmd\git.exe' 'Git.Git' 'https://git-scm.com/install/win'
    $script:gh = Require-GitHubTool 'gh' 'GitHub CLI\gh.exe' 'GitHub.cli' 'https://cli.github.com/'
    if (-not (Test-NativeCommand $script:gh @('auth','status','--hostname','github.com'))) {
        Write-Host 'Sign in to GitHub in your browser to continue.'
        & $script:gh auth login --hostname github.com --web --git-protocol https
        if ($LASTEXITCODE -ne 0) { throw 'GitHub sign-in failed. Complete browser authorization and rerun this batch file.' }
    }
    Invoke-Gh -Arguments @('auth','setup-git','--hostname','github.com') | Out-Host
    if (-not (Test-NativeCommand $script:gh @('repo','view',$Repository,'--json','name'))) {
        Invoke-Gh -Arguments @('repo','create',$Repository,('--'+$Visibility),'--description','Electrochemical Intelligence for Analyze, Control and Discover - data analysis, scientific visualization, and SciLink workflows.') | Out-Host
    }
    New-Item -ItemType Directory -Path $configRoot -Force | Out-Null
    @{repository=$Repository;install_root='C:\FaradaIQ\eStudio';channel='latest-release'} | ConvertTo-Json | Set-Content -LiteralPath $configFile -Encoding UTF8
    Write-Output "Configured https://github.com/$Repository . No credentials are stored in the app."
}
if ($Action -eq 'Configure') { Configure-Repository; exit 0 }
if ($Action -eq 'Update') {
    $headers=@{'User-Agent'='FaradaIQ-eStudio-Updater';'Accept'='application/vnd.github+json'}
    $script:gh = Find-GitHubTool 'gh' 'GitHub CLI\gh.exe'
    $useGh = $script:gh -and (Test-NativeCommand $script:gh @('auth','status','--hostname','github.com'))
    if ($useGh) {
        $latest=(Invoke-Gh -Arguments @('api',"repos/$Repository/releases/latest") | Out-String | ConvertFrom-Json)
    } else { $latest=Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -Headers $headers }
    $assets=@($latest.assets | Where-Object { $_.name -match '^FaradaIQ_eStudio_v[0-9]+_package\.zip$' })
    if ($assets.Count -ne 1) { throw 'Latest release must have exactly one eStudio package ZIP. Ask the maintainer to upload a release.' }
    $asset=$assets[0]
    $hashAssets=@($latest.assets | Where-Object { $_.name -eq ($asset.name + '.sha256') })
    if ($hashAssets.Count -ne 1) { throw 'Release SHA-256 asset is missing.' }
    $temp=Join-Path ([IO.Path]::GetTempPath()) ('FaradaIQ_eStudio_' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        if ($useGh) {
            Invoke-Gh -Arguments @('release','download',$latest.tag_name,'--repo',$Repository,'--pattern',$asset.name,'--pattern',($asset.name+'.sha256'),'--dir',$temp) | Out-Host
        } else {
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile (Join-Path $temp $asset.name) -Headers $headers
            Invoke-WebRequest -UseBasicParsing -Uri $hashAssets[0].browser_download_url -OutFile (Join-Path $temp ($asset.name+'.sha256')) -Headers $headers
        }
        $zip=Join-Path $temp $asset.name
        $hashLine=(Get-Content -LiteralPath ($zip+'.sha256') -Raw).Trim()
        if ($hashLine -notmatch '^([a-fA-F0-9]{64})\s+\*?([^\r\n]+)$') { throw 'Invalid release SHA-256 format.' }
        $expected=$Matches[1];$hashName=$Matches[2].Trim()
        if ($hashName -ne $asset.name) { throw 'Checksum names a different package.' }
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $expected) { throw 'Release download failed SHA-256 verification.' }
        $payload=Join-Path $temp 'payload'
        # Validate ZIP names before extracting; prevent paths outside the payload.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive=[IO.Compression.ZipFile]::OpenRead($zip)
        try { foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match '(^[\\/]|^[A-Za-z]:|(^|[\\/])\.\.([\\/]|$))') { throw 'Unsafe ZIP path.' }
        }} finally { $archive.Dispose() }
        Expand-Archive -LiteralPath $zip -DestinationPath $payload
        $metadata=Get-Content -LiteralPath (Join-Path $payload 'release.json') -Raw | ConvertFrom-Json
        if ($metadata.version -ne $latest.tag_name -or $metadata.package_asset -ne $asset.name) { throw 'Release metadata/tag mismatch.' }
        $env:FARADAIQ_SOURCE_PACKAGE_ZIP=$zip
        & (Join-Path $payload 'scripts\install.ps1') -Root $payload -NoLaunch:$NoLaunch
        if (-not $?) { throw 'Installation failed.' }
    } finally { Remove-Item Env:\FARADAIQ_SOURCE_PACKAGE_ZIP -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}
# Publish the exact build payload on the current remote main history.
Configure-Repository
Push-Location $root
try {
    $metadata=Get-Content -LiteralPath 'release.json' -Raw | ConvertFrom-Json
    if (Test-NativeCommand $script:gh @('release','view',$metadata.version,'--repo',$Repository)) {
        throw "Release $($metadata.version) already exists. Increment the version; existing releases are never overwritten."
    }
    if (Test-Path -LiteralPath (Join-Path $root '.git')) {
        if (Test-NativeCommand $script:git @('remote','get-url','origin')) {
            $origin=(& $script:git remote get-url origin | Out-String).Trim()
            if ($origin -notin @("https://github.com/$Repository.git","https://github.com/$Repository","git@github.com:$Repository.git")) {
                throw "The source folder has a different GitHub origin. Verify the configured repository before uploading."
            }
        }
    }
    $python=Join-Path $root '.faradaiq_venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { $python='python' }
    $authorName=''
    $authorEmail=''
    if (Test-NativeCommand $script:git @('config','--get','user.name')) {
        $authorName=(& $script:git config --get user.name | Out-String).Trim()
    }
    if (Test-NativeCommand $script:git @('config','--get','user.email')) {
        $authorEmail=(& $script:git config --get user.email | Out-String).Trim()
    }
    if (-not $authorName -or -not $authorEmail) {
        $user=(Invoke-Gh -Arguments @('api','user') | Out-String | ConvertFrom-Json)
        $authorName=$user.login
        $authorEmail="$($user.id)+$($user.login)@users.noreply.github.com"
    }
    & $python (Join-Path $PSScriptRoot 'build_release.py') --from-manifest
    if ($LASTEXITCODE -ne 0) { throw 'Release build failed.' }
    $package=Join-Path $root ('dist\'+$metadata.package_asset)
    Write-Host "Fetching GitHub main and publishing the verified $($metadata.version) source files."
    $publisher=@((Join-Path $PSScriptRoot 'publish_payload.py'), '--zip', $package,
        '--git', $script:git, '--remote', "https://github.com/$Repository.git",
        '--author-name', $authorName, '--author-email', $authorEmail)
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        $sha=(& $python @publisher | Out-String).Trim()
        $publishExit=$LASTEXITCODE
    } finally { $ErrorActionPreference='Stop' }
    if ($publishExit -ne 0 -or $sha -notmatch '^[a-f0-9]{40}$') { throw 'Source upload failed. Review the Git diagnostic above.' }
    Invoke-Gh -Arguments @('release','create',$metadata.version,$package,($package+'.sha256'),'--repo',$Repository,'--target',$sha,'--latest','--title',"FaradaIQ eStudio $($metadata.version)",'--notes-file',(Join-Path $root "RELEASE_NOTES_$($metadata.version).md")) | Out-Host
    Write-Output "Published https://github.com/$Repository/releases/tag/$($metadata.version)"
} finally { Pop-Location }
