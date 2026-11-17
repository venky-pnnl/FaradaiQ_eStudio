@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_VERSION=v292"
set "AUTO_MODE=0"
set "HELPER=_install_deareis_optional.py"
set "INSTALL_LOG=%TEMP%\FaradaIQ_Studio_v292_DearEIS_install.log"
for %%A in (%*) do (
  if /I "%%~A"=="--auto" set "AUTO_MODE=1"
  if /I "%%~A"=="/auto" set "AUTO_MODE=1"
)

title FaradaIQ DearEIS optional installer %APP_VERSION%
echo ============================================================
echo FaradaIQ optional DearEIS installer/checker %APP_VERSION%
echo ============================================================
echo.
echo DearEIS is a third-party open-source GUI/API for impedance-spectrum
echo processing, validation, DRT analysis, equivalent-circuit fitting, and plotting.
echo Credit: DearEIS developers / Ville Yrjana. See:
echo   https://vyrjana.github.io/DearEIS/
echo.
echo v292 uses a transactional clean rebuild of .deareis_venv.
echo It does not upgrade pip in place. If repair fails, the previous
echo environment is restored automatically.
echo Detailed log:
echo   %INSTALL_LOG%
echo.

if not exist "%HELPER%" (
  echo ERROR: %HELPER% is missing.
  set "INSTALL_EXIT=1"
  goto finish
)

set "INSTALL_EXIT=9009"
if exist ".faradaiq_venv\Scripts\python.exe" (
  ".faradaiq_venv\Scripts\python.exe" "%HELPER%" --root "%CD%" --log "%INSTALL_LOG%"
  set "INSTALL_EXIT=%ERRORLEVEL%"
  goto finish
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%HELPER%" --root "%CD%" --log "%INSTALL_LOG%"
  set "INSTALL_EXIT=%ERRORLEVEL%"
  goto finish
)

where python >nul 2>nul
if not errorlevel 1 (
  python "%HELPER%" --root "%CD%" --log "%INSTALL_LOG%"
  set "INSTALL_EXIT=%ERRORLEVEL%"
  goto finish
)

echo No Python controller was found. Trying automatic user-scope Python 3.12 installation...
where winget >nul 2>nul
if errorlevel 1 (
  echo ERROR: winget is unavailable, so Python 3.12 could not be installed automatically.
  set "INSTALL_EXIT=1"
  goto finish
)
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo ERROR: Automatic Python 3.12 installation failed.
  set "INSTALL_EXIT=1"
  goto finish
)
py -3.12 "%HELPER%" --root "%CD%" --log "%INSTALL_LOG%"
set "INSTALL_EXIT=%ERRORLEVEL%"

:finish
if "%INSTALL_EXIT%"=="0" (
  echo.
  echo DearEIS is ready.
  echo To launch it manually later:
  echo   run_deareis_gui.bat
) else (
  echo.
  echo ERROR: DearEIS setup failed with code %INSTALL_EXIT%.
  echo Review the detailed log:
  echo   %INSTALL_LOG%
)

if "%AUTO_MODE%"=="1" exit /b %INSTALL_EXIT%
exit /b %INSTALL_EXIT%
