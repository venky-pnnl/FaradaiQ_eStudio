[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Root = '',

    [Parameter(Mandatory = $false)]
    [string]$VenvName = ".faradaiq_venv",

    [Parameter(Mandatory = $false)]
    [switch]$AllowPythonInstall
)

if ([string]::IsNullOrWhiteSpace($Root)) { $Root = $PSScriptRoot }
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$rootPath = [System.IO.Path]::GetFullPath($Root)
$venvPath = Join-Path $rootPath $VenvName
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$logRoot = if ($env:FARADAIQ_SESSION_DIR) {
    Join-Path $env:FARADAIQ_SESSION_DIR "logs"
} else {
    Join-Path $env:TEMP "FaradaIQ_eStudio_v331"
}
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logPath = Join-Path $logRoot ("bootstrap_{0}.log" -f ($VenvName.TrimStart('.') -replace '[^A-Za-z0-9_.-]', '_'))
$reportPath = Join-Path $logRoot ("bootstrap_{0}.json" -f ($VenvName.TrimStart('.') -replace '[^A-Za-z0-9_.-]', '_'))

function Write-BootstrapLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Test-VenvPython {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    & $PythonPath -c "import sys, venv; assert sys.prefix != ''; print('VENV_OK')" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-PythonCandidate {
    param(
        [string]$Executable,
        [string[]]$PrefixArguments,
        [string]$Label
    )

    if ([System.IO.Path]::IsPathRooted($Executable)) {
        if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
            return $null
        }
    } elseif (-not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        return $null
    }

    $probeCode = "import sys,venv; print('PY_OK'); print(sys.executable); print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $output = @(& $Executable @PrefixArguments -c $probeCode 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -or -not ($output -contains "PY_OK")) {
        return $null
    }

    $resolvedExecutable = [string]($output | Select-Object -Skip 1 -First 1)
    $version = [string]($output | Select-Object -Skip 2 -First 1)
    if (-not $resolvedExecutable) {
        $resolvedExecutable = $Executable
    }

    return [pscustomobject]@{
        Executable = $Executable
        PrefixArguments = @($PrefixArguments)
        ResolvedExecutable = $resolvedExecutable
        Version = $version
        Label = $Label
    }
}

function Find-SeedPython {
    $candidateSpecs = @(
        @{ Executable = "py"; Arguments = @("-3.12"); Label = "Python launcher 3.12" },
        @{ Executable = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Arguments = @(); Label = "User Python 3.12" },
        @{ Executable = "C:\Program Files\Python312\python.exe"; Arguments = @(); Label = "Program Files Python 3.12" },
        @{ Executable = "py"; Arguments = @("-3.11"); Label = "Python launcher 3.11" },
        @{ Executable = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"; Arguments = @(); Label = "User Python 3.11" },
        @{ Executable = "C:\Program Files\Python311\python.exe"; Arguments = @(); Label = "Program Files Python 3.11" },
        @{ Executable = "py"; Arguments = @("-3.13"); Label = "Python launcher 3.13" },
        @{ Executable = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"; Arguments = @(); Label = "User Python 3.13" },
        @{ Executable = "C:\Program Files\Python313\python.exe"; Arguments = @(); Label = "Program Files Python 3.13" },
        @{ Executable = "py"; Arguments = @("-3.10"); Label = "Python launcher 3.10" },
        @{ Executable = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"; Arguments = @(); Label = "User Python 3.10" },
        @{ Executable = "C:\Program Files\Python311\python.exe"; Arguments = @(); Label = "Program Files Python 3.10" },
        @{ Executable = "python"; Arguments = @(); Label = "python.exe on PATH" },
        @{ Executable = "python3"; Arguments = @(); Label = "python3.exe on PATH" },
        @{ Executable = "py"; Arguments = @("-3"); Label = "Default Python 3 launcher" }
    )

    foreach ($spec in $candidateSpecs) {
        $candidate = Test-PythonCandidate -Executable $spec.Executable -PrefixArguments $spec.Arguments -Label $spec.Label
        if ($null -ne $candidate) {
            return $candidate
        }
    }
    return $null
}

function Install-Python312Automatically {
    if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        Write-BootstrapLog "winget is unavailable; automatic Python installation cannot continue."
        return $false
    }

    Write-BootstrapLog "No usable Python runtime was found. Installing Python 3.12 automatically with winget; no prompt will be shown."
    $arguments = @(
        "install", "-e", "--id", "Python.Python.3.12", "--scope", "user",
        "--silent", "--disable-interactivity", "--accept-package-agreements", "--accept-source-agreements"
    )
    & winget @arguments
    if ($LASTEXITCODE -eq 0) {
        return $true
    }

    Write-BootstrapLog "The first winget command failed; retrying without --disable-interactivity while retaining silent acceptance flags."
    $arguments = @(
        "install", "-e", "--id", "Python.Python.3.12", "--scope", "user",
        "--silent", "--accept-package-agreements", "--accept-source-agreements"
    )
    & winget @arguments
    return ($LASTEXITCODE -eq 0)
}

$report = [ordered]@{
    app_version = "v331"
    root = $rootPath
    venv_name = $VenvName
    venv_path = $venvPath
    status = "started"
    existing_environment_reused = $false
    python_install_attempted = $false
    seed_label = ""
    seed_executable = ""
    seed_version = ""
    completed = ""
}

try {
    Write-BootstrapLog "FaradaIQ v331 automatic environment bootstrap started for $VenvName."

    if (Test-VenvPython -PythonPath $venvPython) {
        $report.status = "ready"
        $report.existing_environment_reused = $true
        $report.completed = (Get-Date).ToString("s")
        $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
        Write-BootstrapLog "Existing private environment is healthy: $venvPython"
        exit 0
    }

    if (Test-Path -LiteralPath $venvPath) {
        $brokenPath = "{0}.broken_{1}" -f $venvPath, (Get-Date -Format "yyyyMMdd_HHmmss")
        Write-BootstrapLog "Existing private environment is unusable. Moving it to $brokenPath"
        Move-Item -LiteralPath $venvPath -Destination $brokenPath -Force
    }

    $seed = Find-SeedPython
    if ($null -eq $seed -and $AllowPythonInstall) {
        $report.python_install_attempted = $true
        if (Install-Python312Automatically) {
            $seed = Find-SeedPython
        }
    }

    if ($null -eq $seed) {
        throw "No usable Python 3 runtime was found. Automatic Python 3.12 installation was unavailable or failed."
    }

    $report.seed_label = $seed.Label
    $report.seed_executable = $seed.ResolvedExecutable
    $report.seed_version = $seed.Version
    Write-BootstrapLog ("Selected {0}: {1} (Python {2})" -f $seed.Label, $seed.ResolvedExecutable, $seed.Version)

    $createArgs = @($seed.PrefixArguments) + @("-m", "venv", $venvPath)
    Write-BootstrapLog "Creating private environment at $venvPath"
    & $seed.Executable @createArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python exited with code $LASTEXITCODE while creating the private environment."
    }

    if (-not (Test-VenvPython -PythonPath $venvPython)) {
        throw "The private environment was created but its Python executable did not pass validation."
    }

    $report.status = "ready"
    $report.completed = (Get-Date).ToString("s")
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-BootstrapLog "Private environment is ready: $venvPython"
    exit 0
}
catch {
    $report.status = "failed"
    $report.error = $_.Exception.Message
    $report.completed = (Get-Date).ToString("s")
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-BootstrapLog ("ERROR: " + $_.Exception.Message)
    exit 1
}
