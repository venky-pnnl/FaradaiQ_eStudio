@echo off
setlocal
set "APPDIR=%~dp0"
set "PSE_DIR=%APPDIR%integrated_tools\PySimpleEChem"
if not exist "%PSE_DIR%\PySimpleEChem.py" (
  echo PySimpleEChem.py was not found at: %PSE_DIR%
  pause
  exit /b 1
)
cd /d "%PSE_DIR%"
echo Starting native PySimpleEChem CV workflow...
echo If this fails, install optional dependencies: PyQt5 pyqtgraph superqt statsmodels odfpy xlrd galvani
python PySimpleEChem.py
if errorlevel 1 (
  echo.
  echo PySimpleEChem exited with an error. Check optional dependencies above.
  pause
)
endlocal
