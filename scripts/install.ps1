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
$browserLauncher = 'Start_FaradaIQ_eStudio_Browser_' + $release.version + '.bat'
$privatePython = Join-Path $target '.faradaiq_venv\Scripts\python.exe'
$quietPython = Join-Path $target '.faradaiq_venv\Scripts\pythonw.exe'
$quietLauncher = Join-Path $target 'scripts\launch_estudio.pyw'
$scilinkRoot = if ($env:FARADAIQ_SCILINK_ROOT) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($env:FARADAIQ_SCILINK_ROOT)
} else { 'C:\FaradaIQ\SciLink' }
$scilinkPython = Join-Path $scilinkRoot 'bootstrap\Scripts\pythonw.exe'
$scilinkLauncher = Join-Path $scilinkRoot 'scilink_launch.py'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { $desktop = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Desktop' }
$shortcutPath = Join-Path $desktop 'Holaris Apps\FaradaIQ eStudio.lnk'
$browserShortcutPath = Join-Path $desktop 'Holaris Apps\FaradaIQ eStudio Browser.lnk'
$scilinkShortcutPath = Join-Path $desktop 'Holaris Apps\SciLink.lnk'
if ($Plan) {
    Write-Output "Install $($release.app_name) $($release.version) -> $target"
    Write-Output 'Setup order: eStudio core, DearEIS and pyDRTtools, managed SciLink, shortcuts and launch.'
    Write-Output "Desktop shortcut -> $shortcutPath"
    Write-Output "Browser shortcut -> $browserShortcutPath"
    Write-Output "Managed SciLink -> $scilinkRoot"
    Write-Output "SciLink shortcut -> $scilinkShortcutPath"
    exit 0
}
$logRoot = Join-Path $env:LOCALAPPDATA 'FaradaIQ\eStudio\logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot ('install_' + $release.version + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
Start-Transcript -Path $log | Out-Null
try {
    & (Join-Path $PSScriptRoot 'verify.ps1') -Root $Root
    $installedMarker = Join-Path $target 'installation_complete.json'
    $validInstallation = Test-Path -LiteralPath $installedMarker
    if ($validInstallation) {
        try {
            & (Join-Path $target 'scripts\verify.ps1') -Root $target
            $validInstallation = (Test-Path -LiteralPath $privatePython -PathType Leaf) -and (Test-Path -LiteralPath $quietPython -PathType Leaf)
        } catch { $validInstallation = $false }
    }
    if (-not $validInstallation) {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        $manifest = Get-Content -LiteralPath (Join-Path $Root 'PACKAGE_MANIFEST_SHA256_v341.txt')
        foreach ($line in $manifest) {
            if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw 'Malformed payload manifest.' }
            $relative = $Matches[2]
            $from = Join-Path $Root $relative
            $to = Join-Path $target $relative
            New-Item -ItemType Directory -Path (Split-Path $to -Parent) -Force | Out-Null
            if ([IO.Path]::GetFullPath($from) -ne [IO.Path]::GetFullPath($to)) { Copy-Item -LiteralPath $from -Destination $to -Force }
        }
        if ($Root.TrimEnd('\','/') -ne [IO.Path]::GetFullPath($target).TrimEnd('\','/')) {
            Copy-Item -LiteralPath (Join-Path $Root 'PACKAGE_MANIFEST_SHA256_v341.txt') -Destination $target -Force
        }
        & (Join-Path $target 'scripts\verify.ps1') -Root $target
    } else {
        Write-Output 'This version is already installed. Rechecking its runtime and integrations.'
    }

    Write-Output '[1/3] Checking the private eStudio runtime and core dependencies.'
    # Bootstrap and verify in the final install location on both fresh setup and rerun.
    & $env:ComSpec /d /c "`"$(Join-Path $target $launcher)`" --install-only"
    if ($LASTEXITCODE -ne 0) { throw "Python bootstrap failed (exit $LASTEXITCODE). The previous app shortcut is unchanged." }
    if (-not (Test-Path -LiteralPath $quietPython -PathType Leaf)) { throw 'Private Python is missing pythonw.exe. Reinstall Python 3.12 with Windows GUI support.' }

    if (-not $validInstallation) {
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
    }

    Write-Output '[2/3] Checking DearEIS and pyDRTtools.'
    $analysisExit = $null
    Push-Location -LiteralPath $target
    try {
        & $privatePython (Join-Path $target 'scripts\setup_analysis_tools.py')
        $analysisExit = $LASTEXITCODE
        if ($analysisExit -ne 0) { Write-Warning 'One or more optional analysis tools need attention. See the setup messages above.' }
    } catch {
        Write-Warning "Optional analysis setup could not run: $_"
    } finally {
        Pop-Location
    }

    Write-Output "[3/3] Checking for a verified SciLink update in $scilinkRoot"
    $previousSciLinkRoot = $env:FARADAIQ_SCILINK_ROOT
    $activeSciLink = $null
    Push-Location -LiteralPath $target
    try {
        $env:FARADAIQ_SCILINK_ROOT = $scilinkRoot
        & $privatePython -m faradaiq.managed_scilink update
        if ($LASTEXITCODE -ne 0) { Write-Warning 'SciLink update failed. A usable previous installation will be retained.' }
    } catch {
        Write-Warning "SciLink setup could not run: $_"
    } finally {
        try {
            $scilinkStatus = & $privatePython -m faradaiq.managed_scilink status
            if ($LASTEXITCODE -eq 0 -and $scilinkStatus) { $activeSciLink = $scilinkStatus | ConvertFrom-Json }
        } catch {
            Write-Warning "Could not verify the SciLink runtime: $_"
        } finally {
            $env:FARADAIQ_SCILINK_ROOT = $previousSciLinkRoot
            Pop-Location
        }
    }
    $scilinkReady = ($null -ne $activeSciLink) -and (Test-Path -LiteralPath $scilinkPython -PathType Leaf) -and
        (Test-Path -LiteralPath $scilinkLauncher -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $scilinkRoot 'active.json') -PathType Leaf)

    # Switch the stable shortcuts only after verified copy and successful bootstrap.
    New-Item -ItemType Directory -Path (Split-Path $shortcutPath -Parent) -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $quietPython
    $shortcut.Arguments = '"' + $quietLauncher + '" desktop'
    $shortcut.WorkingDirectory = $target
    $shortcut.IconLocation = (Join-Path $target 'assets\estudio.ico') + ',0'
    $shortcut.Description = "$($release.app_name) $($release.version)"
    $shortcut.Save()

    $browserShortcut = $shell.CreateShortcut($browserShortcutPath)
    $browserShortcut.TargetPath = $quietPython
    $browserShortcut.Arguments = '"' + $quietLauncher + '" browser'
    $browserShortcut.WorkingDirectory = $target
    $browserShortcut.IconLocation = (Join-Path $target 'assets\estudio.ico') + ',0'
    $browserShortcut.Description = "$($release.app_name) Browser $($release.version)"
    $browserShortcut.Save()

    $scilinkArguments = '"' + $scilinkLauncher + '" --root .'
    if ($scilinkReady) {
        try {
            & (Join-Path $target 'scripts\install_scilink_shortcut.ps1') -SciLinkRoot $scilinkRoot
        } catch {
            Write-Warning "SciLink is available at $scilinkRoot, but its desktop shortcut could not be refreshed: $_"
        }
    } else {
        Write-Warning 'No verified managed SciLink installation is available. Run this installer again to retry SciLink setup.'
    }

    $analysisReport = Join-Path $target ('analysis_tools_status_' + $release.version + '.json')
    $reportedAnalysisPath = if (Test-Path -LiteralPath $analysisReport -PathType Leaf) { $analysisReport } else { $null }
    @{version=$release.version;installed_at=(Get-Date -Format o);target=$target;analysis_tools_exit_code=$analysisExit;analysis_tools_report=$reportedAnalysisPath;scilink_ready=$scilinkReady} | ConvertTo-Json | Set-Content -LiteralPath $installedMarker -Encoding UTF8
    @{version=$release.version;target=$target;launcher=(Join-Path $target $launcher);browser_launcher=(Join-Path $target $browserLauncher)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'current.json') -Encoding UTF8
    Write-Output "Installed: $target"
    Write-Output "Desktop shortcut: $shortcutPath"
    Write-Output "Browser shortcut: $browserShortcutPath"
    if (-not $NoLaunch) {
        Start-Process -FilePath $quietPython -ArgumentList $shortcut.Arguments -WorkingDirectory $target
        Start-Process -FilePath $quietPython -ArgumentList $browserShortcut.Arguments -WorkingDirectory $target
        if ($scilinkReady) { Start-Process -FilePath $scilinkPython -ArgumentList $scilinkArguments -WorkingDirectory $scilinkRoot }
    }
} finally { Stop-Transcript | Out-Null }
