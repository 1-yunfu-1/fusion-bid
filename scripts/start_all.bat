@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title FusionBid

cd /d "%~dp0.."
set "ROOT=%CD%"

echo ========================================
echo   FusionBid - One Click Start
echo ========================================
echo   Root: %ROOT%
echo.

REM ---- Find system Python (prefer py launcher) ----
set "SYS_PY="
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%I"
)
if not defined SYS_PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%I"
  )
)
if not defined SYS_PY (
  echo [ERROR] Python 3 not found.
  echo Install Python 3.12+ and check "Add python.exe to PATH"
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [OK] System Python: %SYS_PY%
"%SYS_PY%" -c "import sys; v=sys.version_info; raise SystemExit(0 if v.major==3 and v.minor>=12 else 1)" 2>nul
if errorlevel 1 (
  echo [ERROR] Need Python ^>= 3.12
  "%SYS_PY%" -c "import sys; print(sys.version)"
  pause
  exit /b 1
)

REM Check port before creating environments or installing dependencies.
REM Never terminate an arbitrary process on port 8000.
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  if not defined PORT_PID set "PORT_PID=%%P"
)
if defined PORT_PID (
  "%SYS_PY%" -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2)); current='managed-public-browser-v1' in d.get('capabilities',[]); raise SystemExit(0 if 'FusionBid' in str(d.get('app','')) and d.get('extraction_version')=='v2' and d.get('database_revision')=='20260718_0006' and current else 2 if 'FusionBid' in str(d.get('app','')) else 1)" >nul 2>&1
  set "HEALTH_RC=!ERRORLEVEL!"
  if "!HEALTH_RC!"=="0" (
      echo [OK] Current FusionBid is already running on port 8000 ^(PID !PORT_PID!^).
      start "" http://127.0.0.1:8000/
      exit /b 0
  )
  if "!HEALTH_RC!"=="2" (
    powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=!PORT_PID!'; $root=[IO.Path]::GetFullPath('%ROOT%'); if(([string]$p.ExecutablePath).StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or ([string]$p.CommandLine).Contains($root)){exit 0}else{exit 1}" >nul 2>&1
    if errorlevel 1 (
      echo [ERROR] An older FusionBid is running, but it cannot be verified as this workspace ^(PID !PORT_PID!^).
      echo         It will not be terminated. Close it manually, then retry.
      pause
      exit /b 1
    )
    echo [WARN] Older FusionBid from this workspace detected ^(PID !PORT_PID!^).
    choice /C YN /N /M "Safely stop it and start the upgraded version? [Y/N] "
    if errorlevel 2 exit /b 0
    taskkill /PID !PORT_PID! >nul 2>&1
    timeout /t 2 /nobreak >nul
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
      echo [ERROR] The old FusionBid did not stop safely. Close its window manually.
      pause
      exit /b 1
    )
    echo [OK] Old FusionBid stopped. Database migration will run before startup.
    set "PORT_PID="
  ) else (
  echo [ERROR] Port 8000 is occupied by another process ^(PID !PORT_PID!^).
  echo         Stop that process yourself or configure a different port, then retry.
  pause
  exit /b 1
  )
)

if not exist "%ROOT%\.env" (
  if exist "%ROOT%\.env.example" (
    copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [OK] Created .env from .env.example ^(no API key^)
  )
)

set "VENV_DIR=%ROOT%\backend\.venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "NEED_VENV=0"

if not exist "%PY%" (
  set "NEED_VENV=1"
) else (
  REM Only rebuild when venv python cannot run
  "%PY%" -c "import sys; print(sys.version)" >nul 2>&1
  if errorlevel 1 (
    echo [WARN] Broken venv detected, will recreate...
    set "NEED_VENV=1"
  )
)

if "!NEED_VENV!"=="1" (
  if exist "%VENV_DIR%" (
    echo [1/4] Removing broken venv...
    rmdir /s /q "%VENV_DIR%" 2>nul
  )
  echo [1/4] Creating venv...
  pushd "%ROOT%\backend"
  "%SYS_PY%" -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv
    popd
    pause
    exit /b 1
  )
  popd
  if not exist "%PY%" (
    echo [ERROR] venv python.exe missing after create
    pause
    exit /b 1
  )
) else (
  echo [1/4] venv OK
)

echo [2/4] Installing backend deps ^(may take a few minutes^)...
"%PY%" -m pip install -q -U pip
if errorlevel 1 (
  echo [WARN] pip upgrade failed, recreating venv...
  rmdir /s /q "%VENV_DIR%" 2>nul
  pushd "%ROOT%\backend"
  "%SYS_PY%" -m venv .venv
  popd
  "%PY%" -m pip install -q -U pip
  if errorlevel 1 (
    echo [ERROR] pip failed
    pause
    exit /b 1
  )
)

"%PY%" -m pip install -q -e "%ROOT%\backend[full]"
if errorlevel 1 (
  echo [ERROR] Dependency install failed. Check network and retry.
  pause
  exit /b 1
)

pushd "%ROOT%\backend"
"%PY%" -m alembic upgrade head
if errorlevel 1 (
  echo [ERROR] Database migration failed. The server was not started.
  popd
  pause
  exit /b 1
)
popd

echo [3/4] Playwright chromium ^(optional^)...
"%PY%" -m playwright install chromium >nul 2>&1

if not exist "%ROOT%\frontend\dist\index.html" (
  echo [WARN] frontend\dist missing - UI may show JSON only.
  echo        Run: cd frontend ^&^& npm install ^&^& npm run build
) else (
  echo [OK] frontend\dist found
)

echo [4/4] Starting server...
echo.
echo   Open browser:  http://127.0.0.1:8000/
echo   API docs:      http://127.0.0.1:8000/docs
echo   API Key: configure in Settings page
echo.
echo   Keep this window open. Close it to stop.
echo ========================================
echo.

cd /d "%ROOT%\backend"

REM open browser after short delay in background
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000/"

"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERROR] Server exited with code %RC%
  echo If port busy: close other FusionBid/uvicorn windows.
)
echo Stopped.
pause
endlocal
