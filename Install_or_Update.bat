@echo off
setlocal EnableExtensions
set "ESTUDIO_UPDATE_SELF=%~f0"
set "ESTUDIO_NO_LAUNCH=0"
if /I "%~1"=="-NoLaunch" set "ESTUDIO_NO_LAUNCH=1"
if exist "%~dp0scripts\distribute.ps1" goto local
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content -LiteralPath $env:ESTUDIO_UPDATE_SELF -Raw; & ([ScriptBlock]::Create($s.Substring($s.LastIndexOf('#__ESTUDIO_UPDATER__')+20)))"
goto result
:local
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\distribute.ps1" -Action Update %*
:result
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
 echo.
 echo Update failed. Read the message above; a first release must be uploaded before this updater can install it.
 pause
)
exit /b %APP_EXIT%
#__ESTUDIO_UPDATER__
$ErrorActionPreference='Stop'
$Repository='venky-pnnl/FaradaiQ_eStudio'
$configFile=Join-Path $env:LOCALAPPDATA 'FaradaIQ\eStudio\github.json'
if(Test-Path -LiteralPath $configFile){$Repository=(Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json).repository}
if($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'){throw 'Invalid repository configuration.'}
$NoLaunch=($env:ESTUDIO_NO_LAUNCH -eq '1')
function Invoke-Gh([string[]]$Arguments) {
    $output=& gh @Arguments
    if ($LASTEXITCODE -ne 0) { throw "GitHub command failed: gh $($Arguments[0]) $($Arguments[1])" }
    return $output
}
    $headers=@{'User-Agent'='FaradaIQ-eStudio-Updater';'Accept'='application/vnd.github+json'}
    $useGh=$false
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        & gh auth status *> $null
        $useGh=($LASTEXITCODE -eq 0)
    }
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
