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

echo [3/4] Playwright chromium ^(optional^)...
"%PY%" -m playwright install chromium >nul 2>&1

if not exist "%ROOT%\frontend\dist\index.html" (
  echo [WARN] frontend\dist missing - UI may show JSON only.
  echo        Run: cd frontend ^&^& npm install ^&^& npm run build
) else (
  echo [OK] frontend\dist found
)

REM free port 8000 if occupied by old process
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo [WARN] Port 8000 in use by PID %%P, trying to stop it...
  taskkill /F /PID %%P >nul 2>&1
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
