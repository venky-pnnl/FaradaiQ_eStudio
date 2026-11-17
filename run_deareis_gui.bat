@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
if not defined FARADAIQ_THEME set "FARADAIQ_THEME=High Contrast"
set "DEAREIS_PY=.deareis_venv\Scripts\python.exe"
echo ============================================================
echo Launch DearEIS from FaradaIQ optional DearEIS environment
echo ============================================================
if not exist "%DEAREIS_PY%" (
  echo DearEIS optional environment not found.
  echo Running installer now.
  call install_deareis_optional.bat --auto
)
if not exist "%DEAREIS_PY%" (
  echo ERROR: DearEIS environment is still missing.
  pause
  exit /b 1
)
"%DEAREIS_PY%" -c "import sys, importlib; print('Python:', sys.executable); importlib.import_module('deareis'); print('DearEIS import OK')"
if errorlevel 1 (
  echo.
  echo DearEIS is not ready. Running optional installer now.
  call install_deareis_optional.bat --auto
)
echo.
echo Starting DearEIS...
"%DEAREIS_PY%" "%~dp0faradaiq_deareis_gui_loader.py"
if errorlevel 1 (
  echo.
  echo DearEIS did not start. Try install_deareis_optional.bat and check messages above.
  pause
  exit /b 1
)
exit /b 0
