@echo off
setlocal EnableExtensions
set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\.faradaiq_venv\Scripts\python.exe"
set "PROXY=%~dp0labric_mcp_proxy.py"
if "%LABRIC_MCP_URL%"=="" set "LABRIC_MCP_URL=https://mcp.labric.co/mcp"
if "%LABRIC_API_KEY%"=="" (
  echo [FaradaIQ LabRIC MCP] LABRIC_API_KEY is missing. 1>&2
  exit /b 2
)
if not exist "%PY%" (
  echo [FaradaIQ LabRIC MCP] FaradaIQ private Python not found: "%PY%" 1>&2
  exit /b 3
)
if not exist "%PROXY%" (
  echo [FaradaIQ LabRIC MCP] Proxy script not found: "%PROXY%" 1>&2
  exit /b 4
)
set "PYTHONNOUSERSITE=1"
"%PY%" "%PROXY%"
exit /b %ERRORLEVEL%
