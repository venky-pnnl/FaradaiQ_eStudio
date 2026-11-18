@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "INSTALL_ONLY=0"
if /I "%~1"=="--install-only" set "INSTALL_ONLY=1"

set "APP_VERSION=v337"
set "SCRIPT=faradaiq_estudio_v337.py"
set "CHECKER=_check_bridge_dependencies.py"
set "REQ_ALL=requirements_all.txt"
set "BOOTSTRAP_PS1=_ensure_faradaiq_venv_v337.ps1"
set "VENV_DIR=.faradaiq_venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONLEGACYWINDOWSSTDIO=0"

rem All scientific services are available by default.
set "FARADAIQ_AI_LAUNCH_MODE=scilink_edison_llm"
set "FARADAIQ_EDISON_ENABLED=1"
set "FARADAIQ_LLM_TOOLS_ENABLED=1"
set "FARADAIQ_SCIENCE_MODE=scilink_edison_llm"
set "FARADAIQ_SKIP_STARTUP_AI_SELECTOR=1"

for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "FARADAIQ_LAUNCH_TS=%%I"
if not defined FARADAIQ_LAUNCH_TS set "FARADAIQ_LAUNCH_TS=%RANDOM%_%RANDOM%"
set "FARADAIQ_SESSION_ID=FQ_%FARADAIQ_LAUNCH_TS%_%RANDOM%%RANDOM%"
set "FARADAIQ_LAUNCH_ROOT=%CD%\FaradaIQ_launch_sessions"
set "FARADAIQ_SESSION_DIR=%FARADAIQ_LAUNCH_ROOT%\Launch_%FARADAIQ_LAUNCH_TS%"
set /a LAUNCH_SERIAL=1
:unique_launch_folder
if exist "%FARADAIQ_SESSION_DIR%" (
    set /a LAUNCH_SERIAL+=1
    set "FARADAIQ_SESSION_DIR=%FARADAIQ_LAUNCH_ROOT%\Launch_%FARADAIQ_LAUNCH_TS%_!LAUNCH_SERIAL!"
    goto unique_launch_folder
)
set "FARADAIQ_LAUNCH_BAT=%~f0"
set "FARADAIQ_APP_VERSION=v337"
set "FARADAIQ_RUNTIME_STATS_DIR=%CD%\FaradaIQ_runtime_estimates"
mkdir "%FARADAIQ_SESSION_DIR%\logs" >nul 2>&1
mkdir "%FARADAIQ_SESSION_DIR%\activity" >nul 2>&1
mkdir "%FARADAIQ_RUNTIME_STATS_DIR%" >nul 2>&1
(
  echo FaradaIQ eStudio launch session
  echo Session ID: %FARADAIQ_SESSION_ID%
  echo Created: %DATE% %TIME%
  echo Launch folder: %CD%
  echo Tracking folder only: %FARADAIQ_SESSION_DIR%
  echo BAT: %~f0
  echo App version: %FARADAIQ_APP_VERSION%
  echo Runtime estimate stats: %FARADAIQ_RUNTIME_STATS_DIR%
) > "%FARADAIQ_SESSION_DIR%\session_info.txt"

set "CHECK_FILE=%FARADAIQ_SESSION_DIR%\logs\dependency_check.txt"
title FaradaIQ eStudio v337 - %FARADAIQ_SESSION_ID%

echo ============================================================
echo  FaradaIQ eStudio v337 automatic launcher
echo  Private environment: %VENV_DIR%
echo ============================================================
echo.
echo No setup questions are asked. Python and package selection are automatic.
echo Uses Python 3.12 with Tk.
echo Base Python and Anaconda packages are not modified.
echo Session: %FARADAIQ_SESSION_ID%
echo.

