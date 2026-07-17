@echo off
setlocal
cd /d "%~dp0.."
set "BACKEND=%cd%\backend"
set "PY=%BACKEND%\.venv\Scripts\python.exe"

echo ========================================
echo  FusionBid Login Init
echo ========================================
echo.
echo 1. A browser window will open (Chrome preferred).
echo 2. Login to a tender portal you can access.
echo 3. If chinabidding is blocked, type another site URL.
echo 4. After login, press Enter in this window to save.
echo 5. Or wait ~10 minutes for auto-save.
echo.

if not exist "%PY%" (
  echo [ERROR] Python venv not found:
  echo   %PY%
  echo Create it first:
  echo   cd backend
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -e ".[dev]"
  echo   .venv\Scripts\python -m playwright install chromium
  pause
  exit /b 1
)

cd /d "%BACKEND%"

"%PY%" -c "import playwright" 1>nul 2>nul
if errorlevel 1 (
  echo Installing playwright...
  "%PY%" -m pip install -q playwright
  "%PY%" -m playwright install chromium
)

echo Starting login browser...
"%PY%" -m app.tools.login_init --wait 600
set ERR=%ERRORLEVEL%
echo.
echo Exit code: %ERR%
if exist "%cd%\..\data\browser_states\login_portal_state.json" (
  echo OK: state file saved.
) else (
  echo WARN: state file not found. Login may not be saved.
)
echo.
pause
exit /b %ERR%
