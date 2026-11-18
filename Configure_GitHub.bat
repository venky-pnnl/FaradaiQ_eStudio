@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\distribute.ps1" -Action Configure %*
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
 echo.
 echo Operation failed. Read the message above.
 pause
)
exit /b %APP_EXIT%
