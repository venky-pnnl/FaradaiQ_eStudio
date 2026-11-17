@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title FaradaIQ AWS Bedrock LiteLLM Proxy + AWS SSO Check

set "PROXY_VENV=.faradaiq_litellm_venv"
set "PROXY_PY=%PROXY_VENV%\Scripts\python.exe"
set "REQ=requirements_litellm_proxy.txt"
set "CONFIG=litellm_bedrock.yaml"
set "SERVER_BAT=run_litellm_server_only.bat"
set "LITELLM_PORT=4000"

if "%LITELLM_KEY%"=="" set "LITELLM_KEY=sk-local"
if "%LITELLM_MODEL%"=="" set "LITELLM_MODEL=faradaiq-bedrock"
if "%AWS_PROFILE%"=="" set "AWS_PROFILE=BirthrightUser-143924590130"
if "%AWS_REGION%"=="" set "AWS_REGION=us-west-2"
if "%AWS_DEFAULT_REGION%"=="" set "AWS_DEFAULT_REGION=%AWS_REGION%"
if "%AWS_BEDROCK_MODEL_ID%"=="" set "AWS_BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0"
set "LITELLM_BASE_URL=http://127.0.0.1:%LITELLM_PORT%/v1"
set "PYTHONNOUSERSITE=1"
set "SEED_PYTHON_CMD="
set "PYTEST_FILE=%TEMP%\faradaiq_litellm_seed_pytest.txt"

echo ============================================================
echo FaradaIQ AWS Bedrock LiteLLM Proxy + AWS SSO Check
echo ============================================================
echo This launcher creates/uses:
echo   %CD%\%PROXY_VENV%
echo.
echo It asks you to confirm the AWS profile before any AWS identity check.
echo It then checks AWS SSO and runs a real AWS Bedrock chat ping.
echo.
echo Current/default AWS profile:
echo   %AWS_PROFILE%
set /p "AWS_PROFILE_IN=Enter AWS SSO profile name to use [%AWS_PROFILE%]: "
if not "%AWS_PROFILE_IN%"=="" set "AWS_PROFILE=%AWS_PROFILE_IN%"
echo Using AWS profile:
echo   %AWS_PROFILE%
echo.
echo Current/default AWS region:
echo   %AWS_REGION%
set /p "AWS_REGION_IN=Enter AWS region [%AWS_REGION%]: "
if not "%AWS_REGION_IN%"=="" set "AWS_REGION=%AWS_REGION_IN%"
set "AWS_DEFAULT_REGION=%AWS_REGION%"
echo Using AWS region:
echo   %AWS_REGION%
echo.

echo URL:                   %LITELLM_BASE_URL%
echo LiteLLM model alias:    %LITELLM_MODEL%
echo LiteLLM key:            %LITELLM_KEY%
echo AWS profile:            %AWS_PROFILE%
echo AWS region:             %AWS_REGION%
echo Bedrock model/profile:  %AWS_BEDROCK_MODEL_ID%
echo.

if not exist "%REQ%" (
  > "%REQ%" echo litellm[proxy]
  >> "%REQ%" echo boto3
)

call :write_config
call :ensure_proxy_env
if errorlevel 1 goto end
call :check_aws_sso
if errorlevel 1 goto end
call :write_server_bat
call :restart_proxy
call :wait_models
if errorlevel 1 goto end
call :test_chat
if errorlevel 1 (
  echo.
  echo [WARN] Real AWS Bedrock chat ping failed.
  echo Running AWS SSO login, restarting LiteLLM, and retrying once...
  aws sso login --profile "%AWS_PROFILE%"
  if errorlevel 1 (
    echo [ERROR] AWS SSO login failed.
    goto end
  )
  call :restart_proxy
  call :wait_models
  if errorlevel 1 goto end
  call :test_chat
  if errorlevel 1 (
    echo.
    echo [ERROR] Real AWS Bedrock invocation still failed.
    echo Check Bedrock model access, inference profile ID, SCP restrictions, and region.
    goto end
  )
)

echo.
echo [OK] FaradaIQ AWS/LiteLLM check succeeded.
echo Keep the separate LiteLLM proxy window open while using FaradaIQ.
echo.

:end
pause
exit /b 0

