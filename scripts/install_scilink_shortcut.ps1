[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SciLinkRoot)

$ErrorActionPreference = 'Stop'
$SciLinkRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SciLinkRoot)
$python = Join-Path $SciLinkRoot 'bootstrap\Scripts\pythonw.exe'
$launcher = Join-Path $SciLinkRoot 'scilink_launch.py'
foreach ($required in @($python, $launcher, (Join-Path $SciLinkRoot 'active.json'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "SciLink startup file is missing: $required" }
}

$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { $desktop = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Desktop' }
$shortcutPath = Join-Path $desktop 'Holaris Apps\SciLink.lnk'
New-Item -ItemType Directory -Path (Split-Path $shortcutPath -Parent) -Force | Out-Null
$iconSource = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets\scilink.ico'
$icon = Join-Path $SciLinkRoot 'scilink.ico'
Copy-Item -LiteralPath $iconSource -Destination $icon -Force

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $python
$shortcut.Arguments = '"' + $launcher + '" --root .'
$shortcut.WorkingDirectory = $SciLinkRoot
$shortcut.IconLocation = $icon + ',0'
$shortcut.Description = 'SciLink local browser'
$shortcut.Save()
Write-Output "SciLink shortcut: $shortcutPath"
