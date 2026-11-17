@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -Root "%~dp0." %*
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
 echo.
 echo Installation failed. The previous shortcut has not been replaced unless bootstrap succeeded.
 pause
)
exit /b %APP_EXIT%