if not exist "%SCRIPT%" (
    echo ERROR: Cannot find "%SCRIPT%" in "%CD%".
    exit /b 1
)
if not exist "%CHECKER%" (
    echo ERROR: Cannot find "%CHECKER%" in "%CD%".
    exit /b 1
)
if not exist "%REQ_ALL%" (
    echo ERROR: Cannot find "%REQ_ALL%" in "%CD%".
    exit /b 1
)
if not exist "%BOOTSTRAP_PS1%" (
    echo ERROR: Cannot find "%BOOTSTRAP_PS1%" in "%CD%".
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
    echo Python launcher inventory:
    py -0p 2>nul
    echo.
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\%BOOTSTRAP_PS1%" -Root "%CD%" -VenvName "%VENV_DIR%" -AllowPythonInstall
set "BOOTSTRAP_EXIT=!ERRORLEVEL!"
if not "!BOOTSTRAP_EXIT!"=="0" (
    echo.
    echo ERROR: Automatic private-environment creation failed with code !BOOTSTRAP_EXIT!.
    echo See: "%FARADAIQ_SESSION_DIR%\logs\bootstrap_faradaiq_venv.log"
    exit /b !BOOTSTRAP_EXIT!
)

if not exist "%VENV_PY%" (
    echo ERROR: Bootstrap reported success, but "%VENV_PY%" is missing.
    exit /b 1
)

"%VENV_PY%" -c "import sys; print('Private Python:', sys.executable); print('Version:', sys.version.replace(chr(10),' '))"
if errorlevel 1 (
    echo ERROR: The private Python environment cannot start.
    exit /b 1
)

echo.
echo Checking FaradaIQ GUI, pySECCM, DRT, and visualization dependencies...
"%VENV_PY%" "%CHECKER%" > "%CHECK_FILE%" 2>&1
set "CHECK_EXIT=!ERRORLEVEL!"
type "%CHECK_FILE%"
if "!CHECK_EXIT!"=="0" if "%INSTALL_ONLY%"=="1" (
    "%VENV_PY%" "%CD%\faradaiq_browser_v337.py" --check >> "%CHECK_FILE%" 2>&1
    set "CHECK_EXIT=!ERRORLEVEL!"
)

if not "!CHECK_EXIT!"=="0" (
    echo.
    echo Installing or repairing requirements inside %VENV_DIR% only...
    "%VENV_PY%" -m ensurepip --default-pip
    if errorlevel 1 (
        echo ERROR: ensurepip failed inside the private environment.
        exit /b 1
    )
    "%VENV_PY%" -m pip install --disable-pip-version-check --no-input --no-cache-dir --prefer-binary --upgrade -r "%REQ_ALL%"
    if errorlevel 1 (
        echo ERROR: Dependency installation failed inside the private environment.
        exit /b 1
    )
    "%VENV_PY%" "%CHECKER%"
    if errorlevel 1 (
        echo ERROR: Dependencies still do not import cleanly after automatic repair.
        exit /b 1
    )
)

if "%INSTALL_ONLY%"=="1" (
    echo Checking the local browser application...
    "%VENV_PY%" "%CD%\faradaiq_browser_v337.py" --check
    if errorlevel 1 (
        echo ERROR: The local browser application could not start in the private environment.
        exit /b 1
    )
)

if "%INSTALL_ONLY%"=="1" (
    echo.
    echo ============================================================
    echo FaradaIQ eStudio v337 automatic bootstrap completed.
    echo Installed folder: %CD%
    echo Launcher: %CD%\Start_FaradaIQ_eStudio_v337.bat
    echo ============================================================
    >> "%FARADAIQ_SESSION_DIR%\logs\launcher_events.log" echo [%DATE% %TIME%] Install-only bootstrap completed
    exit /b 0
)

rem Default service mode; change providers from Settings in the application.
set "FARADAIQ_AI_LAUNCH_MODE=scilink_edison_llm"
set "FARADAIQ_EDISON_ENABLED=1"
set "FARADAIQ_DIRECT_EDISON_ENABLED=1"
set "FARADAIQ_LLM_TOOLS_ENABLED=1"
set "FARADAIQ_SCIENCE_MODE=scilink_edison_llm"
echo Launching FaradaIQ eStudio v337...
>> "%FARADAIQ_SESSION_DIR%\logs\launcher_events.log" echo [%DATE% %TIME%] Starting GUI with %VENV_PY%
"%VENV_PY%" "%SCRIPT%"
set "APP_EXIT=!ERRORLEVEL!"
>> "%FARADAIQ_SESSION_DIR%\logs\launcher_events.log" echo [%DATE% %TIME%] GUI exited with code !APP_EXIT!
>> "%FARADAIQ_SESSION_DIR%\session_info.txt" echo Exit code: !APP_EXIT!
>> "%FARADAIQ_SESSION_DIR%\session_info.txt" echo Exit time: %DATE% %TIME%
echo FaradaIQ eStudio exited with code !APP_EXIT!.
if not "!APP_EXIT!"=="0" pause
exit /b !APP_EXIT!
