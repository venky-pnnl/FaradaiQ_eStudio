@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "AUTO_MODE=0"
if /I "%~1"=="--auto" set "AUTO_MODE=1"
set "VENV_PY=.faradaiq_venv\Scripts\python.exe"
set "REQ=requirements_official_pydrttools_optional.txt"

if defined FARADAIQ_SESSION_DIR (
  set "INSTALL_LOG=%FARADAIQ_SESSION_DIR%\logs\pydrttools_install.log"
) else (
  set "INSTALL_LOG=%TEMP%\faradaiq_pydrttools_install.log"
)

call :run_install >> "%INSTALL_LOG%" 2>&1
set "INSTALL_EXIT=%ERRORLEVEL%"
type "%INSTALL_LOG%"
if not "%INSTALL_EXIT%"=="0" goto install_failed

echo.
echo Official pyDRTtools is ready for FaradaIQ selected-EIS handoff.
call :finish_terminal
exit /b 0

:run_install
echo ============================================================
echo Official ciuccislab pyDRTtools installer for FaradaIQ v273
echo ============================================================
echo Started: %DATE% %TIME%
echo Installs only into the local FaradaIQ private environment.
echo The pyDRTtools package is installed with --no-deps so its older
echo upstream pins cannot downgrade NumPy, SciPy, pandas, or Matplotlib.
echo.
if not exist "%VENV_PY%" (
  echo ERROR: FaradaIQ private environment was not found.
  echo Run Start_FaradaIQ_Studio_v273.bat once first.
  exit /b 1
)
if not exist "%REQ%" (
  echo ERROR: %REQ% was not found.
  exit /b 1
)

echo Installing/repairing GUI/runtime prerequisites...
"%VENV_PY%" -m pip install --upgrade -r "%REQ%"
if errorlevel 1 exit /b 1

echo.
echo Trying the official Ciucci Lab GitHub package without dependency downgrades...
"%VENV_PY%" -m pip install --upgrade --no-deps "https://github.com/ciuccislab/pyDRTtools/archive/refs/heads/master.zip"
if errorlevel 1 (
  echo GitHub install was unavailable. Trying the PyPI package without dependency downgrades...
  "%VENV_PY%" -m pip install --upgrade --no-deps pyDRTtools
  if errorlevel 1 exit /b 1
)

echo.
echo Verifying official pyDRTtools GUI and selected-data loader...
"%VENV_PY%" "_check_pydrttools_optional.py"
if errorlevel 1 exit /b 1

echo Completed: %DATE% %TIME%
exit /b 0

:install_failed
echo.
echo ERROR: Official pyDRTtools installation or verification failed.
echo Install log:
echo   %INSTALL_LOG%
echo FaradaIQ itself is unchanged and can continue using bundled headless DRT.
call :finish_terminal
exit /b 1

:finish_terminal
exit /b 0
