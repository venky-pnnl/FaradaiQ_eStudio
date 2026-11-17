@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_VERSION=v210"
set "MAIN_VENV_DIR=.faradaiq_venv"
set "MAIN_VENV_PY=%MAIN_VENV_DIR%\Scripts\python.exe"
set "PROXY_ROOT=%CD%\litellm_proxy"
set "PROXY_VENV=%PROXY_ROOT%\.faradaiq_litellm_venv"
set "PROXY_PY=%PROXY_VENV%\Scripts\python.exe"
set "PROXY_CONFIG=%PROXY_ROOT%\litellm_bedrock.yaml"
set "PROXY_REQ=%PROXY_ROOT%\requirements_litellm_proxy.txt"
set "PROXY_START=%PROXY_ROOT%\start_faradaiq_litellm_proxy.bat"
set "LITELLM_PORT=4000"
set "LITELLM_BASE_URL=http://127.0.0.1:%LITELLM_PORT%/v1"
set "LITELLM_KEY=sk-local"
set "LITELLM_MODEL=faradaiq-bedrock"
set "DEFAULT_AWS_PROFILE=BirthrightUser-143924590130"
set "SEED_PYTHON_CMD="
set "PYTEST_FILE=%TEMP%\faradaiq_llm_wizard_seed_pytest.txt"

title FaradaIQ LLM Backend Setup Wizard v210

call :section "FaradaIQ LLM Backend Setup Wizard"
echo This wizard is launched from FaradaIQ LLM settings.
echo It does NOT start FaradaIQ and does NOT run at app startup.
echo.
echo It can configure/check:
echo   1. AWS Bedrock cloud through LiteLLM
echo   2. Local Ollama
echo   3. PNNL AI Incubator / API Key Depot
echo   4. Custom OpenAI-compatible endpoint
echo.
echo Launch folder:       %CD%
echo LiteLLM proxy env:   %PROXY_VENV%
echo Default AWS profile: %DEFAULT_AWS_PROFILE%
echo.

call :initial_env_check
call :choose_mode
call :choose_backend
call :route_backend
echo.
echo Wizard finished. Return to FaradaIQ LLM settings and click:
echo   Load/fill from Windows env
echo   Ping selected
echo.
pause
exit /b 0

:section
echo.
echo ============================================================
echo %~1
echo ============================================================
exit /b 0

:initial_env_check
call :section "Initial Environment Key Check"
echo Secret values are NOT printed. The wizard only checks whether variables exist.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$keys=@('AI_INCUBATOR_BASE_URL','AI_INCUBATOR_MODEL','AI_INCUBATOR_API_KEY','GOOGLE_API_KEY','LLM_API_KEY','OPENAI_API_KEY','CUSTOM_LLM_BASE_URL','CUSTOM_LLM_MODEL','CUSTOM_LLM_API_KEY','OLLAMA_HOST','OLLAMA_MODEL','AWS_PROFILE','AWS_REGION','AWS_DEFAULT_REGION','AWS_BEDROCK_MODEL_ID','BEDROCK_MODEL_ID','LITELLM_BASE_URL','LITELLM_KEY','LITELLM_MODEL','EDISON_API_KEY','EDISON_UPLOAD_URL'); $missing=@(); foreach($k in $keys){$v=[Environment]::GetEnvironmentVariable($k,'Process'); $scope='Process'; if(-not $v){$v=[Environment]::GetEnvironmentVariable($k,'User'); $scope='User'}; if(-not $v){$v=[Environment]::GetEnvironmentVariable($k,'Machine'); $scope='Machine'}; if($v){Write-Host ('[OK]      {0,-26} found in {1} environment' -f $k,$scope) -ForegroundColor Green}else{Write-Host ('[MISSING] {0,-26} not found' -f $k) -ForegroundColor Yellow; $missing+=$k}}; if($missing.Count -gt 0){Write-Host ''; Write-Host 'Missing keys/variables:'; $missing | ForEach-Object {Write-Host ('  ' + $_)}}"
echo.
echo This is only an initial check. Missing values are requested only if the selected backend needs them.
exit /b 0

