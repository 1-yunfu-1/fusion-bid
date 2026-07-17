@echo off
chcp 65001 >nul
setlocal EnableExtensions
title FusionBid

cd /d "%~dp0.."
set "ROOT=%CD%"

echo ========================================
echo   FusionBid - One Click Start
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.12+ and add to PATH.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist "%ROOT%\.env" (
  if exist "%ROOT%\.env.example" (
    copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [OK] Created .env from .env.example (no API key)
  )
)

if not exist "%ROOT%\backend\.venv\Scripts\python.exe" (
  echo [1/4] Creating venv...
  pushd "%ROOT%\backend"
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] venv failed
    popd
    pause
    exit /b 1
  )
  popd
) else (
  echo [1/4] venv exists
)

set "PY=%ROOT%\backend\.venv\Scripts\python.exe"

echo [2/4] Installing backend deps (first run may take several minutes)...
"%PY%" -m pip install -q -U pip
"%PY%" -m pip install -q -e "%ROOT%\backend[full]"
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

echo [3/4] Playwright chromium (optional for login crawl)...
"%PY%" -m playwright install chromium >nul 2>&1

if not exist "%ROOT%\frontend\dist\index.html" (
  echo [WARN] frontend\dist missing. UI may be API-only.
)

echo [4/4] Starting server...
echo.
echo   Open:  http://127.0.0.1:8000
echo   Docs:  http://127.0.0.1:8000/docs
echo   API Key: fill in Settings page (not bundled)
echo.
echo   Close this window to stop.
echo ========================================
echo.

cd /d "%ROOT%\backend"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Stopped.
pause
endlocal
