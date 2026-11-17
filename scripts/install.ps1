[CmdletBinding()]
param([string]$Root = '', [string]$InstallRoot = 'C:\FaradaIQ\eStudio', [switch]$NoLaunch, [switch]$Plan)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Root)) {
    $scriptFolder = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($scriptFolder) -and $PSCommandPath) { $scriptFolder = [IO.Path]::GetDirectoryName($PSCommandPath) }
    if ([string]::IsNullOrWhiteSpace($scriptFolder)) { throw 'Cannot locate this script. Supply -Root with the extracted package folder.' }
    $Root = [IO.Path]::GetDirectoryName($scriptFolder)
}

Set-StrictMode -Version Latest
$Root = [IO.Path]::GetFullPath($Root)
$release = Get-Content -LiteralPath (Join-Path $Root 'release.json') -Raw | ConvertFrom-Json
$target = Join-Path $InstallRoot ('V' + $release.version_number)
$launcher = 'Start_FaradaIQ_eStudio_' + $release.version + '.bat'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { $desktop = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Desktop' }
$shortcutPath = Join-Path $desktop 'Holaris Apps\FaradaIQ eStudio.lnk'
if ($Plan) { Write-Output "Install $($release.app_name) $($release.version) -> $target"; Write-Output "Shortcut -> $shortcutPath"; exit 0 }
$logRoot = Join-Path $env:LOCALAPPDATA 'FaradaIQ\eStudio\logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot ('install_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
Start-Transcript -Path $log | Out-Null
try {
    & (Join-Path $PSScriptRoot 'verify.ps1') -Root $Root
    $installedMarker = Join-Path $target 'installation_complete.json'
    $validInstallation = Test-Path -LiteralPath $installedMarker
    if ($validInstallation) {
        try {
            & (Join-Path $target 'scripts\verify.ps1') -Root $target
            $validInstallation = (Test-Path -LiteralPath (Join-Path $target $launcher)) -and (Test-Path -LiteralPath (Join-Path $target '.faradaiq_venv\Scripts\python.exe'))
        } catch { $validInstallation = $false }
    }
    if (-not $validInstallation) {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        $manifest = Get-Content -LiteralPath (Join-Path $Root 'PACKAGE_MANIFEST_SHA256_v331.txt')
        foreach ($line in $manifest) {
            if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw 'Malformed payload manifest.' }
            $relative = $Matches[2]
            $from = Join-Path $Root $relative
            $to = Join-Path $target $relative
            New-Item -ItemType Directory -Path (Split-Path $to -Parent) -Force | Out-Null
            if ([IO.Path]::GetFullPath($from) -ne [IO.Path]::GetFullPath($to)) { Copy-Item -LiteralPath $from -Destination $to -Force }
        }
        if ($Root.TrimEnd('\','/') -ne [IO.Path]::GetFullPath($target).TrimEnd('\','/')) {
            Copy-Item -LiteralPath (Join-Path $Root 'PACKAGE_MANIFEST_SHA256_v331.txt') -Destination $target -Force
        }
        & (Join-Path $target 'scripts\verify.ps1') -Root $target
        # Invoke bootstrap in the final version folder; virtual environments must not be moved.
        & $env:ComSpec /d /c "`"$(Join-Path $target $launcher)`" --install-only"
        if ($LASTEXITCODE -ne 0) { throw "Python bootstrap failed (exit $LASTEXITCODE). The previous app shortcut is unchanged." }
        $archive = $env:FARADAIQ_SOURCE_PACKAGE_ZIP
        if (-not $archive) {
            $candidate = Join-Path (Split-Path $Root -Parent) $release.package_asset
            if (Test-Path -LiteralPath $candidate) { $archive = $candidate }
            if (-not $archive) {
                $pattern = [IO.Path]::GetFileNameWithoutExtension($release.package_asset) + '*.zip'
                $matches = @(Get-ChildItem -LiteralPath (Split-Path $Root -Parent) -Filter $pattern -File)
                if ($matches.Count -eq 1) { $archive = $matches[0].FullName }
            }
        }
        if ($archive -and (Test-Path -LiteralPath $archive)) {
            $savedArchive = Join-Path $target $release.package_asset
            if ([IO.Path]::GetFullPath($archive) -ne [IO.Path]::GetFullPath($savedArchive)) { Copy-Item -LiteralPath $archive -Destination $savedArchive -Force }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $savedArchive -Algorithm SHA256).Hash) { throw 'Original archive copy verification failed.' }
            Write-Output "Preserved original installation ZIP: $savedArchive"
        } else { Write-Warning 'Original installation ZIP was not found beside the extracted folder. Keep it there when installing to preserve an unchanged copy.' }
        @{version=$release.version;installed_at=(Get-Date -Format o);target=$target} | ConvertTo-Json | Set-Content -LiteralPath $installedMarker -Encoding UTF8
    } else { Write-Output 'This version is already installed; repairing the stable shortcut.' }
    # Switch the stable shortcut only after verified copy and successful bootstrap.
    New-Item -ItemType Directory -Path (Split-Path $shortcutPath -Parent) -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $env:ComSpec
    $shortcut.Arguments = '/d /c ""' + (Join-Path $target $launcher) + '""'
    $shortcut.WorkingDirectory = $target
    $shortcut.IconLocation = (Join-Path $target 'assets\estudio.ico') + ',0'
    $shortcut.Description = "$($release.app_name) $($release.version)"
    $shortcut.Save()
    @{version=$release.version;target=$target;launcher=(Join-Path $target $launcher)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'current.json') -Encoding UTF8
    Write-Output "Installed: $target"
    Write-Output "Shortcut: $shortcutPath"
    if (-not $NoLaunch) { Start-Process -FilePath (Join-Path $target $launcher) -WorkingDirectory $target }
} finally { Stop-Transcript | Out-Null }