:choose_mode
call :section "Startup Mode"
echo   1. Fast setup/check
echo      - Minimal checks
echo      - Skip optional model-list/identity checks where possible
echo.
echo   2. Custom / setup check
echo      - Best for first-time setup
echo      - Can run richer checks/tests and install missing command-line tools
echo.
set "SETUP_MODE=1"
set /p "SETUP_MODE=Enter choice [1]: "
if "%SETUP_MODE%"=="" set "SETUP_MODE=1"
if "%SETUP_MODE%"=="2" (
    echo Selected mode: Custom / setup
) else (
    set "SETUP_MODE=1"
    echo Selected mode: Fast
)
exit /b 0

:choose_backend
call :section "LLM Backend"
echo   1. AWS Bedrock cloud through LiteLLM
echo   2. Local Ollama
echo   3. PNNL AI Incubator / API Key Depot
echo   4. Custom OpenAI-compatible endpoint
echo.
set "BACKEND_CHOICE=1"
set /p "BACKEND_CHOICE=Enter choice [1]: "
if "%BACKEND_CHOICE%"=="" set "BACKEND_CHOICE=1"
if "%BACKEND_CHOICE%"=="1" set "BACKEND_NAME=AWS Bedrock via LiteLLM"
if "%BACKEND_CHOICE%"=="2" set "BACKEND_NAME=Local Ollama"
if "%BACKEND_CHOICE%"=="3" set "BACKEND_NAME=PNNL AI Incubator / API Key Depot"
if "%BACKEND_CHOICE%"=="4" set "BACKEND_NAME=Custom OpenAI-compatible"
echo Selected backend: %BACKEND_NAME%
exit /b 0

:route_backend
call :section "Routing to Selected LLM Backend"
echo Selected backend: %BACKEND_NAME%
if "%BACKEND_CHOICE%"=="1" call :setup_aws_litellm
if "%BACKEND_CHOICE%"=="2" call :setup_ollama
if "%BACKEND_CHOICE%"=="3" call :setup_ai_incubator
if "%BACKEND_CHOICE%"=="4" call :setup_custom
exit /b 0

:setup_aws_litellm
call :section "AWS / LiteLLM Setup"
call :ensure_aws_cli
where aws >nul 2>nul
if errorlevel 1 (
    echo [ERROR] AWS CLI is required for AWS Bedrock via LiteLLM.
    exit /b 1
)