:write_config
(
echo model_list:
echo   - model_name: %LITELLM_MODEL%
echo     litellm_params:
echo       model: bedrock/%AWS_BEDROCK_MODEL_ID%
echo       aws_region_name: "%AWS_REGION%"
echo       aws_profile_name: "%AWS_PROFILE%"
echo litellm_settings:
echo   drop_params: true
echo general_settings:
echo   master_key: %LITELLM_KEY%
) > "%CONFIG%"
(
echo {
echo   "model": "%LITELLM_MODEL%",
echo   "messages": [
echo     {"role": "user", "content": "Say exactly: FaradaIQ LiteLLM proxy is working."}
echo   ],
echo   "max_tokens": 60
echo }
) > "proxy_test.json"
echo [OK] LiteLLM config written:
echo   %CD%\%CONFIG%
exit /b 0

:ensure_proxy_env
if not exist "%PROXY_PY%" (
  echo Creating isolated LiteLLM proxy environment...
  if exist "..\.faradaiq_venv\Scripts\python.exe" (
    echo Using main FaradaIQ Python:
    echo   ..\.faradaiq_venv\Scripts\python.exe
    "..\.faradaiq_venv\Scripts\python.exe" -m venv "%PROXY_VENV%"
  ) else (
    call :find_seed_python
    if not defined SEED_PYTHON_CMD (
      echo [ERROR] No Python was found to create the LiteLLM proxy environment.
      echo Open FaradaIQ with Start_run_faradaiq_studio_v216.bat first, or install Python 3.11/3.12.
      exit /b 1
    )
    echo Using seed Python command:
    echo   !SEED_PYTHON_CMD!
    !SEED_PYTHON_CMD! -m venv "%PROXY_VENV%"
  )
  if errorlevel 1 (
    echo [ERROR] Failed to create proxy environment.
    exit /b 1
  )
)

if not exist "%PROXY_PY%" (
  echo [ERROR] Proxy Python was not created:
  echo   %CD%\%PROXY_PY%
  exit /b 1
)

echo Installing/checking LiteLLM proxy dependencies...
"%PROXY_PY%" -m ensurepip --upgrade >nul 2>nul
"%PROXY_PY%" -m pip install --upgrade "pip<27" setuptools wheel
if errorlevel 1 exit /b 1
"%PROXY_PY%" -m pip install -r "%REQ%"
if errorlevel 1 exit /b 1
"%PROXY_PY%" -m pip freeze > "requirements_litellm_freeze.txt" 2>nul
echo [OK] LiteLLM proxy environment ready.
exit /b 0

:check_aws_sso
echo.
echo Checking AWS CLI and SSO identity...
where aws >nul 2>nul
if errorlevel 1 (
  echo [ERROR] AWS CLI not found.
  echo Install AWS CLI, then rerun this BAT.
  exit /b 1
)

aws configure list-profiles | findstr /R /C:"^%AWS_PROFILE%$" >nul 2>nul
if errorlevel 1 (
  echo [WARN] AWS profile not listed by aws configure list-profiles:
  echo   %AWS_PROFILE%
)

aws sts get-caller-identity --profile "%AWS_PROFILE%"
if errorlevel 1 (
  echo.
  echo [WARN] AWS identity check failed. Running AWS SSO login now...
  aws sso login --profile "%AWS_PROFILE%"
  if errorlevel 1 (
    echo [ERROR] AWS SSO login failed.
    exit /b 1
  )
  echo Rechecking AWS identity after SSO login...
  aws sts get-caller-identity --profile "%AWS_PROFILE%"
  if errorlevel 1 (
    echo [ERROR] AWS identity still fails after SSO login.
    exit /b 1
  )
)
echo [OK] AWS identity / SSO check succeeded.
exit /b 0

