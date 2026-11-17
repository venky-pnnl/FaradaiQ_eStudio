@echo off
cd /d "%~dp0"
if not exist "litellm_proxy\start_faradaiq_litellm_proxy.bat" (
  echo [ERROR] Missing litellm_proxy\start_faradaiq_litellm_proxy.bat
  pause
  exit /b 1
)
call "litellm_proxy\start_faradaiq_litellm_proxy.bat"