for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$p=[Environment]::GetEnvironmentVariable('AWS_PROFILE','Process'); if(-not $p){$p=[Environment]::GetEnvironmentVariable('AWS_PROFILE','User')}; if(-not $p){$p=[Environment]::GetEnvironmentVariable('AWS_PROFILE','Machine')}; if($p){$p}else{'%DEFAULT_AWS_PROFILE%'}"`) do set "AWS_PROFILE_USE=%%A"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$r=[Environment]::GetEnvironmentVariable('AWS_REGION','Process'); if(-not $r){$r=[Environment]::GetEnvironmentVariable('AWS_REGION','User')}; if(-not $r){$r=[Environment]::GetEnvironmentVariable('AWS_DEFAULT_REGION','Process')}; if(-not $r){$r=[Environment]::GetEnvironmentVariable('AWS_DEFAULT_REGION','User')}; if($r){$r}else{'us-west-2'}"`) do set "AWS_REGION_USE=%%A"

echo Current/default AWS profile:
echo   %AWS_PROFILE_USE%
set /p "AWS_PROFILE_IN=Enter AWS SSO profile name to use [%AWS_PROFILE_USE%]: "
if not "%AWS_PROFILE_IN%"=="" set "AWS_PROFILE_USE=%AWS_PROFILE_IN%"
set "AWS_PROFILE=%AWS_PROFILE_USE%"

set /p "AWS_REGION_IN=Enter AWS region [%AWS_REGION_USE%]: "
if not "%AWS_REGION_IN%"=="" set "AWS_REGION_USE=%AWS_REGION_IN%"
set "AWS_REGION=%AWS_REGION_USE%"
set "AWS_DEFAULT_REGION=%AWS_REGION_USE%"

aws configure list-profiles | findstr /R /C:"^%AWS_PROFILE_USE%$" >nul 2>nul
if errorlevel 1 (
    echo [WARN] AWS profile not found in aws configure list-profiles.
    choice /C YN /M "Run aws sso login --profile %AWS_PROFILE_USE% now"
    if not errorlevel 2 aws sso login --profile "%AWS_PROFILE_USE%"
) else (
    echo [OK] AWS profile exists.
)

if "%SETUP_MODE%"=="2" (
    echo Running AWS identity check...
    aws sts get-caller-identity --profile "%AWS_PROFILE_USE%"
    if errorlevel 1 (
        echo [WARN] AWS identity check failed.
        choice /C YN /M "Run aws sso login --profile %AWS_PROFILE_USE% now"
        if not errorlevel 2 aws sso login --profile "%AWS_PROFILE_USE%"
    )
) else (
    echo [FAST] Skipping AWS identity check/model list.
)

call :choose_bedrock_model
call :write_litellm_config
call :find_python_for_proxy
if not defined SEED_PYTHON_CMD (
    echo [ERROR] No Python found to create LiteLLM proxy environment.
    exit /b 1
)
call :ensure_litellm_proxy_env
call :start_litellm_proxy
call :wait_litellm
if errorlevel 1 (
    echo [WARN] LiteLLM /models did not respond.
) else (
    echo [OK] LiteLLM API responding.
)
call :test_litellm_chat
if errorlevel 1 (
    echo [WARN] LiteLLM selected-model chat test failed.
    echo Common causes: expired AWS SSO token, no Bedrock model access, wrong model ID, SCP deny, or region mismatch.
    echo Current AWS profile:
    echo   %AWS_PROFILE_USE%
    choice /C YN /M "Run aws sso login, restart LiteLLM, and retry"
    if not errorlevel 2 (
        aws sso login --profile "%AWS_PROFILE_USE%"
        call :stop_litellm_port
        call :start_litellm_proxy
        call :wait_litellm
        call :test_litellm_chat
        if errorlevel 1 (
            echo [WARN] Retry still failed. Use FaradaIQ LLM settings to inspect the exact error.
        ) else (
            echo [OK] Chat completion test succeeded after AWS SSO refresh.
        )
    )
) else (
    echo [OK] Chat completion test succeeded.
)

choice /C YN /M "Persist selected AWS/LiteLLM variables to Windows User env with setx"
if not errorlevel 2 (
    setx AWS_PROFILE "%AWS_PROFILE_USE%"
    setx AWS_REGION "%AWS_REGION_USE%"
    setx AWS_DEFAULT_REGION "%AWS_REGION_USE%"
    setx AWS_BEDROCK_MODEL_ID "%AWS_BEDROCK_MODEL_ID%"
    setx LITELLM_BASE_URL "%LITELLM_BASE_URL%"
    setx LITELLM_KEY "%LITELLM_KEY%"
    setx LITELLM_MODEL "%LITELLM_MODEL%"
)
exit /b 0

:choose_bedrock_model
call :section "Choose AWS Bedrock Model"
echo   1. Claude 3 Haiku        stable/default; confirmed in prior flow
echo   2. Claude 3 Sonnet       legacy; may require access
echo   3. Claude 3.5 Sonnet v2  may require access
echo   4. Custom Bedrock model or inference-profile ID
echo.
set "MODEL_CHOICE=1"
set /p "MODEL_CHOICE=Enter choice [1]: "
if "%MODEL_CHOICE%"=="" set "MODEL_CHOICE=1"
if "%MODEL_CHOICE%"=="1" (
    set "AWS_BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0"
    echo Writing LiteLLM config for Claude 3 Haiku...
) else if "%MODEL_CHOICE%"=="2" (
    set "AWS_BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0"
    echo Writing LiteLLM config for Claude 3 Sonnet...
) else if "%MODEL_CHOICE%"=="3" (
    set "AWS_BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0"
    echo Writing LiteLLM config for Claude 3.5 Sonnet v2...
) else (
    set /p "AWS_BEDROCK_MODEL_ID=Enter custom Bedrock model/profile ID: "
    if "%AWS_BEDROCK_MODEL_ID%"=="" set "AWS_BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0"
)
set "BEDROCK_MODEL_ID=%AWS_BEDROCK_MODEL_ID%"
set "LITELLM_MODEL=faradaiq-bedrock"
set "LITELLM_KEY=sk-local"
set "LITELLM_BASE_URL=http://127.0.0.1:%LITELLM_PORT%/v1"
powershell -NoProfile -Command "if('%AWS_BEDROCK_MODEL_ID%' -match '^[0-9a-fA-F]{48,}$'){Write-Host '[WARN] AWS_BEDROCK_MODEL_ID looks like a long hash/token, not a normal Bedrock model ID.' -ForegroundColor Yellow}"
exit /b 0

:write_litellm_config
if not exist "%PROXY_ROOT%" mkdir "%PROXY_ROOT%"
> "%PROXY_REQ%" echo litellm[proxy]
>> "%PROXY_REQ%" echo boto3
(
echo model_list:
echo   - model_name: %LITELLM_MODEL%
echo     litellm_params:
echo       model: bedrock/%AWS_BEDROCK_MODEL_ID%
echo       aws_region_name: "%AWS_REGION_USE%"
echo       aws_profile_name: "%AWS_PROFILE_USE%"
echo litellm_settings:
echo   drop_params: true
echo general_settings:
echo   master_key: %LITELLM_KEY%
) > "%PROXY_CONFIG%"
(
echo {
echo   "model": "%LITELLM_MODEL%",
echo   "messages": [
echo     {"role": "user", "content": "Say exactly: FaradaIQ LiteLLM check OK."}
echo   ],
echo   "max_tokens": 60
echo }
) > "%PROXY_ROOT%\proxy_test.json"
echo [OK] LiteLLM config written:
echo   %PROXY_CONFIG%
exit /b 0

:ensure_litellm_proxy_env
call :section "Checking/Installing LiteLLM Proxy Environment"
if not exist "%PROXY_PY%" (
    echo Creating isolated LiteLLM proxy environment:
    echo   %PROXY_VENV%
    %SEED_PYTHON_CMD% -m venv "%PROXY_VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create LiteLLM proxy environment.
        exit /b 1
    )
)
echo Installing/checking LiteLLM and boto3 inside proxy env only...
"%PROXY_PY%" -m ensurepip --upgrade >nul 2>nul
"%PROXY_PY%" -m pip install --upgrade "pip<27" setuptools wheel
"%PROXY_PY%" -m pip install -r "%PROXY_REQ%"
if errorlevel 1 (
    echo [ERROR] LiteLLM proxy dependency installation failed.
    exit /b 1
)
"%PROXY_PY%" -m pip freeze > "%PROXY_ROOT%\requirements_litellm_freeze.txt" 2>nul
if exist "%PROXY_ROOT%\requirements_litellm_freeze.txt" echo [OK] LiteLLM frozen package list saved: %PROXY_ROOT%\requirements_litellm_freeze.txt
call :write_proxy_start_bat
exit /b 0

:write_proxy_start_bat
(
echo @echo off
echo setlocal EnableExtensions
echo cd /d "%%~dp0"
echo set "AWS_PROFILE=%AWS_PROFILE_USE%"
echo set "AWS_REGION=%AWS_REGION_USE%"
echo set "AWS_DEFAULT_REGION=%AWS_REGION_USE%"
echo set "LITELLM_BASE_URL=%LITELLM_BASE_URL%"
echo set "LITELLM_KEY=%LITELLM_KEY%"
echo set "LITELLM_MODEL=%LITELLM_MODEL%"
echo set "PYTHONNOUSERSITE=1"
echo title FaradaIQ AWS Bedrock LiteLLM Proxy
echo echo ============================================================
echo echo FaradaIQ AWS Bedrock LiteLLM Proxy
echo echo ============================================================
echo echo URL:   %LITELLM_BASE_URL%
echo echo Model: %LITELLM_MODEL%
echo echo AWS profile: %AWS_PROFILE_USE%
echo echo AWS region:  %AWS_REGION_USE%
echo echo Bedrock model/profile: %AWS_BEDROCK_MODEL_ID%
echo echo.
echo if exist ".faradaiq_litellm_venv\Scripts\litellm.exe" ^(
echo   ".faradaiq_litellm_venv\Scripts\litellm.exe" --config "litellm_bedrock.yaml" --host 127.0.0.1 --port %LITELLM_PORT%
echo ^) else ^(
echo   ".faradaiq_litellm_venv\Scripts\python.exe" -m litellm --config "litellm_bedrock.yaml" --host 127.0.0.1 --port %LITELLM_PORT%
echo ^)
echo pause
) > "%PROXY_START%"
echo [OK] Proxy start BAT written:
echo   %PROXY_START%
exit /b 0

:start_litellm_proxy
echo Starting LiteLLM proxy in a separate window...
start "FaradaIQ LiteLLM Proxy" "%PROXY_START%"
exit /b 0

:stop_litellm_port
echo Stopping any existing LiteLLM process on port %LITELLM_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$pids=Get-NetTCPConnection -LocalPort %LITELLM_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach($pid in $pids){try{Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Write-Host ('Stopped PID ' + $pid)}catch{}}"
exit /b 0

:wait_litellm
echo Waiting for LiteLLM proxy...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u=$env:LITELLM_BASE_URL + '/models'; $h=@{Authorization='Bearer ' + $env:LITELLM_KEY}; for($i=0;$i -lt 75;$i++){try{$r=Invoke-RestMethod -Uri $u -Headers $h -TimeoutSec 2; Write-Host '[OK] /models responded'; exit 0}catch{Start-Sleep -Seconds 1}}; Write-Host '[WARN] timed out waiting for LiteLLM /models'; exit 1"
exit /b %ERRORLEVEL%

:test_litellm_chat
echo Running LiteLLM selected-model chat test...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$body=@{model=$env:LITELLM_MODEL; messages=@(@{role='user'; content='Say exactly: FaradaIQ LiteLLM check OK.'}); max_tokens=60} | ConvertTo-Json -Depth 8; try{$r=Invoke-RestMethod -Uri ($env:LITELLM_BASE_URL + '/chat/completions') -Method Post -Headers @{Authorization=('Bearer '+$env:LITELLM_KEY)} -ContentType 'application/json' -Body $body -TimeoutSec 90; $txt=$r.choices[0].message.content; Write-Host $txt; exit 0}catch{Write-Host '[WARN] Chat completion test failed.'; Write-Host $_.Exception.Message; if($_.ErrorDetails.Message){Write-Host $_.ErrorDetails.Message}; exit 1}"
exit /b %ERRORLEVEL%

:setup_ollama
call :section "Ollama Setup"
call :ensure_ollama
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('OLLAMA_HOST','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('OLLAMA_HOST','User')}; if($v){$v}else{'http://127.0.0.1:11434'}"`) do set "OLLAMA_HOST=%%A"
set /p "OLLAMA_HOST_IN=Enter Ollama host [%OLLAMA_HOST%]: "
if not "%OLLAMA_HOST_IN%"=="" set "OLLAMA_HOST=%OLLAMA_HOST_IN%"
set "OLLAMA_BASE_URL=%OLLAMA_HOST%/v1"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('OLLAMA_MODEL','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('OLLAMA_MODEL','User')}; if($v){$v}else{'llama3.1'}"`) do set "OLLAMA_MODEL=%%A"
set /p "OLLAMA_MODEL_IN=Enter Ollama model [%OLLAMA_MODEL%]: "
if not "%OLLAMA_MODEL_IN%"=="" set "OLLAMA_MODEL=%OLLAMA_MODEL_IN%"
set "OLLAMA_API_KEY=ollama"
where ollama >nul 2>nul
if not errorlevel 1 ollama list
echo Checking Ollama HTTP API...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$host=$env:OLLAMA_HOST.TrimEnd('/'); try{$r=Invoke-RestMethod -Uri ($host + '/api/tags') -TimeoutSec 4; Write-Host '[OK] Ollama API responded.'; $r.models | ForEach-Object {Write-Host ('  - ' + $_.name)}; exit 0}catch{Write-Host '[WARN] Ollama API not responding.'; Write-Host $_.Exception.Message; exit 1}"
if errorlevel 1 (
    where ollama >nul 2>nul
    if not errorlevel 1 start "Ollama serve" cmd /k "ollama serve"
)
echo Testing Ollama selected model...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$host=$env:OLLAMA_HOST.TrimEnd('/'); $body=@{model=$env:OLLAMA_MODEL; messages=@(@{role='user'; content='Say exactly: FaradaIQ Ollama check OK.'}); stream=$false} | ConvertTo-Json -Depth 8; try{$r=Invoke-RestMethod -Uri ($host + '/api/chat') -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 90; Write-Host $r.message.content; exit 0}catch{Write-Host '[WARN] Ollama chat test failed.'; Write-Host $_.Exception.Message; exit 1}"
if errorlevel 1 (
    where ollama >nul 2>nul
    if not errorlevel 1 (
        choice /C YN /M "Pull model %OLLAMA_MODEL% with ollama pull now"
        if not errorlevel 2 ollama pull "%OLLAMA_MODEL%"
    )
)
choice /C YN /M "Persist selected Ollama variables to Windows User env with setx"
if not errorlevel 2 (
    setx OLLAMA_HOST "%OLLAMA_HOST%"
    setx OLLAMA_MODEL "%OLLAMA_MODEL%"
)
exit /b 0

:setup_ai_incubator
call :section "PNNL AI Incubator / API Key Depot Setup"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$names=@('AI_INCUBATOR_API_KEY','GOOGLE_API_KEY','LLM_API_KEY','OPENAI_API_KEY'); foreach($n in $names){$v=[Environment]::GetEnvironmentVariable($n,'Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable($n,'User')}; if(-not $v){$v=[Environment]::GetEnvironmentVariable($n,'Machine')}; if($v){Write-Output $v; exit}}; Write-Output ''"`) do set "AI_KEY_FOUND=%%A"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('AI_INCUBATOR_BASE_URL','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('AI_INCUBATOR_BASE_URL','User')}; if($v){$v}else{'https://ai-incubator-api.pnnl.gov/v1'}"`) do set "AI_INCUBATOR_BASE_URL=%%A"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('AI_INCUBATOR_MODEL','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('AI_INCUBATOR_MODEL','User')}; if($v){$v}else{'claude-sonnet-4-6-birthright'}"`) do set "AI_INCUBATOR_MODEL=%%A"
set /p "AI_BASE_IN=Enter AI Incubator/OpenAI-compatible base URL [%AI_INCUBATOR_BASE_URL%]: "
if not "%AI_BASE_IN%"=="" set "AI_INCUBATOR_BASE_URL=%AI_BASE_IN%"
set /p "AI_MODEL_IN=Enter AI Incubator model [%AI_INCUBATOR_MODEL%]: "
if not "%AI_MODEL_IN%"=="" set "AI_INCUBATOR_MODEL=%AI_MODEL_IN%"
if "%AI_KEY_FOUND%"=="" (
    set /p "AI_KEY_FOUND=Paste API key for this session only, or leave blank to skip test: "
) else (
    echo [OK] AI Incubator/API Key Depot key found in environment.
)
if "%AI_KEY_FOUND%"=="" (
    echo [WARN] No key provided. Use FaradaIQ LLM settings to fill/test later.
    exit /b 0
)
set "AI_INCUBATOR_API_KEY=%AI_KEY_FOUND%"
set "GOOGLE_API_KEY=%AI_KEY_FOUND%"
echo Listing models if supported...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base=$env:AI_INCUBATOR_BASE_URL.TrimEnd('/'); if(-not $base.EndsWith('/v1')){$base=$base+'/v1'}; try{$r=Invoke-RestMethod -Uri ($base + '/models') -Headers @{Authorization=('Bearer '+$env:AI_INCUBATOR_API_KEY)} -TimeoutSec 15; Write-Host '[OK] /models responded.'; $r.data | ForEach-Object {Write-Host ('  - ' + $_.id)}; exit 0}catch{Write-Host '[WARN] Model list not available or failed.'; Write-Host $_.Exception.Message; exit 0}"
echo Running AI Incubator chat test...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base=$env:AI_INCUBATOR_BASE_URL.TrimEnd('/'); if(-not $base.EndsWith('/v1')){$base=$base+'/v1'}; $body=@{model=$env:AI_INCUBATOR_MODEL; messages=@(@{role='user'; content='Say exactly: FaradaIQ AI Incubator check OK.'}); max_tokens=60} | ConvertTo-Json -Depth 8; try{$r=Invoke-RestMethod -Uri ($base + '/chat/completions') -Method Post -Headers @{Authorization=('Bearer '+$env:AI_INCUBATOR_API_KEY)} -ContentType 'application/json' -Body $body -TimeoutSec 90; Write-Host $r.choices[0].message.content; exit 0}catch{Write-Host '[WARN] AI Incubator chat test failed.'; Write-Host $_.Exception.Message; if($_.ErrorDetails.Message){Write-Host $_.ErrorDetails.Message}; exit 1}"
choice /C YN /M "Persist AI Incubator base/model/key to Windows User env with setx"
if not errorlevel 2 (
    setx AI_INCUBATOR_BASE_URL "%AI_INCUBATOR_BASE_URL%"
    setx AI_INCUBATOR_MODEL "%AI_INCUBATOR_MODEL%"
    setx AI_INCUBATOR_API_KEY "%AI_INCUBATOR_API_KEY%"
)
exit /b 0

:setup_custom
call :section "Custom OpenAI-compatible Setup"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_BASE_URL','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('OPENAI_BASE_URL','Process')}; if(-not $v){$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_BASE_URL','User')}; if($v){$v}else{'http://127.0.0.1:8000/v1'}"`) do set "CUSTOM_LLM_BASE_URL=%%A"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_MODEL','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('OPENAI_MODEL','Process')}; if(-not $v){$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_MODEL','User')}; if($v){$v}else{''}"`) do set "CUSTOM_LLM_MODEL=%%A"
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_API_KEY','Process'); if(-not $v){$v=[Environment]::GetEnvironmentVariable('OPENAI_API_KEY','Process')}; if(-not $v){$v=[Environment]::GetEnvironmentVariable('CUSTOM_LLM_API_KEY','User')}; if($v){$v}else{''}"`) do set "CUSTOM_LLM_API_KEY=%%A"
set /p "CUSTOM_BASE_IN=Enter custom base URL [%CUSTOM_LLM_BASE_URL%]: "
if not "%CUSTOM_BASE_IN%"=="" set "CUSTOM_LLM_BASE_URL=%CUSTOM_BASE_IN%"
set /p "CUSTOM_MODEL_IN=Enter custom model [%CUSTOM_LLM_MODEL%]: "
if not "%CUSTOM_MODEL_IN%"=="" set "CUSTOM_LLM_MODEL=%CUSTOM_MODEL_IN%"
if "%CUSTOM_LLM_API_KEY%"=="" set /p "CUSTOM_LLM_API_KEY=Paste custom API key for this session only, or leave blank: "
echo Listing custom models if supported...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base=$env:CUSTOM_LLM_BASE_URL.TrimEnd('/'); if(-not $base.EndsWith('/v1')){$base=$base+'/v1'}; $h=@{}; if($env:CUSTOM_LLM_API_KEY){$h.Authorization='Bearer '+$env:CUSTOM_LLM_API_KEY}; try{$r=Invoke-RestMethod -Uri ($base + '/models') -Headers $h -TimeoutSec 15; Write-Host '[OK] /models responded.'; $r.data | ForEach-Object {Write-Host ('  - ' + $_.id)}; exit 0}catch{Write-Host '[WARN] /models failed.'; Write-Host $_.Exception.Message; exit 0}"
exit /b 0

:ensure_aws_cli
where aws >nul 2>nul
if not errorlevel 1 (
    echo [OK] AWS CLI found.
    exit /b 0
)
echo [WARN] AWS CLI not found.
if not "%SETUP_MODE%"=="2" (
    echo [FAST] Not installing AWS CLI. Use Custom/setup mode if this is a new PC.
    exit /b 0
)
where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget not found; cannot auto-install AWS CLI.
    exit /b 1
)
choice /C YN /M "Install AWS CLI using winget"
if errorlevel 2 exit /b 0
winget install -e --id Amazon.AWSCLI --accept-package-agreements --accept-source-agreements
exit /b 0