:write_server_bat
(
echo @echo off
echo setlocal EnableExtensions
echo cd /d "%%~dp0"
echo set "AWS_PROFILE=%AWS_PROFILE%"
echo set "AWS_REGION=%AWS_REGION%"
echo set "AWS_DEFAULT_REGION=%AWS_REGION%"
echo set "AWS_BEDROCK_MODEL_ID=%AWS_BEDROCK_MODEL_ID%"
echo set "LITELLM_BASE_URL=%LITELLM_BASE_URL%"
echo set "LITELLM_KEY=%LITELLM_KEY%"
echo set "LITELLM_MODEL=%LITELLM_MODEL%"
echo set "PYTHONNOUSERSITE=1"
echo title FaradaIQ LiteLLM Server Only
echo echo ============================================================
echo echo FaradaIQ LiteLLM Server Only
echo echo ============================================================
echo echo URL: %%LITELLM_BASE_URL%%
echo echo Model alias: %%LITELLM_MODEL%%
echo echo AWS profile: %%AWS_PROFILE%%
echo echo AWS region: %%AWS_REGION%%
echo echo Bedrock model: %%AWS_BEDROCK_MODEL_ID%%
echo echo.
echo if exist ".faradaiq_litellm_venv\Scripts\litellm.exe" ^(
echo   ".faradaiq_litellm_venv\Scripts\litellm.exe" --config "litellm_bedrock.yaml" --host 127.0.0.1 --port %LITELLM_PORT%
echo ^) else ^(
echo   ".faradaiq_litellm_venv\Scripts\python.exe" -m litellm --config "litellm_bedrock.yaml" --host 127.0.0.1 --port %LITELLM_PORT%
echo ^)
echo pause
) > "%SERVER_BAT%"
echo [OK] Server-only BAT written:
echo   %CD%\%SERVER_BAT%
exit /b 0

:restart_proxy
echo.
echo Stopping any existing process listening on port %LITELLM_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$pids=Get-NetTCPConnection -LocalPort %LITELLM_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach($pid in $pids){try{Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Write-Host ('Stopped PID ' + $pid)}catch{}}"
timeout /t 2 /nobreak >nul
echo Starting LiteLLM proxy in a separate window...
start "FaradaIQ LiteLLM Proxy" "%CD%\%SERVER_BAT%"
exit /b 0

:wait_models
echo.
echo Waiting for LiteLLM /models...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u=$env:LITELLM_BASE_URL.TrimEnd('/') + '/models'; $h=@{Authorization='Bearer '+$env:LITELLM_KEY}; for($i=0;$i -lt 75;$i++){try{$r=Invoke-RestMethod -Uri $u -Headers $h -TimeoutSec 2; Write-Host '[OK] /models responded'; if($r.data){$r.data | ForEach-Object {Write-Host ('  - ' + $_.id)}}; exit 0}catch{Start-Sleep -Seconds 1}}; Write-Host '[ERROR] timed out waiting for LiteLLM /models'; exit 1"
exit /b %ERRORLEVEL%

:test_chat
echo.
echo Running real AWS Bedrock chat ping through LiteLLM...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$body=@{model=$env:LITELLM_MODEL; messages=@(@{role='user'; content='Say exactly: FaradaIQ AWS Bedrock ping OK.'}); max_tokens=80} | ConvertTo-Json -Depth 8; try{$r=Invoke-RestMethod -Uri ($env:LITELLM_BASE_URL.TrimEnd('/') + '/chat/completions') -Method Post -Headers @{Authorization=('Bearer '+$env:LITELLM_KEY)} -ContentType 'application/json' -Body $body -TimeoutSec 120; Write-Host '[OK] Chat completion succeeded:'; Write-Host $r.choices[0].message.content; exit 0}catch{Write-Host '[ERROR] Chat completion failed.'; Write-Host $_.Exception.Message; if($_.ErrorDetails.Message){Write-Host $_.ErrorDetails.Message}; exit 1}"
exit /b %ERRORLEVEL%

:find_seed_python
set "SEED_PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
  findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
  if not errorlevel 1 set "SEED_PYTHON_CMD=py -3.11"
  if defined SEED_PYTHON_CMD exit /b 0

  py -3.12 -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
  findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
  if not errorlevel 1 set "SEED_PYTHON_CMD=py -3.12"
  if defined SEED_PYTHON_CMD exit /b 0

  py -3 -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
  findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
  if not errorlevel 1 set "SEED_PYTHON_CMD=py -3"
  if defined SEED_PYTHON_CMD exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
  findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
  if not errorlevel 1 set "SEED_PYTHON_CMD=python"
  if defined SEED_PYTHON_CMD exit /b 0
)

where python3 >nul 2>nul
if not errorlevel 1 (
  python3 -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
  findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
  if not errorlevel 1 set "SEED_PYTHON_CMD=python3"
)
exit /b 0
