@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
title FaradaIQ eStudio Browser v338

set "BROWSER_PYTHON=.faradaiq_venv\Scripts\python.exe"
if not exist "%BROWSER_PYTHON%" (
    echo Install FaradaIQ eStudio v338 before opening Browser mode.
    echo Run Install_FaradaIQ_eStudio_v338.bat from the extracted package.
    pause
    exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONFAULTHANDLER=1"
set "FARADAIQ_APP_VERSION=v338"
set "LOG_DIR=%LOCALAPPDATA%\FaradaIQ\eStudio\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" goto :without_log
set "LOG_FILE=%LOG_DIR%\browser_v338.log"
echo FaradaIQ eStudio Browser v338 is starting.
echo Keep this window open while using Browser mode.
echo Server log: "%LOG_FILE%"
>> "%LOG_FILE%" echo [%DATE% %TIME%] Starting Browser mode.
"%BROWSER_PYTHON%" -u "%CD%\faradaiq_browser_v338.py" %* >> "%LOG_FILE%" 2>&1
set "BROWSER_EXIT=%ERRORLEVEL%"
>> "%LOG_FILE%" echo [%DATE% %TIME%] Browser process exited with code %BROWSER_EXIT%.
if "%BROWSER_EXIT%"=="0" exit /b 0
echo Browser mode exited with code %BROWSER_EXIT%. See "%LOG_FILE%".
pause
exit /b %BROWSER_EXIT%

:without_log
echo Could not create the Browser startup log folder. Output will appear in this window.
"%BROWSER_PYTHON%" -u "%CD%\faradaiq_browser_v338.py" %*
set "BROWSER_EXIT=%ERRORLEVEL%"
if not "%BROWSER_EXIT%"=="0" pause
exit /b %BROWSER_EXIT%