:ensure_ollama
where ollama >nul 2>nul
if not errorlevel 1 (
    echo [OK] Ollama found.
    exit /b 0
)
echo [WARN] Ollama not found.
if not "%SETUP_MODE%"=="2" (
    echo [FAST] Not installing Ollama. Use Custom/setup mode if this is a new PC.
    exit /b 0
)
where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget not found; cannot auto-install Ollama.
    exit /b 1
)
choice /C YN /M "Install Ollama using winget"
if errorlevel 2 exit /b 0
winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
exit /b 0

:find_python_for_proxy
set "SEED_PYTHON_CMD="
if exist "%MAIN_VENV_PY%" (
    set "SEED_PYTHON_CMD="%MAIN_VENV_PY%""
    exit /b 0
)
for %%C in ("py -3.11" "py -3.12" "py -3.10" "py -3.9" "py -3" "python" "python3") do (
    if not defined SEED_PYTHON_CMD (
        call :try_seed_python %%~C
    )
)
exit /b 0

:try_seed_python
set "CAND=%*"
%CAND% -c "import sys, venv; print('PY_OK')" > "%PYTEST_FILE%" 2>&1
findstr /B /C:"PY_OK" "%PYTEST_FILE%" >nul 2>nul
if not errorlevel 1 set "SEED_PYTHON_CMD=%CAND%"
exit /b 0
