@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem The shared tool setup runs _install_deareis_optional.py transactionally.
if not exist ".faradaiq_venv\Scripts\python.exe" (
  echo Run Install_FaradaIQ_eStudio_v337.bat first to prepare the private runtime.
  if /I not "%~1"=="--auto" pause
  exit /b 1
)
".faradaiq_venv\Scripts\python.exe" "scripts\setup_analysis_tools.py" --tool deareis --auto
set "INSTALL_EXIT=%ERRORLEVEL%"
if not "%INSTALL_EXIT%"=="0" if /I not "%~1"=="--auto" pause
exit /b %INSTALL_EXIT%
